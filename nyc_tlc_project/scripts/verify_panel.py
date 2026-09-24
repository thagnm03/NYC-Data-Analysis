"""Run integrity checks for the development zone x 4-hour panel."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from audit_data import DEFAULT_AUDIT_CONFIG, load_audit_config, quote_path, write_json
from build_panel import DEFAULT_PANEL_CONFIG, load_panel_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-config", type=Path, default=DEFAULT_AUDIT_CONFIG)
    parser.add_argument("--panel-config", type=Path, default=DEFAULT_PANEL_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    audit_config = load_audit_config(args.audit_config.resolve())
    panel_config = load_panel_config(args.panel_config.resolve())
    output_dir: Path = panel_config["output_dir"]
    panel_path = panel_config["processed_dir"] / panel_config["panel_filename"]
    audit_verification_path = audit_config["output_dir"] / "audit_verification.json"
    if not panel_path.is_file():
        raise FileNotFoundError(f"Missing panel: {panel_path}")
    if not audit_verification_path.is_file():
        raise FileNotFoundError(f"Missing Step 3 verification: {audit_verification_path}")
    audit_verification = json.loads(audit_verification_path.read_text(encoding="utf-8"))
    build_summary = json.loads((output_dir / "panel_build_summary.json").read_text(encoding="utf-8"))

    connection = duckdb.connect()
    connection.execute(f"CREATE VIEW panel AS SELECT * FROM read_parquet({quote_path(panel_path)})")
    stats = connection.execute(
        f"""
        SELECT
          count(*)::BIGINT AS row_count,
          count(DISTINCT (zone_id, block_start))::BIGINT AS distinct_keys,
          count(DISTINCT zone_id)::INTEGER AS zones,
          count(DISTINCT block_start)::BIGINT AS blocks,
          min(block_start) AS min_block,
          max(block_start) AS max_block,
          sum(trips)::BIGINT AS target_sum,
          count_if(zone_id IS NULL OR block_start IS NULL OR trips IS NULL)::BIGINT AS key_or_target_null,
          count_if(trips < 0)::BIGINT AS negative_target,
          count_if(extract(minute FROM block_start) <> 0 OR extract(second FROM block_start) <> 0 OR extract(hour FROM block_start) % {int(panel_config['block_hours'])} <> 0)::BIGINT AS misaligned_blocks,
          count_if(block_start >= TIMESTAMP '{panel_config['development_end_exclusive']}')::BIGINT AS final_test_rows
        FROM panel
        """
    ).df().iloc[0].to_dict()
    daily = connection.execute(
        """
        SELECT min(blocks_per_day)::INTEGER AS min_blocks_per_zone_day,
               max(blocks_per_day)::INTEGER AS max_blocks_per_zone_day
        FROM (
          SELECT zone_id, CAST(block_start AS DATE) AS day, count(*) AS blocks_per_day
          FROM panel
          GROUP BY zone_id, day
        )
        """
    ).df().iloc[0].to_dict()
    gaps = connection.execute(
        f"""
        WITH ordered AS (
          SELECT zone_id, block_start,
                 lag(block_start) OVER (PARTITION BY zone_id ORDER BY block_start) AS previous_block
          FROM panel
        )
        SELECT count_if(previous_block IS NOT NULL AND block_start - previous_block <> INTERVAL '{int(panel_config['block_hours'])} hours')::BIGINT AS gap_count
        FROM ordered
        """
    ).fetchone()[0]
    type_frame = connection.execute("DESCRIBE panel").df()
    trips_type = type_frame.loc[type_frame["column_name"] == "trips", "column_type"].iloc[0]

    start = pd.Timestamp(panel_config["development_start"])
    end = pd.Timestamp(panel_config["development_end_exclusive"])
    expected_blocks = int((end - start).total_seconds() / 3600 / int(panel_config["block_hours"]))
    expected_rows = int(stats["zones"]) * expected_blocks
    expected_clean_rows = int(audit_verification["expected_clean_rows_for_panel"])
    excluded_by_universe = int(build_summary["target_rows_excluded_by_training_zone_universe"])
    expected_panel_target = expected_clean_rows - excluded_by_universe
    expected_daily_blocks = 24 // int(panel_config["block_hours"])

    checks = {
        "unique_zone_block_key": int(stats["row_count"]) == int(stats["distinct_keys"]),
        "row_count_matches_zone_grid": int(stats["row_count"]) == expected_rows,
        "time_block_count_matches_range": int(stats["blocks"]) == expected_blocks,
        "no_key_or_target_null": int(stats["key_or_target_null"]) == 0,
        "target_nonnegative": int(stats["negative_target"]) == 0,
        "target_is_integer_type": str(trips_type).upper() in {"BIGINT", "INTEGER", "HUGEINT"},
        "blocks_aligned": int(stats["misaligned_blocks"]) == 0,
        "six_blocks_per_zone_day": int(daily["min_blocks_per_zone_day"]) == expected_daily_blocks
        and int(daily["max_blocks_per_zone_day"]) == expected_daily_blocks,
        "no_time_gaps_within_zone": int(gaps) == 0,
        "target_sum_reconciles_to_clean_audit": int(stats["target_sum"]) == expected_panel_target,
        "final_test_absent": int(stats["final_test_rows"]) == 0
        and pd.Timestamp(stats["max_block"]) < end,
        "build_reports_no_final_test_files": int(build_summary["final_test_files_used"]) == 0,
    }
    errors = [name for name, passed in checks.items() if not passed]
    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": not errors,
        "panel_file": str(panel_path),
        "panel_file_bytes": panel_path.stat().st_size,
        "row_count": int(stats["row_count"]),
        "eligible_zones": int(stats["zones"]),
        "time_blocks": int(stats["blocks"]),
        "expected_rows": expected_rows,
        "first_block": stats["min_block"],
        "last_block": stats["max_block"],
        "target_sum": int(stats["target_sum"]),
        "expected_clean_rows_from_step_3": expected_clean_rows,
        "target_rows_excluded_by_training_zone_universe": excluded_by_universe,
        "expected_panel_target_sum": expected_panel_target,
        "trips_column_type": str(trips_type),
        "min_blocks_per_zone_day": int(daily["min_blocks_per_zone_day"]),
        "max_blocks_per_zone_day": int(daily["max_blocks_per_zone_day"]),
        "gap_count": int(gaps),
        "checks": checks,
        "errors": errors,
    }
    write_json(output_dir / "panel_integrity_report.json", report)
    connection.close()
    print(json.dumps(report, indent=2, default=str))
    if errors:
        raise RuntimeError("Panel integrity failed: " + ", ".join(errors))


if __name__ == "__main__":
    main()
