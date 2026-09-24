"""Independently verify the locked Step 6 feature frame, folds, and leakage controls."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from build_modeling_frame import (
    DEFAULT_MODELING_CONFIG,
    feature_names,
    load_modeling_config,
    maximum_lookback_blocks,
    write_json,
)


REQUIRED_OUTPUTS = {
    "excluded_features.csv",
    "feature_availability.csv",
    "feature_build_summary.json",
    "leakage_audit.json",
    "validation_folds.csv",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_MODELING_CONFIG)
    return parser.parse_args()


def values_match(left: pd.Series, right: pd.Series) -> bool:
    return bool(np.allclose(left.to_numpy(dtype="float64"), right.to_numpy(dtype="float64"), equal_nan=True))


def check_record(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


def main() -> None:
    args = parse_args()
    config_path = args.config.resolve()
    config = load_modeling_config(config_path)
    output_dir: Path = config["output_dir"]
    frame_path: Path = config["modeling_frame_path"]
    panel_path: Path = config["panel_path"]
    checks: list[dict[str, Any]] = []

    missing_outputs = sorted(name for name in REQUIRED_OUTPUTS if not (output_dir / name).is_file())
    checks.append(check_record("required_spec_outputs_exist", not missing_outputs, f"missing={missing_outputs}"))
    checks.append(check_record("modeling_frame_exists", frame_path.is_file(), str(frame_path)))
    if missing_outputs or not frame_path.is_file():
        report = {
            "verified_at_utc": datetime.now(timezone.utc).isoformat(),
            "success": False,
            "errors": [item["detail"] for item in checks if not item["passed"]],
            "checks": checks,
        }
        write_json(output_dir / "modeling_spec_verification.json", report)
        raise RuntimeError("Missing Step 6 artefacts")

    frame = pd.read_parquet(frame_path).sort_values(["zone_id", "block_start"]).reset_index(drop=True)
    panel = pd.read_parquet(panel_path).sort_values(["zone_id", "block_start"]).reset_index(drop=True)
    frame["block_start"] = pd.to_datetime(frame["block_start"])
    panel["block_start"] = pd.to_datetime(panel["block_start"])
    calendar_names, history_names = feature_names(config)
    active_features = ["zone_id", *calendar_names, *history_names]
    target = config["target"]
    start = pd.Timestamp(config["development_start"])
    end = pd.Timestamp(config["development_end_exclusive"])
    final_start = pd.Timestamp(config["final_test_start"])
    max_lookback = maximum_lookback_blocks(config)
    first_ready = pd.Timestamp(start.to_pydatetime() + timedelta(hours=max_lookback * int(config["block_hours"])))

    same_keys = len(frame) == len(panel) and frame[["zone_id", "block_start"]].equals(panel[["zone_id", "block_start"]])
    checks.append(check_record("frame_keys_match_panel", same_keys, f"frame_rows={len(frame):,}; panel_rows={len(panel):,}"))
    same_target = len(frame) == len(panel) and np.array_equal(frame[target].to_numpy(), panel[target].to_numpy())
    checks.append(check_record("target_is_unchanged", same_target, f"target_sum={int(frame[target].sum()):,}"))
    checks.append(check_record("unique_zone_block_key", not frame.duplicated(["zone_id", "block_start"]).any(), "no duplicate key expected"))
    checks.append(check_record("development_window_only", frame["block_start"].min() == start and frame["block_start"].max() < end, f"{frame['block_start'].min()} to {frame['block_start'].max()}"))
    checks.append(check_record("no_final_test_rows", not (frame["block_start"] >= final_start).any(), "2026+ rows=0"))

    expected_ready = frame["block_start"] >= first_ready
    checks.append(check_record("feature_ready_flag_matches_warmup", frame["features_ready"].equals(expected_ready), f"first_ready={first_ready}"))
    checks.append(check_record("active_features_complete_when_ready", not frame.loc[frame["features_ready"], active_features].isna().any().any(), f"ready_rows={int(frame['features_ready'].sum()):,}"))

    expected_calendar = {
        "month_of_year": frame["block_start"].dt.month,
        "day_of_week": frame["block_start"].dt.dayofweek,
        "block_hour": frame["block_start"].dt.hour,
        "is_holiday": frame["block_start"].dt.normalize().isin(pd.to_datetime(config["holiday_dates"])).astype(int),
        "time_index_days": (frame["block_start"] - start).dt.total_seconds() / 86_400,
    }
    for name, expected in expected_calendar.items():
        checks.append(check_record(f"calendar_definition_{name}", values_match(frame[name], expected), "recomputed from block_start/locked calendar"))

    grouped = frame.groupby("zone_id", sort=False)[target]
    for feature in config["history_features"]:
        name = feature["name"]
        operation = feature["operation"]
        if operation == "lag":
            expected = grouped.shift(int(feature["lag_blocks"]))
        elif operation == "mean_of_lags":
            expected = pd.concat(
                [grouped.shift(int(lag)) for lag in feature["lag_blocks"]], axis=1
            ).mean(axis=1)
        elif operation == "shifted_rolling":
            shifted = grouped.shift(int(feature["shift_blocks"]))
            rolling = shifted.groupby(frame["zone_id"], sort=False).rolling(
                int(feature["window_blocks"]), min_periods=int(feature["window_blocks"])
            )
            if feature["statistic"] == "mean":
                expected = rolling.mean().reset_index(level=0, drop=True).sort_index()
            elif feature["statistic"] == "std_ddof_0":
                expected = rolling.std(ddof=0).reset_index(level=0, drop=True).sort_index()
            else:
                raise ValueError(f"Unsupported statistic in verifier: {feature['statistic']}")
        else:
            raise ValueError(f"Unsupported operation in verifier: {operation}")
        checks.append(check_record(f"history_definition_{name}", values_match(frame[name], expected), "recomputed independently from prior same-zone targets"))

    availability = pd.read_csv(output_dir / "feature_availability.csv")
    checks.append(check_record("availability_matrix_matches_active_features", set(availability["feature"]) == set(active_features), f"features={len(availability)}"))
    checks.append(check_record("availability_matrix_has_no_target_block_use", not availability["uses_target_block"].astype(bool).any(), "all active features must be pre-target"))
    history_availability = availability[availability["role"] == "historical_target"]
    checks.append(check_record("history_requires_positive_offset", bool((history_availability["minimum_source_offset_blocks"] >= 1).all()), "minimum source offset >= 1 block"))

    model_references: set[str] = set()
    for model in config["models"]:
        for key in ("categorical_features", "numeric_features", "categorical_main_effects", "numeric_main_effects", "polynomial_numeric_features"):
            model_references.update(model.get(key, []))
    checks.append(check_record("all_model_features_exist", model_references <= set(active_features), f"missing={sorted(model_references - set(active_features))}"))
    baseline_references = {item["prediction_column"] for item in config["baselines"]}
    checks.append(check_record("all_baseline_columns_exist", baseline_references <= set(frame.columns), f"missing={sorted(baseline_references - set(frame.columns))}"))

    excluded = {item["feature"] for item in config["excluded_features"]}
    checks.append(check_record("excluded_features_not_active", not (excluded & set(active_features)), f"intersection={sorted(excluded & set(active_features))}"))
    checks.append(check_record("preprocessing_fit_is_training_only", "fit_on_fold_training_only" in config["preprocessing"]["one_hot_encoder"] and "fit_on_fold_training_only" in config["preprocessing"]["standard_scaling"], "encoder and scaler policy"))

    folds = pd.read_csv(output_dir / "validation_folds.csv", parse_dates=[
        "train_start", "train_end_exclusive", "validation_start", "validation_end_exclusive", "effective_train_start"
    ])
    previous_validation_end: pd.Timestamp | None = None
    folds_valid = True
    fold_details: list[str] = []
    for fold in config["validation"]["folds"]:
        stored = folds.loc[folds["fold"] == fold["fold"]]
        if len(stored) != 1:
            folds_valid = False
            fold_details.append(f"{fold['fold']}: missing or duplicate")
            continue
        train_start = pd.Timestamp(fold["train_start"])
        train_end = pd.Timestamp(fold["train_end_exclusive"])
        validation_start = pd.Timestamp(fold["validation_start"])
        validation_end = pd.Timestamp(fold["validation_end_exclusive"])
        train_mask = frame["features_ready"] & frame["block_start"].between(train_start, train_end, inclusive="left")
        validation_mask = frame["features_ready"] & frame["block_start"].between(validation_start, validation_end, inclusive="left")
        row = stored.iloc[0]
        if train_end != validation_start or (previous_validation_end is not None and validation_start != previous_validation_end):
            folds_valid = False
            fold_details.append(f"{fold['fold']}: chronology/continuity failure")
        if int(row["train_rows"]) != int(train_mask.sum()) or int(row["validation_rows"]) != int(validation_mask.sum()):
            folds_valid = False
            fold_details.append(f"{fold['fold']}: row count mismatch")
        previous_validation_end = validation_end
    checks.append(check_record("fold_table_matches_locked_rolling_windows", folds_valid, f"errors={fold_details}"))
    checks.append(check_record("validation_covers_2025_once", folds["validation_rows"].sum() == int(((frame["block_start"] >= pd.Timestamp("2025-01-01")) & (frame["block_start"] < end)).sum()), f"validation_rows={int(folds['validation_rows'].sum()):,}"))

    build_summary = json.loads((output_dir / "feature_build_summary.json").read_text(encoding="utf-8"))
    leakage_audit = json.loads((output_dir / "leakage_audit.json").read_text(encoding="utf-8"))
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    checks.append(check_record("config_checksum_matches_build", build_summary.get("config_sha256") == config_hash and leakage_audit.get("config_sha256") == config_hash, config_hash))
    checks.append(check_record("builder_leakage_audit_passed", leakage_audit.get("success") is True, f"checks={leakage_audit.get('checks_passed')}/{leakage_audit.get('checks_total')}"))

    failed = [item for item in checks if not item["passed"]]
    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": not failed,
        "config_version": config["version"],
        "config_sha256": config_hash,
        "scope": "development_only",
        "final_test_rows_used": 0,
        "rows": int(len(frame)),
        "feature_ready_rows": int(frame["features_ready"].sum()),
        "active_feature_count_including_zone_id": len(active_features),
        "fold_count": len(config["validation"]["folds"]),
        "checks_passed": len(checks) - len(failed),
        "checks_total": len(checks),
        "errors": [item["check"] for item in failed],
        "checks": checks,
    }
    write_json(output_dir / "modeling_spec_verification.json", report)
    print(json.dumps(report, indent=2))
    if failed:
        raise RuntimeError("Modeling-spec verification failed: " + "; ".join(report["errors"]))


if __name__ == "__main__":
    main()
