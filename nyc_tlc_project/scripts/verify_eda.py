"""Verify Step 5 EDA scope, outputs, and table consistency."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from run_eda import DEFAULT_EDA_CONFIG, load_eda_config, write_json


REQUIRED_TABLES = {
    "activity_tier_profile.csv",
    "activity_tiers.csv",
    "borough_summary.csv",
    "daily_demand.csv",
    "lag_correlations.csv",
    "largest_daily_changes_vs_previous_week.csv",
    "monthly_demand.csv",
    "time_of_week_profile.csv",
    "yearly_demand.csv",
    "zone_summary.csv",
}

REQUIRED_FIGURES = {
    "activity_tier_zero_rate.png",
    "daily_demand.png",
    "lag_correlations.png",
    "monthly_daily_average.png",
    "time_of_week_heatmaps.png",
    "top20_zone_share.png",
    "zone_concentration.png",
    "zone_year_comparison.png",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_EDA_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_eda_config(args.config.resolve())
    output_dir: Path = config["output_dir"]
    figures = output_dir / "figures"
    errors: list[str] = []

    missing_tables = sorted(name for name in REQUIRED_TABLES if not (output_dir / name).is_file())
    missing_figures = sorted(name for name in REQUIRED_FIGURES if not (figures / name).is_file())
    if missing_tables:
        errors.append(f"missing tables: {missing_tables}")
    if missing_figures:
        errors.append(f"missing figures: {missing_figures}")
    if errors:
        raise RuntimeError("; ".join(errors))

    summary = json.loads((output_dir / "eda_summary.json").read_text(encoding="utf-8"))
    daily = pd.read_csv(output_dir / "daily_demand.csv", parse_dates=["date"])
    monthly = pd.read_csv(output_dir / "monthly_demand.csv", dtype={"month": str})
    tiers = pd.read_csv(output_dir / "activity_tiers.csv")
    lags = pd.read_csv(output_dir / "lag_correlations.csv")
    zone = pd.read_csv(output_dir / "zone_summary.csv")

    if daily["date"].max() >= pd.Timestamp(config["development_end_exclusive"]):
        errors.append("daily table contains final-test dates")
    if any(month.startswith("2026-") for month in monthly["month"]):
        errors.append("monthly table contains final-test months")
    if int(monthly["trips"].sum()) != int(summary["target_sum"]):
        errors.append("monthly demand does not reconcile to EDA target sum")
    if int(zone["trips"].sum()) != int(summary["target_sum"]):
        errors.append("zone demand does not reconcile to EDA target sum")
    tier_counts = tiers["activity_tier"].value_counts()
    if set(tier_counts.index) != set(config["activity_tier_labels"]) or tier_counts.nunique() != 1:
        errors.append("activity tiers are missing or unequal in zone count")
    if set(lags["lag"]) != set(config["lags_in_blocks"]):
        errors.append("lag table does not match configured lags")
    numeric_correlation_columns = [
        "pooled_raw_correlation",
        "pooled_within_zone_centered_correlation",
        "zone_correlation_p25",
        "zone_correlation_median",
        "zone_correlation_p75",
    ]
    if lags[numeric_correlation_columns].isna().any().any():
        errors.append("lag summary contains null correlations")
    too_small = sorted(path.name for path in figures.glob("*.png") if path.stat().st_size < 10_000)
    if too_small:
        errors.append(f"figures unexpectedly small: {too_small}")

    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": not errors,
        "development_only": daily["date"].max() < pd.Timestamp(config["development_end_exclusive"]),
        "final_test_rows_used": int(summary["final_test_rows_used"]),
        "table_count": len(REQUIRED_TABLES),
        "figure_count": len(REQUIRED_FIGURES),
        "target_sum": int(summary["target_sum"]),
        "zones": int(summary["zones"]),
        "tier_counts": {str(key): int(value) for key, value in tier_counts.items()},
        "errors": errors,
    }
    write_json(output_dir / "eda_verification.json", report)
    print(json.dumps(report, indent=2))
    if errors:
        raise RuntimeError("EDA verification failed: " + "; ".join(errors))


if __name__ == "__main__":
    main()
