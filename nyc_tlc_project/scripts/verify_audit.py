"""Verify internal consistency of Step 3 audit artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from audit_data import DEFAULT_AUDIT_CONFIG, load_audit_config, write_json
from download_data import month_range


REQUIRED_OUTPUTS = {
    "audit_run_metadata.json",
    "audit_summary.json",
    "categorical_value_counts.csv",
    "duplicate_groups_summary.csv",
    "missing_bundle_summary.csv",
    "missingness_by_month.csv",
    "missingness_overall.csv",
    "monthly_audit.csv",
    "numeric_summary.csv",
    "quality_by_vendor.csv",
    "schema_by_month.csv",
    "schema_variants.csv",
    "yearly_counts.csv",
    "zone_lookup_audit.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-config", type=Path, default=DEFAULT_AUDIT_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.audit_config.resolve()
    config = load_audit_config(config_path)
    output_dir: Path = config["output_dir"]
    errors: list[str] = []

    missing_outputs = sorted(name for name in REQUIRED_OUTPUTS if not (output_dir / name).is_file())
    if missing_outputs:
        errors.append(f"Missing outputs: {missing_outputs}")
        raise RuntimeError("; ".join(errors))

    summary = json.loads((output_dir / "audit_summary.json").read_text(encoding="utf-8"))
    monthly = pd.read_csv(output_dir / "monthly_audit.csv", dtype={"month": str})
    schema = pd.read_csv(output_dir / "schema_by_month.csv", dtype={"month": str})
    missingness = pd.read_csv(output_dir / "missingness_by_month.csv", dtype={"month": str})
    duplicate = pd.read_csv(output_dir / "duplicate_groups_summary.csv")
    zone = json.loads((output_dir / "zone_lookup_audit.json").read_text(encoding="utf-8"))

    expected_months = month_range(
        config["development_start_month"], config["development_end_month"]
    )
    observed_months = monthly["month"].tolist()
    if observed_months != expected_months:
        errors.append("monthly_audit does not contain the exact configured month sequence")
    for name, frame in {"schema": schema, "missingness": missingness}.items():
        if any(str(month).startswith("2026-") for month in frame["month"].unique()):
            errors.append(f"{name} unexpectedly contains final-test months")
    if int(monthly["rows"].sum()) != int(summary["raw_rows"]):
        errors.append("monthly raw-row sum does not match audit_summary")
    if int(monthly["target_valid_rows"].sum()) != int(summary["target_valid_rows"]):
        errors.append("monthly target-valid sum does not match audit_summary")
    target_missing = missingness[
        missingness["column"].isin(["tpep_pickup_datetime", "PULocationID"])
    ]["null_count"].sum()
    if int(target_missing) != 0:
        errors.append("target-defining fields contain nulls")
    if int(duplicate["exact_duplicate_rows"].sum()) != int(summary["verified_exact_duplicate_rows"]):
        errors.append("duplicate total does not match audit_summary")
    if int(duplicate["target_valid_exact_duplicate_rows"].sum()) != int(
        summary["target_valid_exact_duplicate_rows"]
    ):
        errors.append("target-valid duplicate total does not match audit_summary")
    if zone["location_id_duplicates"] != 0 or zone["location_id_null"] != 0:
        errors.append("zone lookup has duplicate or null LocationID")

    cleaned_rows = int(summary["target_valid_rows"]) - int(
        summary["target_valid_exact_duplicate_rows"]
    )
    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": not errors,
        "development_months": len(expected_months),
        "raw_rows": int(summary["raw_rows"]),
        "target_valid_before_dedup": int(summary["target_valid_rows"]),
        "target_valid_exact_duplicates": int(summary["target_valid_exact_duplicate_rows"]),
        "expected_clean_rows_for_panel": cleaned_rows,
        "final_test_content_absent": not any(
            str(month).startswith("2026-")
            for frame in (monthly, schema, missingness)
            for month in frame["month"].unique()
        ),
        "errors": errors,
    }
    write_json(output_dir / "audit_verification.json", report)
    print(json.dumps(report, indent=2))
    if errors:
        raise RuntimeError("Audit verification failed: " + "; ".join(errors))


if __name__ == "__main__":
    main()
