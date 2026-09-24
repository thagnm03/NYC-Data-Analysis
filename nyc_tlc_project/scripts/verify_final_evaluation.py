"""Independently verify the Step 8 final panel, predictions, metrics, and frozen decision."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from build_final_test import DEFAULT_FINAL_CONFIG, load_final_config
from build_modeling_frame import feature_names, load_modeling_config, write_json
from run_models import assign_fold_segments, calculate_metrics


REQUIRED_TABLES = {
    "development_vs_final_metrics.csv",
    "final_calibration_deciles.csv",
    "final_daily_error.csv",
    "final_metrics.csv",
    "final_segment_metrics.csv",
    "final_worst_days.csv",
    "final_worst_zone_blocks.csv",
    "final_zone_error.csv",
}
REQUIRED_JSON = {"final_evaluation_summary.json", "frozen_decision.json"}
REQUIRED_FIGURES = {
    "final_activity_tier_mae.png",
    "final_calibration_deciles.png",
    "final_daily_error.png",
    "final_mae_comparison.png",
    "final_monthly_mae.png",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


def values_match(left: pd.Series, right: pd.Series) -> bool:
    return bool(np.allclose(left.to_numpy(dtype="float64"), right.to_numpy(dtype="float64"), equal_nan=True))


def close(left: float, right: float, tolerance: float = 1e-8) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    return bool(np.isclose(float(left), float(right), rtol=tolerance, atol=tolerance))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_FINAL_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_final_config(args.config.resolve())
    modeling_config = load_modeling_config(config["modeling_config_path"])
    output_dir: Path = config["evaluation_output_dir"]
    figures = output_dir / "figures"
    checks: list[dict[str, Any]] = []

    missing_tables = sorted(name for name in REQUIRED_TABLES if not (output_dir / name).is_file())
    missing_json = sorted(name for name in REQUIRED_JSON if not (output_dir / name).is_file())
    missing_figures = sorted(name for name in REQUIRED_FIGURES if not (figures / name).is_file())
    prediction_path = output_dir / "final_predictions.parquet"
    checks.append(check("required_tables_exist", not missing_tables, f"missing={missing_tables}"))
    checks.append(check("required_json_exists", not missing_json, f"missing={missing_json}"))
    checks.append(check("required_figures_exist", not missing_figures, f"missing={missing_figures}"))
    checks.append(check("final_predictions_exist", prediction_path.is_file(), str(prediction_path)))
    if missing_tables or missing_json or missing_figures or not prediction_path.is_file():
        report = {"verified_at_utc": datetime.now(timezone.utc).isoformat(), "success": False, "checks": checks}
        write_json(output_dir / "final_evaluation_verification.json", report)
        raise RuntimeError("Missing Step 8 outputs")

    build_audit = json.loads((config["build_output_dir"] / "final_build_audit.json").read_text(encoding="utf-8"))
    summary = json.loads((output_dir / "final_evaluation_summary.json").read_text(encoding="utf-8"))
    decision = json.loads((output_dir / "frozen_decision.json").read_text(encoding="utf-8"))
    selection = json.loads(config["model_selection_path"].read_text(encoding="utf-8"))
    checks.append(check("final_build_audit_passed", build_audit.get("success") is True, f"checks={build_audit.get('checks_passed')}/{build_audit.get('checks_total')}"))
    checks.append(check("modeling_hash_frozen", sha256(config["modeling_config_path"]) == config["expected_modeling_config_sha256"], sha256(config["modeling_config_path"])))
    checks.append(check("training_hash_frozen", sha256(config["training_config_path"]) == config["expected_training_config_sha256"], sha256(config["training_config_path"])))
    checks.append(check("selection_hash_frozen", sha256(config["model_selection_path"]) == config["expected_model_selection_sha256"], sha256(config["model_selection_path"])))
    checks.append(check("formal_selection_unchanged", selection["selected_model"] == decision["formal_selected_method_before_final_test"] == decision["formal_selected_method_after_final_test"] == config["formal_selected_method"] and decision["selection_changed"] is False, config["formal_selected_method"]))
    checks.append(check("final_not_used_for_reselection", decision["final_test_not_used_for_reselection"] is True, "frozen decision flag"))
    checks.append(check("ridge_alpha_frozen", float(summary["frozen_ridge_alpha"]) == float(config["frozen_ridge_alpha"]) == float(selection["selected_ridge_alpha"]), str(summary["frozen_ridge_alpha"])))

    development_panel = pd.read_parquet(modeling_config["panel_path"])
    final_panel = pd.read_parquet(config["final_panel_path"])
    final_frame = pd.read_parquet(config["final_modeling_frame_path"])
    predictions = pd.read_parquet(prediction_path)
    for frame in (development_panel, final_panel, final_frame, predictions):
        frame["block_start"] = pd.to_datetime(frame["block_start"])
    final_start = pd.Timestamp(config["final_test_start"])
    final_end = pd.Timestamp(config["final_test_end_exclusive"])
    target = modeling_config["target"]
    observed_start = pd.Timestamp(final_frame["block_start"].min())
    observed_end = pd.Timestamp(final_frame["block_start"].max())
    expected_end = pd.Timestamp(final_end.to_pydatetime() - timedelta(hours=int(modeling_config["block_hours"])))
    period_exact = observed_start.value == final_start.value and observed_end.value == expected_end.value
    checks.append(check("final_period_exact", period_exact, f"{observed_start} to {observed_end}"))
    checks.append(check("final_panel_balanced", len(final_panel) == 261 * 906 and final_panel["zone_id"].nunique() == 261 and final_panel["block_start"].nunique() == 906, f"rows={len(final_panel):,}"))
    checks.append(check("final_frame_matches_panel_keys", final_frame[["zone_id", "block_start", target]].equals(final_panel[["zone_id", "block_start", target]]), f"rows={len(final_frame):,}"))
    checks.append(check("final_features_ready", bool(final_frame["features_ready"].all()), "all final rows have complete history"))

    combined = pd.concat([development_panel, final_panel], ignore_index=True).sort_values(["zone_id", "block_start"]).reset_index(drop=True)
    combined["block_start"] = pd.to_datetime(combined["block_start"])
    grouped = combined.groupby("zone_id", sort=False)[target]
    history_errors: list[str] = []
    final_mask = combined["block_start"].between(final_start, final_end, inclusive="left")
    for feature in modeling_config["history_features"]:
        operation = feature["operation"]
        if operation == "lag":
            expected = grouped.shift(int(feature["lag_blocks"]))
        elif operation == "mean_of_lags":
            expected = pd.concat([grouped.shift(int(lag)) for lag in feature["lag_blocks"]], axis=1).mean(axis=1)
        elif operation == "shifted_rolling":
            shifted = grouped.shift(int(feature["shift_blocks"]))
            rolling = shifted.groupby(combined["zone_id"], sort=False).rolling(int(feature["window_blocks"]), min_periods=int(feature["window_blocks"]))
            if feature["statistic"] == "mean":
                expected = rolling.mean().reset_index(level=0, drop=True).sort_index()
            else:
                expected = rolling.std(ddof=0).reset_index(level=0, drop=True).sort_index()
        else:
            raise ValueError(operation)
        expected_final = expected.loc[final_mask].reset_index(drop=True)
        if not values_match(final_frame[feature["name"]].reset_index(drop=True), expected_final):
            history_errors.append(feature["name"])
    checks.append(check("final_history_features_recomputed", not history_errors, f"errors={history_errors}"))

    expected_keys = final_frame[["zone_id", "block_start", target]].sort_values(["zone_id", "block_start"]).reset_index(drop=True)
    actual_keys = predictions[["zone_id", "block_start", target]].sort_values(["zone_id", "block_start"]).reset_index(drop=True)
    checks.append(check("prediction_rows_match_final_frame", expected_keys.equals(actual_keys), f"rows={len(predictions):,}"))
    checks.append(check("prediction_key_unique", not predictions.duplicated(["zone_id", "block_start"]).any(), "zone-block unique"))
    prediction_columns = [column for column in predictions.columns if column.startswith("pred__")]
    checks.append(check("predictions_complete_and_nonnegative", not predictions[prediction_columns].isna().any().any() and bool((predictions[prediction_columns] >= 0).all().all()), f"methods={len(prediction_columns)}"))

    merged = predictions.merge(final_frame[["zone_id", "block_start", target, "lag_1", "lag_6", "lag_42", "seasonal_mean_4w"]], on=["zone_id", "block_start", target], how="left", validate="one_to_one")
    baseline_sources = {
        "baseline_previous_block": "lag_1",
        "baseline_previous_day": "lag_6",
        "baseline_previous_week": "lag_42",
        "baseline_seasonal_mean_4w": "seasonal_mean_4w",
    }
    for method, source in baseline_sources.items():
        checks.append(check(f"final_baseline_{method}", bool(np.allclose(merged[f"pred__{method}"], merged[source], rtol=0, atol=0)), f"equals {source}"))

    development_frame = pd.read_parquet(modeling_config["modeling_frame_path"])
    development_frame["block_start"] = pd.to_datetime(development_frame["block_start"])
    expected_segments = assign_fold_segments(development_frame[development_frame["features_ready"]], final_frame, target)
    segment_errors = []
    for column in ["activity_tier", "peak_period", "day_type", "target_state"]:
        expected = expected_segments[["zone_id", "block_start", column]]
        actual = predictions[["zone_id", "block_start", column]]
        compare = actual.merge(expected, on=["zone_id", "block_start"], suffixes=("_actual", "_expected"), validate="one_to_one")
        if not (compare[f"{column}_actual"] == compare[f"{column}_expected"]).all():
            segment_errors.append(column)
    checks.append(check("segments_defined_from_development_only", not segment_errors, f"errors={segment_errors}"))

    metrics = pd.read_csv(output_dir / "final_metrics.csv")
    metric_errors = []
    for _, row in metrics.iterrows():
        recomputed = calculate_metrics(predictions[target].to_numpy(), predictions[f"pred__{row['method']}"].to_numpy())
        for metric in ["MAE", "MSE", "RMSE", "WAPE_pct", "R2", "mean_bias"]:
            if not close(row[metric], recomputed[metric]):
                metric_errors.append(f"{row['method']}/{metric}")
    checks.append(check("final_metrics_recompute_exactly", not metric_errors, f"errors={metric_errors}"))
    empirical_best = str(metrics.sort_values("MAE").iloc[0]["method"])
    checks.append(check("empirical_rank_is_descriptive_only", empirical_best == decision["empirical_lowest_final_MAE_method_descriptive_only"] and decision["selection_changed"] is False, empirical_best))

    formal = config["formal_selected_method"]
    expected_error = predictions[f"pred__{formal}"] - predictions[target]
    checks.append(check("formal_error_columns_exact", values_match(predictions["formal_error"], expected_error) and values_match(predictions["formal_absolute_error"], expected_error.abs()), formal))
    checks.append(check("summary_row_count_and_target", int(summary["test_rows"]) == len(predictions) and int(summary["test_target_sum"]) == int(predictions[target].sum()), f"rows={len(predictions):,}; target={int(predictions[target].sum()):,}"))

    small_figures = sorted(path.name for path in figures.glob("*.png") if path.stat().st_size < 10_000)
    checks.append(check("figures_have_nontrivial_size", not small_figures, f"small={small_figures}"))
    failed = [item for item in checks if not item["passed"]]
    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": not failed,
        "scope": "frozen_final_test_2026_01_through_2026_05",
        "test_rows": int(len(predictions)),
        "test_target_sum": int(predictions[target].sum()),
        "formal_selected_method": formal,
        "selection_changed": False,
        "checks_passed": len(checks) - len(failed),
        "checks_total": len(checks),
        "errors": [item["check"] for item in failed],
        "checks": checks,
    }
    write_json(output_dir / "final_evaluation_verification.json", report)
    print(json.dumps(report, indent=2))
    if failed:
        raise RuntimeError("Final evaluation verification failed: " + "; ".join(report["errors"]))


if __name__ == "__main__":
    main()
