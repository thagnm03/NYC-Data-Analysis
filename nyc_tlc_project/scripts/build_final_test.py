"""Open Step 8 and build the fixed-universe 2026 final-test panel and feature frame."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from audit_data import quote_path
from build_modeling_frame import build_history_features, feature_names, load_modeling_config, write_json
from build_panel import load_panel_config
from download_data import load_config, month_range


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FINAL_CONFIG = PROJECT_ROOT / "config" / "final_evaluation_config.json"


def resolve_from_config(config_path: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def load_final_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    path_keys = {
        "modeling_config_path",
        "training_config_path",
        "model_selection_path",
        "project_config_path",
        "panel_config_path",
        "zone_eligibility_path",
        "final_panel_path",
        "final_modeling_frame_path",
        "build_output_dir",
        "evaluation_output_dir",
    }
    required = path_keys | {
        "expected_modeling_config_sha256",
        "expected_training_config_sha256",
        "expected_model_selection_sha256",
        "final_test_start_month",
        "final_test_end_month",
        "final_test_start",
        "final_test_end_exclusive",
        "formal_selected_method",
        "secondary_prespecified_benchmark",
        "diagnostic_regression",
        "frozen_ridge_alpha",
        "figure_dpi",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise KeyError(f"Final-evaluation config is missing keys: {missing}")
    for key in path_keys:
        config[key] = resolve_from_config(path, config[key])
    return config


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_record(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_FINAL_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    final_config_path = args.config.resolve()
    config = load_final_config(final_config_path)
    modeling_hash = sha256(config["modeling_config_path"])
    training_hash = sha256(config["training_config_path"])
    selection_hash = sha256(config["model_selection_path"])
    if modeling_hash != config["expected_modeling_config_sha256"]:
        raise RuntimeError("Modeling config changed after Step 6")
    if training_hash != config["expected_training_config_sha256"]:
        raise RuntimeError("Training config changed after Step 7")
    if selection_hash != config["expected_model_selection_sha256"]:
        raise RuntimeError("Model selection changed after Step 7")

    selection = json.loads(config["model_selection_path"].read_text(encoding="utf-8"))
    if selection["selected_model"] != config["formal_selected_method"]:
        raise RuntimeError("Formal selected method does not match frozen Step 7 decision")
    if float(selection["selected_ridge_alpha"]) != float(config["frozen_ridge_alpha"]):
        raise RuntimeError("Frozen Ridge alpha does not match Step 7")
    if selection["polynomial_executed"]:
        raise RuntimeError("Unexpected polynomial state in frozen selection")

    project_config = load_config(config["project_config_path"])
    panel_config = load_panel_config(config["panel_config_path"])
    modeling_config = load_modeling_config(config["modeling_config_path"])
    final_months = month_range(config["final_test_start_month"], config["final_test_end_month"])
    raw_files = [(month, project_config.raw_dir / f"yellow_tripdata_{month}.parquet") for month in final_months]
    missing_files = [str(path) for _, path in raw_files if not path.is_file()]
    if missing_files:
        raise FileNotFoundError(f"Missing frozen final-test raw files: {missing_files}")
    if not config["zone_eligibility_path"].is_file():
        raise FileNotFoundError(f"Missing development-defined zone universe: {config['zone_eligibility_path']}")

    output_dir: Path = config["build_output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    config["final_panel_path"].parent.mkdir(parents=True, exist_ok=True)
    temp_dir: Path = panel_config["interim_dir"] / "duckdb_final_test_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    connection.execute(f"SET threads={int(panel_config['duckdb_threads'])}")
    connection.execute(f"SET memory_limit='{panel_config['duckdb_memory_limit']}'")
    connection.execute(f"SET temp_directory={quote_path(temp_dir)}")
    connection.execute("CREATE TABLE demand (zone_id INTEGER, block_start TIMESTAMP, trips BIGINT)")

    zone_min = int(panel_config["valid_pickup_zone_min"])
    zone_max = int(panel_config["valid_pickup_zone_max"])
    block_hours = int(panel_config["block_hours"])
    monthly_audit: list[dict[str, Any]] = []
    for index, (month, path) in enumerate(raw_files, start=1):
        year, number = map(int, month.split("-"))
        next_index = year * 12 + number
        month_end = f"{next_index // 12:04d}-{next_index % 12 + 1:02d}-01"
        raw_rows = int(pq.ParquetFile(path).metadata.num_rows)
        connection.execute(
            f"CREATE OR REPLACE TEMP TABLE month_deduplicated AS SELECT DISTINCT * FROM read_parquet({quote_path(path)})"
        )
        deduplicated_rows = int(connection.execute("SELECT count(*) FROM month_deduplicated").fetchone()[0])
        valid_rows = int(
            connection.execute(
                f"""
                SELECT count(*)
                FROM month_deduplicated
                WHERE tpep_pickup_datetime >= TIMESTAMP '{month}-01'
                  AND tpep_pickup_datetime < TIMESTAMP '{month_end}'
                  AND PULocationID BETWEEN {zone_min} AND {zone_max}
                """
            ).fetchone()[0]
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE month_demand AS
            SELECT
              CAST(PULocationID AS INTEGER) AS zone_id,
              time_bucket(INTERVAL '{block_hours} hours', tpep_pickup_datetime) AS block_start,
              count(*)::BIGINT AS trips
            FROM month_deduplicated
            WHERE tpep_pickup_datetime >= TIMESTAMP '{month}-01'
              AND tpep_pickup_datetime < TIMESTAMP '{month_end}'
              AND PULocationID BETWEEN {zone_min} AND {zone_max}
            GROUP BY 1, 2
            """
        )
        demand_stats = connection.execute(
            "SELECT count(*)::BIGINT, count(DISTINCT zone_id)::INTEGER, coalesce(sum(trips), 0)::BIGINT FROM month_demand"
        ).fetchone()
        connection.execute("INSERT INTO demand SELECT * FROM month_demand")
        monthly_audit.append(
            {
                "month": month,
                "raw_rows": raw_rows,
                "deduplicated_rows": deduplicated_rows,
                "exact_duplicate_rows_removed": raw_rows - deduplicated_rows,
                "clean_target_rows_all_valid_zones": valid_rows,
                "nonzero_zone_blocks": int(demand_stats[0]),
                "observed_zones": int(demand_stats[1]),
                "aggregated_target_sum": int(demand_stats[2]),
            }
        )
        print(
            f"opened {month} ({index}/{len(raw_files)}): raw={raw_rows:,}, "
            f"clean_target={valid_rows:,}, duplicates_removed={raw_rows - deduplicated_rows:,}",
            flush=True,
        )

    monthly_audit_frame = pd.DataFrame(monthly_audit)
    monthly_audit_frame.to_csv(output_dir / "final_raw_monthly_audit.csv", index=False)
    connection.execute(
        f"CREATE TABLE eligible_zones AS SELECT * FROM read_csv_auto({quote_path(config['zone_eligibility_path'])}, header=true) WHERE eligible = true"
    )
    eligible_count = int(connection.execute("SELECT count(*) FROM eligible_zones").fetchone()[0])
    excluded_target_sum = int(
        connection.execute(
            "SELECT coalesce(sum(d.trips), 0)::BIGINT FROM demand d ANTI JOIN eligible_zones e ON d.zone_id = CAST(e.zone_id AS INTEGER)"
        ).fetchone()[0]
    )
    final_start = pd.Timestamp(config["final_test_start"])
    final_end = pd.Timestamp(config["final_test_end_exclusive"])
    connection.execute(
        f"""
        CREATE TABLE final_panel AS
        SELECT
          CAST(e.zone_id AS INTEGER) AS zone_id,
          e.borough,
          e.zone_name,
          e.service_zone,
          grid.block_start,
          coalesce(d.trips, 0)::BIGINT AS trips
        FROM eligible_zones e
        CROSS JOIN generate_series(
          TIMESTAMP '{config['final_test_start']}',
          TIMESTAMP '{config['final_test_end_exclusive']}' - INTERVAL '{block_hours} hours',
          INTERVAL '{block_hours} hours'
        ) AS grid(block_start)
        LEFT JOIN demand d ON d.zone_id = CAST(e.zone_id AS INTEGER) AND d.block_start = grid.block_start
        ORDER BY zone_id, block_start
        """
    )
    if config["final_panel_path"].exists():
        config["final_panel_path"].unlink()
    connection.execute(
        f"COPY final_panel TO {quote_path(config['final_panel_path'])} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
    )
    panel_stats = connection.execute(
        """
        SELECT count(*)::BIGINT, count(DISTINCT zone_id)::INTEGER, count(DISTINCT block_start)::INTEGER,
               min(block_start), max(block_start), sum(trips)::BIGINT, count_if(trips = 0)::BIGINT
        FROM final_panel
        """
    ).fetchone()
    monthly_panel = connection.execute(
        """
        SELECT strftime(block_start, '%Y-%m') AS month, count(*)::BIGINT AS panel_rows,
               sum(trips)::BIGINT AS trips, count_if(trips = 0)::BIGINT AS zero_cells,
               100.0 * count_if(trips = 0) / count(*) AS zero_cell_pct
        FROM final_panel GROUP BY month ORDER BY month
        """
    ).df()
    monthly_panel.to_csv(output_dir / "final_panel_monthly_summary.csv", index=False)
    connection.close()

    development_panel = pd.read_parquet(modeling_config["panel_path"])
    final_panel = pd.read_parquet(config["final_panel_path"])
    combined = pd.concat([development_panel, final_panel], ignore_index=True).sort_values(
        ["zone_id", "block_start"]
    ).reset_index(drop=True)
    combined["block_start"] = pd.to_datetime(combined["block_start"])
    combined["month_of_year"] = combined["block_start"].dt.month.astype("int8")
    combined["day_of_week"] = combined["block_start"].dt.dayofweek.astype("int8")
    combined["block_hour"] = combined["block_start"].dt.hour.astype("int8")
    combined["is_holiday"] = combined["block_start"].dt.normalize().isin(
        pd.to_datetime(modeling_config["holiday_dates"])
    ).astype("int8")
    development_start = pd.Timestamp(modeling_config["development_start"])
    combined["time_index_days"] = (
        (combined["block_start"] - development_start).dt.total_seconds() / 86_400
    ).astype("float64")
    combined = build_history_features(combined, modeling_config)
    calendar_names, history_names = feature_names(modeling_config)
    combined["features_ready"] = combined[history_names].notna().all(axis=1)
    final_frame = combined[
        combined["block_start"].between(final_start, final_end, inclusive="left")
    ].copy()
    ordered_columns = [
        "zone_id", "borough", "zone_name", "service_zone", "block_start", modeling_config["target"],
        *calendar_names, *history_names, "features_ready",
    ]
    temporary = config["final_modeling_frame_path"].with_suffix(".parquet.tmp")
    final_frame[ordered_columns].to_parquet(temporary, index=False, engine="pyarrow", compression="zstd")
    temporary.replace(config["final_modeling_frame_path"])

    expected_blocks = int((final_end - final_start).total_seconds() / (block_hours * 3600))
    audit = [
        check_record("frozen_modeling_config_hash", modeling_hash == config["expected_modeling_config_sha256"], modeling_hash),
        check_record("frozen_training_config_hash", training_hash == config["expected_training_config_sha256"], training_hash),
        check_record("frozen_selection_hash", selection_hash == config["expected_model_selection_sha256"], selection_hash),
        check_record("five_expected_raw_months", len(raw_files) == 5, str(final_months)),
        check_record("zone_universe_fixed_at_261", eligible_count == 261, f"zones={eligible_count}"),
        check_record("balanced_panel_row_count", int(panel_stats[0]) == eligible_count * expected_blocks, f"rows={int(panel_stats[0]):,}"),
        check_record("balanced_panel_block_count", int(panel_stats[2]) == expected_blocks, f"blocks={int(panel_stats[2])}"),
        check_record("final_start_exact", pd.Timestamp(panel_stats[3]) == final_start, str(panel_stats[3])),
        check_record("final_end_exact", pd.Timestamp(panel_stats[4]) == final_end - pd.Timedelta(hours=block_hours), str(panel_stats[4])),
        check_record("panel_target_reconciles", int(panel_stats[5]) + excluded_target_sum == int(monthly_audit_frame["clean_target_rows_all_valid_zones"].sum()), f"panel={int(panel_stats[5]):,}; excluded={excluded_target_sum:,}"),
        check_record("final_features_complete", bool(final_frame["features_ready"].all()), f"rows={len(final_frame):,}"),
        check_record("final_frame_matches_panel_rows", len(final_frame) == int(panel_stats[0]), f"rows={len(final_frame):,}"),
        check_record("final_target_nonnegative", bool((final_frame[modeling_config["target"]] >= 0).all()), f"minimum={final_frame[modeling_config['target']].min()}"),
    ]
    failed = [item for item in audit if not item["passed"]]
    audit_payload = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": not failed,
        "scope": "final_test_2026_01_through_2026_05",
        "checks_passed": len(audit) - len(failed),
        "checks_total": len(audit),
        "checks": audit,
    }
    write_json(output_dir / "final_build_audit.json", audit_payload)
    if failed:
        raise RuntimeError("Final-test build audit failed: " + "; ".join(item["check"] for item in failed))

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "final_test_2026_01_through_2026_05",
        "raw_files_used": [str(path) for _, path in raw_files],
        "raw_rows": int(monthly_audit_frame["raw_rows"].sum()),
        "exact_duplicate_rows_removed": int(monthly_audit_frame["exact_duplicate_rows_removed"].sum()),
        "clean_target_rows_all_valid_zones": int(monthly_audit_frame["clean_target_rows_all_valid_zones"].sum()),
        "target_rows_excluded_by_frozen_zone_universe": excluded_target_sum,
        "panel_target_sum": int(panel_stats[5]),
        "panel_rows": int(panel_stats[0]),
        "zones": int(panel_stats[1]),
        "time_blocks": int(panel_stats[2]),
        "zero_cells": int(panel_stats[6]),
        "zero_cell_pct": 100 * int(panel_stats[6]) / int(panel_stats[0]),
        "first_block": panel_stats[3],
        "last_block": panel_stats[4],
        "final_panel_file": str(config["final_panel_path"]),
        "final_modeling_frame_file": str(config["final_modeling_frame_path"]),
        "final_modeling_frame_bytes": config["final_modeling_frame_path"].stat().st_size,
        "modeling_config_sha256": modeling_hash,
        "training_config_sha256": training_hash,
        "model_selection_sha256": selection_hash,
        "formal_selected_method": selection["selected_model"],
        "frozen_ridge_alpha": selection["selected_ridge_alpha"],
    }
    write_json(output_dir / "final_build_summary.json", summary)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
