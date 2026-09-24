"""Verify Step 7 predictions, metrics, selection rules, and final-test isolation."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from build_modeling_frame import load_modeling_config, write_json
from run_models import DEFAULT_TRAINING_CONFIG, calculate_metrics, load_training_config


REQUIRED_TABLES = {
    "fit_diagnostics.csv",
    "fold_metrics.csv",
    "mean_fold_metrics.csv",
    "mean_segment_metrics.csv",
    "model_selection_candidates.csv",
    "polynomial_residual_deciles.csv",
    "ridge_alpha_fold_metrics.csv",
    "ridge_alpha_summary.csv",
    "segment_metrics.csv",
}
REQUIRED_JSON = {
    "model_selection.json",
    "modeling_run_summary.json",
    "polynomial_gate.json",
}
REQUIRED_FIGURES = {
    "activity_tier_mae.png",
    "fold_mae_comparison.png",
    "mean_mae_comparison.png",
    "ridge_alpha_curve.png",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_TRAINING_CONFIG)
    return parser.parse_args()


def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


def close(left: float, right: float, tolerance: float = 1e-8) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    return bool(np.isclose(float(left), float(right), rtol=tolerance, atol=tolerance))


def main() -> None:
    args = parse_args()
    training_path = args.config.resolve()
    training_config = load_training_config(training_path)
    modeling_path: Path = training_config["modeling_config_path"]
    modeling_config = load_modeling_config(modeling_path)
    output_dir: Path = training_config["output_dir"]
    figures = output_dir / "figures"
    checks: list[dict[str, Any]] = []

    missing_tables = sorted(name for name in REQUIRED_TABLES if not (output_dir / name).is_file())
    missing_json = sorted(name for name in REQUIRED_JSON if not (output_dir / name).is_file())
    missing_figures = sorted(name for name in REQUIRED_FIGURES if not (figures / name).is_file())
    prediction_path = output_dir / "validation_predictions.parquet"
    checks.append(check("required_tables_exist", not missing_tables, f"missing={missing_tables}"))
    checks.append(check("required_json_exists", not missing_json, f"missing={missing_json}"))
    checks.append(check("required_figures_exist", not missing_figures, f"missing={missing_figures}"))
    checks.append(check("validation_predictions_exist", prediction_path.is_file(), str(prediction_path)))
    if missing_tables or missing_json or missing_figures or not prediction_path.is_file():
        report = {
            "verified_at_utc": datetime.now(timezone.utc).isoformat(),
            "success": False,
            "errors": [item["check"] for item in checks if not item["passed"]],
            "checks": checks,
        }
        write_json(output_dir / "modeling_verification.json", report)
        raise RuntimeError("Missing Step 7 outputs")

    config_hash = hashlib.sha256(modeling_path.read_bytes()).hexdigest()
    run_summary = json.loads((output_dir / "modeling_run_summary.json").read_text(encoding="utf-8"))
    selection = json.loads((output_dir / "model_selection.json").read_text(encoding="utf-8"))
    polynomial_gate = json.loads((output_dir / "polynomial_gate.json").read_text(encoding="utf-8"))
    predictions = pd.read_parquet(prediction_path)
    predictions["block_start"] = pd.to_datetime(predictions["block_start"])
    frame = pd.read_parquet(modeling_config["modeling_frame_path"])
    frame["block_start"] = pd.to_datetime(frame["block_start"])
    target = modeling_config["target"]
    model_names = list(run_summary["models_evaluated"])
    prediction_columns = [f"pred__{name}" for name in model_names]

    checks.append(check("modeling_config_checksum", config_hash == training_config["expected_modeling_config_sha256"] == run_summary["modeling_config_sha256"] == selection["modeling_config_sha256"], config_hash))
    checks.append(check("development_only", predictions["block_start"].max() < pd.Timestamp(modeling_config["final_test_start"]), str(predictions["block_start"].max())))
    checks.append(check("final_test_rows_used_zero", run_summary["final_test_rows_used"] == 0 and selection["final_test_rows_used"] == 0, "summary and selection"))
    checks.append(check("prediction_key_unique", not predictions.duplicated(["zone_id", "block_start"]).any(), f"rows={len(predictions):,}"))
    checks.append(check("all_prediction_columns_exist", set(prediction_columns) <= set(predictions.columns), f"missing={sorted(set(prediction_columns) - set(predictions.columns))}"))
    checks.append(check("predictions_are_complete", not predictions[prediction_columns].isna().any().any(), "no null predictions"))
    checks.append(check("predictions_are_nonnegative", bool((predictions[prediction_columns] >= 0).all().all()), "postprocessing applied"))

    expected_validation = frame[
        frame["features_ready"]
        & frame["block_start"].between(pd.Timestamp("2025-01-01"), pd.Timestamp(modeling_config["development_end_exclusive"]), inclusive="left")
    ][["zone_id", "block_start", target, "lag_1", "lag_6", "lag_42", "seasonal_mean_4w"]].copy()
    actual_keys = predictions[["zone_id", "block_start", target]].sort_values(["zone_id", "block_start"]).reset_index(drop=True)
    expected_keys = expected_validation[["zone_id", "block_start", target]].sort_values(["zone_id", "block_start"]).reset_index(drop=True)
    checks.append(check("validation_rows_match_locked_common_sample", actual_keys.equals(expected_keys), f"actual={len(actual_keys):,}; expected={len(expected_keys):,}"))
    checks.append(check("validation_rows_match_summary", len(predictions) == int(run_summary["validation_rows"]), f"rows={len(predictions):,}"))

    merged = predictions.merge(expected_validation, on=["zone_id", "block_start", target], how="left", validate="one_to_one")
    baseline_mapping = {
        "baseline_previous_block": "lag_1",
        "baseline_previous_day": "lag_6",
        "baseline_previous_week": "lag_42",
        "baseline_seasonal_mean_4w": "seasonal_mean_4w",
    }
    for model_name, source in baseline_mapping.items():
        matches = np.allclose(merged[f"pred__{model_name}"], merged[source], rtol=0, atol=0)
        checks.append(check(f"baseline_definition_{model_name}", bool(matches), f"equals {source}"))

    fold_metrics = pd.read_csv(output_dir / "fold_metrics.csv")
    metric_names = ["MAE", "MSE", "RMSE", "WAPE_pct", "R2", "mean_bias"]
    metric_errors: list[str] = []
    for (fold_name, model_name), stored_group in fold_metrics.groupby(["fold", "model"]):
        if len(stored_group) != 1:
            metric_errors.append(f"{fold_name}/{model_name}: duplicate")
            continue
        group = predictions[predictions["fold"] == fold_name]
        recomputed = calculate_metrics(group[target].to_numpy(), group[f"pred__{model_name}"].to_numpy())
        stored = stored_group.iloc[0]
        for metric in metric_names:
            if not close(stored[metric], recomputed[metric]):
                metric_errors.append(f"{fold_name}/{model_name}/{metric}")
    expected_metric_rows = len(model_names) * len(modeling_config["validation"]["folds"])
    checks.append(check("fold_metrics_recompute_exactly", not metric_errors and len(fold_metrics) == expected_metric_rows, f"errors={metric_errors}; rows={len(fold_metrics)}"))

    mean_metrics = pd.read_csv(output_dir / "mean_fold_metrics.csv")
    mean_errors: list[str] = []
    for model_name in model_names:
        stored = mean_metrics[mean_metrics["model"] == model_name]
        source = fold_metrics[fold_metrics["model"] == model_name]
        if len(stored) != 1 or len(source) != 4:
            mean_errors.append(f"{model_name}: row count")
            continue
        for metric in metric_names:
            if not close(stored.iloc[0][f"mean_{metric}"], source[metric].mean()):
                mean_errors.append(f"{model_name}/mean_{metric}")
    checks.append(check("mean_fold_metrics_use_unweighted_four_fold_mean", not mean_errors, f"errors={mean_errors}"))

    ridge_summary = pd.read_csv(output_dir / "ridge_alpha_summary.csv")
    expected_alpha = float(ridge_summary.sort_values(["mean_MAE", "alpha"], ascending=[True, False]).iloc[0]["alpha"])
    checks.append(check("ridge_alpha_selection_rule", expected_alpha == float(selection["selected_ridge_alpha"]), f"selected={selection['selected_ridge_alpha']}"))

    candidates = pd.read_csv(output_dir / "model_selection_candidates.csv")
    qualified = candidates[candidates["qualified_to_replace_baseline"].astype(bool)]
    if len(qualified):
        expected_selected = str(qualified.sort_values(["mean_MAE", "complexity_rank", "mean_RMSE", "model"]).iloc[0]["model"])
    else:
        expected_selected = "baseline_previous_week"
    checks.append(check("model_selection_follows_locked_rules", selection["selected_model"] == expected_selected, f"selected={selection['selected_model']}; expected={expected_selected}"))
    checks.append(check("polynomial_execution_matches_gate", bool(polynomial_gate["gate_passed"]) == bool(polynomial_gate["polynomial_executed"]) == bool(run_summary["polynomial_executed"]), f"gate={polynomial_gate['gate_passed']}"))
    polynomial_column_present = "pred__polynomial_regression_degree_2" in predictions.columns
    checks.append(check("polynomial_prediction_presence_matches_execution", polynomial_column_present == bool(run_summary["polynomial_executed"]), f"column_present={polynomial_column_present}"))

    small_figures = sorted(path.name for path in figures.glob("*.png") if path.stat().st_size < 10_000)
    checks.append(check("figures_have_nontrivial_size", not small_figures, f"small={small_figures}"))
    failed = [item for item in checks if not item["passed"]]
    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": not failed,
        "scope": "development_rolling_validation_only",
        "modeling_config_sha256": config_hash,
        "validation_rows": int(len(predictions)),
        "models_evaluated": model_names,
        "selected_model": selection["selected_model"],
        "polynomial_executed": bool(run_summary["polynomial_executed"]),
        "final_test_rows_used": 0,
        "checks_passed": len(checks) - len(failed),
        "checks_total": len(checks),
        "errors": [item["check"] for item in failed],
        "checks": checks,
    }
    write_json(output_dir / "modeling_verification.json", report)
    print(json.dumps(report, indent=2))
    if failed:
        raise RuntimeError("Step 7 verification failed: " + "; ".join(report["errors"]))


if __name__ == "__main__":
    main()
