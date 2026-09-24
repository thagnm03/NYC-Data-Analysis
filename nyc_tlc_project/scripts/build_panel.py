"""Build the leakage-safe development panel at pickup-zone x 4-hour grain."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from audit_data import DEFAULT_AUDIT_CONFIG, load_audit_config, quote_path, write_json
from download_data import DEFAULT_CONFIG, load_config, month_range


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL_CONFIG = PROJECT_ROOT / "config" / "panel_config.json"


def resolve_from_config(config_path: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def load_panel_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "development_start",
        "development_end_exclusive",
        "eligibility_start",
        "eligibility_end_exclusive",
        "block_hours",
        "valid_pickup_zone_min",
        "valid_pickup_zone_max",
        "processed_dir",
        "output_dir",
        "interim_dir",
        "panel_filename",
        "duckdb_threads",
        "duckdb_memory_limit",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise KeyError(f"Panel config is missing keys: {missing}")
    raw["processed_dir"] = resolve_from_config(path, raw["processed_dir"])
    raw["output_dir"] = resolve_from_config(path, raw["output_dir"])
    raw["interim_dir"] = resolve_from_config(path, raw["interim_dir"])
    if int(raw["block_hours"]) <= 0 or 24 % int(raw["block_hours"]) != 0:
        raise ValueError("block_hours must be a positive divisor of 24")
    return raw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--audit-config", type=Path, default=DEFAULT_AUDIT_CONFIG)
    parser.add_argument("--panel-config", type=Path, default=DEFAULT_PANEL_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    project_config_path = args.project_config.resolve()
    audit_config_path = args.audit_config.resolve()
    panel_config_path = args.panel_config.resolve()
    project_config = load_config(project_config_path)
    audit_config = load_audit_config(audit_config_path)
    panel_config = load_panel_config(panel_config_path)

    development_months = month_range(
        audit_config["development_start_month"], audit_config["development_end_month"]
    )
    if development_months[0] != panel_config["development_start"][:7]:
        raise ValueError("Panel start does not match audit development start")
    expected_end_month = panel_config["development_end_exclusive"][:7]
    last_year, last_month = map(int, development_months[-1].split("-"))
    next_index = last_year * 12 + last_month
    if expected_end_month != f"{next_index // 12:04d}-{next_index % 12 + 1:02d}":
        raise ValueError("Panel end does not immediately follow audit development end")

    files = [
        (month, project_config.raw_dir / f"yellow_tripdata_{month}.parquet")
        for month in development_months
    ]
    missing_files = [str(path) for _, path in files if not path.is_file()]
    if missing_files:
        raise FileNotFoundError(f"Missing development files: {missing_files}")

    processed_dir: Path = panel_config["processed_dir"]
    output_dir: Path = panel_config["output_dir"]
    interim_dir: Path = panel_config["interim_dir"]
    for directory in (processed_dir, output_dir, interim_dir):
        directory.mkdir(parents=True, exist_ok=True)
    temp_dir = interim_dir / "duckdb_panel_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    panel_path = processed_dir / panel_config["panel_filename"]

    connection = duckdb.connect()
    connection.execute(f"SET threads={int(panel_config['duckdb_threads'])}")
    connection.execute(f"SET memory_limit='{panel_config['duckdb_memory_limit']}'")
    connection.execute(f"SET temp_directory={quote_path(temp_dir)}")
    connection.execute(
        """
        CREATE TABLE demand (
          zone_id INTEGER,
          block_start TIMESTAMP,
          trips BIGINT
        )
        """
    )

    zone_min = int(panel_config["valid_pickup_zone_min"])
    zone_max = int(panel_config["valid_pickup_zone_max"])
    block_hours = int(panel_config["block_hours"])
    monthly_rows: list[dict[str, Any]] = []
    for index, (month, path) in enumerate(files, start=1):
        tick = time.perf_counter()
        year, number = map(int, month.split("-"))
        next_month_index = year * 12 + number
        month_end = f"{next_month_index // 12:04d}-{next_month_index % 12 + 1:02d}-01"
        connection.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE month_demand AS
            WITH deduplicated AS (
              SELECT DISTINCT *
              FROM read_parquet({quote_path(path)})
            ),
            valid AS (
              SELECT
                CAST(PULocationID AS INTEGER) AS zone_id,
                tpep_pickup_datetime
              FROM deduplicated
              WHERE tpep_pickup_datetime >= TIMESTAMP '{month}-01'
                AND tpep_pickup_datetime < TIMESTAMP '{month_end}'
                AND PULocationID BETWEEN {zone_min} AND {zone_max}
            )
            SELECT
              zone_id,
              time_bucket(INTERVAL '{block_hours} hours', tpep_pickup_datetime) AS block_start,
              count(*)::BIGINT AS trips
            FROM valid
            GROUP BY 1, 2
            """
        )
        stats = connection.execute(
            """
            SELECT
              coalesce(sum(trips), 0)::BIGINT AS clean_trip_rows,
              count(*)::BIGINT AS nonzero_zone_blocks,
              count(DISTINCT zone_id)::INTEGER AS observed_zones,
              min(block_start) AS min_block,
              max(block_start) AS max_block
            FROM month_demand
            """
        ).df().iloc[0].to_dict()
        stats["month"] = month
        monthly_rows.append(stats)
        connection.execute("INSERT INTO demand SELECT * FROM month_demand")
        print(
            f"aggregated {month} ({index}/{len(files)}): "
            f"trips={int(stats['clean_trip_rows']):,}, "
            f"nonzero_cells={int(stats['nonzero_zone_blocks']):,}, "
            f"zones={int(stats['observed_zones'])}, "
            f"elapsed={time.perf_counter() - tick:.1f}s",
            flush=True,
        )

    monthly_clean = pd.DataFrame(monthly_rows).sort_values("month")
    monthly_clean.to_csv(output_dir / "monthly_clean_demand.csv", index=False)

    lookup_path = project_config.raw_dir / "taxi_zone_lookup.csv"
    connection.execute(
        f"CREATE TABLE zones AS SELECT * FROM read_csv_auto({quote_path(lookup_path)}, header=true)"
    )
    connection.execute(
        f"""
        CREATE TABLE eligible_zones AS
        SELECT
          d.zone_id,
          sum(d.trips)::BIGINT AS initial_training_trips
        FROM demand d
        WHERE d.block_start >= TIMESTAMP '{panel_config['eligibility_start']}'
          AND d.block_start < TIMESTAMP '{panel_config['eligibility_end_exclusive']}'
        GROUP BY d.zone_id
        HAVING sum(d.trips) > 0
        """
    )
    connection.execute(
        """
        CREATE TABLE zone_eligibility AS
        WITH development_totals AS (
          SELECT zone_id, sum(trips)::BIGINT AS development_trips
          FROM demand
          GROUP BY zone_id
        )
        SELECT
          CAST(z.LocationID AS INTEGER) AS zone_id,
          z.Borough AS borough,
          z.Zone AS zone_name,
          z.service_zone,
          coalesce(e.initial_training_trips, 0)::BIGINT AS initial_training_trips,
          coalesce(d.development_trips, 0)::BIGINT AS development_trips,
          (e.zone_id IS NOT NULL) AS eligible
        FROM zones z
        LEFT JOIN eligible_zones e ON CAST(z.LocationID AS INTEGER) = e.zone_id
        LEFT JOIN development_totals d ON CAST(z.LocationID AS INTEGER) = d.zone_id
        WHERE CAST(z.LocationID AS INTEGER) BETWEEN 1 AND 263
        ORDER BY zone_id
        """
    )
    eligibility = connection.execute("SELECT * FROM zone_eligibility ORDER BY zone_id").df()
    eligibility.to_csv(output_dir / "zone_eligibility.csv", index=False)

    excluded_demand = connection.execute(
        """
        SELECT
          d.zone_id,
          z.Borough AS borough,
          z.Zone AS zone_name,
          min(d.block_start) AS first_observed_block,
          max(d.block_start) AS last_observed_block,
          sum(d.trips)::BIGINT AS excluded_development_trips
        FROM demand d
        ANTI JOIN eligible_zones e USING (zone_id)
        LEFT JOIN zones z ON d.zone_id = CAST(z.LocationID AS INTEGER)
        GROUP BY d.zone_id, z.Borough, z.Zone
        ORDER BY excluded_development_trips DESC
        """
    ).df()
    excluded_demand.to_csv(output_dir / "ineligible_zone_demand.csv", index=False)

    connection.execute(
        f"""
        CREATE TABLE panel AS
        SELECT
          e.zone_id,
          z.Borough AS borough,
          z.Zone AS zone_name,
          z.service_zone,
          grid.block_start,
          coalesce(d.trips, 0)::BIGINT AS trips
        FROM eligible_zones e
        JOIN zones z ON e.zone_id = CAST(z.LocationID AS INTEGER)
        CROSS JOIN generate_series(
          TIMESTAMP '{panel_config['development_start']}',
          TIMESTAMP '{panel_config['development_end_exclusive']}' - INTERVAL '{block_hours} hours',
          INTERVAL '{block_hours} hours'
        ) AS grid(block_start)
        LEFT JOIN demand d USING (zone_id, block_start)
        ORDER BY zone_id, block_start
        """
    )
    if panel_path.exists():
        panel_path.unlink()
    connection.execute(
        f"COPY panel TO {quote_path(panel_path)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
    )

    panel_monthly = connection.execute(
        """
        SELECT
          strftime(block_start, '%Y-%m') AS month,
          count(*)::BIGINT AS panel_rows,
          sum(trips)::BIGINT AS trips,
          count_if(trips = 0)::BIGINT AS zero_cells,
          100.0 * count_if(trips = 0) / count(*) AS zero_cell_pct
        FROM panel
        GROUP BY month
        ORDER BY month
        """
    ).df()
    panel_monthly.to_csv(output_dir / "panel_monthly_summary.csv", index=False)
    panel_zones = connection.execute(
        """
        SELECT
          zone_id,
          any_value(borough) AS borough,
          any_value(zone_name) AS zone_name,
          count(*)::BIGINT AS panel_rows,
          sum(trips)::BIGINT AS trips,
          count_if(trips = 0)::BIGINT AS zero_cells,
          100.0 * count_if(trips = 0) / count(*) AS zero_cell_pct,
          min(block_start) AS first_block,
          max(block_start) AS last_block
        FROM panel
        GROUP BY zone_id
        ORDER BY zone_id
        """
    ).df()
    panel_zones.to_csv(output_dir / "panel_zone_summary.csv", index=False)

    panel_stats = connection.execute(
        """
        SELECT
          count(*)::BIGINT AS panel_rows,
          count(DISTINCT zone_id)::INTEGER AS eligible_zones,
          count(DISTINCT block_start)::BIGINT AS time_blocks,
          min(block_start) AS first_block,
          max(block_start) AS last_block,
          sum(trips)::BIGINT AS panel_target_sum,
          count_if(trips = 0)::BIGINT AS zero_cells
        FROM panel
        """
    ).df().iloc[0].to_dict()
    clean_target_sum = int(monthly_clean["clean_trip_rows"].sum())
    excluded_target_sum = int(excluded_demand["excluded_development_trips"].sum()) if len(excluded_demand) else 0
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "development_only",
        "development_start": panel_config["development_start"],
        "development_end_exclusive": panel_config["development_end_exclusive"],
        "eligibility_rule": "zone 1-263 with at least one clean pickup in 2024 initial training window",
        "eligibility_start": panel_config["eligibility_start"],
        "eligibility_end_exclusive": panel_config["eligibility_end_exclusive"],
        "block_hours": block_hours,
        "panel_file": str(panel_path),
        "panel_file_bytes": panel_path.stat().st_size,
        "panel_rows": int(panel_stats["panel_rows"]),
        "eligible_zones": int(panel_stats["eligible_zones"]),
        "time_blocks": int(panel_stats["time_blocks"]),
        "first_block": panel_stats["first_block"],
        "last_block": panel_stats["last_block"],
        "clean_target_rows_all_valid_zones": clean_target_sum,
        "target_rows_excluded_by_training_zone_universe": excluded_target_sum,
        "panel_target_sum": int(panel_stats["panel_target_sum"]),
        "zero_cells": int(panel_stats["zero_cells"]),
        "zero_cell_pct": 100 * int(panel_stats["zero_cells"]) / int(panel_stats["panel_rows"]),
        "elapsed_seconds": time.perf_counter() - started,
        "final_test_files_used": 0,
    }
    write_json(output_dir / "panel_build_summary.json", summary)
    write_json(
        output_dir / "panel_run_metadata.json",
        {
            "generated_at_utc": summary["generated_at_utc"],
            "project_config": str(project_config_path),
            "audit_config": str(audit_config_path),
            "panel_config": str(panel_config_path),
            "duckdb_version": duckdb.__version__,
            "pandas_version": pd.__version__,
            "input_files": [str(path) for _, path in files],
            "input_months": development_months,
        },
    )
    connection.close()
    print(
        f"panel built: rows={summary['panel_rows']:,}, zones={summary['eligible_zones']}, "
        f"blocks={summary['time_blocks']:,}, target_sum={summary['panel_target_sum']:,}, "
        f"zero_cells={summary['zero_cell_pct']:.2f}%\n"
        f"panel: {panel_path}\noutputs: {output_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
