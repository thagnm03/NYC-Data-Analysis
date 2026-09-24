"""Build the locked, leakage-safe Step 6 development modeling frame and audit artefacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELING_CONFIG = PROJECT_ROOT / "config" / "modeling_config.json"


def resolve_from_config(config_path: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def load_modeling_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "version",
        "status",
        "panel_path",
        "modeling_frame_path",
        "output_dir",
        "development_start",
        "development_end_exclusive",
        "final_test_start",
        "final_test_end_exclusive",
        "block_hours",
        "target",
        "calendar_features",
        "history_features",
        "holiday_dates",
        "baselines",
        "models",
        "validation",
        "metrics",
        "segments",
        "model_selection",
        "excluded_features",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise KeyError(f"Modeling config is missing keys: {missing}")
    config["panel_path"] = resolve_from_config(path, config["panel_path"])
    config["modeling_frame_path"] = resolve_from_config(path, config["modeling_frame_path"])
    config["output_dir"] = resolve_from_config(path, config["output_dir"])
    return config


def write_json(path: Path, payload: dict[str, Any]) -> None:
    def safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [safe(item) for item in value]
        if isinstance(value, (pd.Timestamp, datetime)):
            return value.isoformat()
        if hasattr(value, "item"):
            return safe(value.item())
        if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
            return None
        return value

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(safe(payload), indent=2), encoding="utf-8")
    temporary.replace(path)


def feature_names(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    calendar = [item["name"] for item in config["calendar_features"]]
    history = [item["name"] for item in config["history_features"]]
    return calendar, history


def maximum_lookback_blocks(config: dict[str, Any]) -> int:
    values: list[int] = []
    for feature in config["history_features"]:
        if feature["operation"] == "lag":
            values.append(int(feature["lag_blocks"]))
        elif feature["operation"] == "mean_of_lags":
            values.append(max(int(value) for value in feature["lag_blocks"]))
        elif feature["operation"] == "shifted_rolling":
            values.append(int(feature["shift_blocks"]) + int(feature["window_blocks"]) - 1)
        else:
            raise ValueError(f"Unsupported history operation: {feature['operation']}")
    return max(values)


def build_history_features(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    grouped = frame.groupby("zone_id", sort=False)[config["target"]]
    for feature in config["history_features"]:
        name = feature["name"]
        operation = feature["operation"]
        if operation == "lag":
            frame[name] = grouped.shift(int(feature["lag_blocks"])).astype("float64")
        elif operation == "mean_of_lags":
            components = [grouped.shift(int(lag)) for lag in feature["lag_blocks"]]
            frame[name] = pd.concat(components, axis=1).mean(axis=1).astype("float64")
        elif operation == "shifted_rolling":
            shift = int(feature["shift_blocks"])
            window = int(feature["window_blocks"])
            shifted = grouped.shift(shift)
            rolling = shifted.groupby(frame["zone_id"], sort=False).rolling(window, min_periods=window)
            statistic = feature["statistic"]
            if statistic == "mean":
                values = rolling.mean()
            elif statistic == "std_ddof_0":
                values = rolling.std(ddof=0)
            else:
                raise ValueError(f"Unsupported rolling statistic: {statistic}")
            frame[name] = values.reset_index(level=0, drop=True).sort_index().astype("float64")
        else:
            raise ValueError(f"Unsupported history operation: {operation}")
    return frame


def build_feature_availability(config: dict[str, Any]) -> pd.DataFrame:
    models_by_feature: dict[str, list[str]] = {}
    for model in config["models"]:
        names = (
            model.get("categorical_features", [])
            + model.get("numeric_features", [])
            + model.get("categorical_main_effects", [])
            + model.get("numeric_main_effects", [])
            + model.get("polynomial_numeric_features", [])
        )
        for name in names:
            models_by_feature.setdefault(name, []).append(model["name"])
    baselines_by_feature = {
        baseline["prediction_column"]: baseline["name"] for baseline in config["baselines"]
    }

    rows: list[dict[str, Any]] = [
        {
            "feature": "zone_id",
            "role": "entity_identity",
            "dtype": "categorical",
            "source": "taxi_zone_lookup_and_panel_key",
            "transformation": "one_hot_fit_on_fold_training_only",
            "minimum_source_offset_blocks": None,
            "maximum_lookback_blocks": 0,
            "known_at_prediction_time": True,
            "requires_internal_feed": False,
            "uses_target_block": False,
            "included_in_models": "|".join(sorted(set(models_by_feature.get("zone_id", [])))),
            "baseline_role": "",
        }
    ]
    for feature in config["calendar_features"]:
        rows.append(
            {
                "feature": feature["name"],
                "role": "calendar",
                "dtype": feature["dtype"],
                "source": "block_start_or_locked_holiday_calendar",
                "transformation": feature["definition"],
                "minimum_source_offset_blocks": None,
                "maximum_lookback_blocks": 0,
                "known_at_prediction_time": True,
                "requires_internal_feed": False,
                "uses_target_block": False,
                "included_in_models": "|".join(sorted(set(models_by_feature.get(feature["name"], [])))),
                "baseline_role": baselines_by_feature.get(feature["name"], ""),
            }
        )
    for feature in config["history_features"]:
        if feature["operation"] == "lag":
            minimum_offset = maximum_lookback = int(feature["lag_blocks"])
        elif feature["operation"] == "mean_of_lags":
            lags = [int(value) for value in feature["lag_blocks"]]
            minimum_offset, maximum_lookback = min(lags), max(lags)
        else:
            minimum_offset = int(feature["shift_blocks"])
            maximum_lookback = minimum_offset + int(feature["window_blocks"]) - 1
        rows.append(
            {
                "feature": feature["name"],
                "role": "historical_target",
                "dtype": "numeric_continuous",
                "source": "same_zone_completed_pickup_counts",
                "transformation": feature["definition"],
                "minimum_source_offset_blocks": minimum_offset,
                "maximum_lookback_blocks": maximum_lookback,
                "known_at_prediction_time": True,
                "requires_internal_feed": True,
                "uses_target_block": False,
                "included_in_models": "|".join(sorted(set(models_by_feature.get(feature["name"], [])))),
                "baseline_role": baselines_by_feature.get(feature["name"], ""),
            }
        )
    return pd.DataFrame(rows)


def validate_fold_contract(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    folds = config["validation"]["folds"]
    prior_validation_end: pd.Timestamp | None = None
    prior_train_end: pd.Timestamp | None = None
    for fold in folds:
        train_start = pd.Timestamp(fold["train_start"])
        train_end = pd.Timestamp(fold["train_end_exclusive"])
        validation_start = pd.Timestamp(fold["validation_start"])
        validation_end = pd.Timestamp(fold["validation_end_exclusive"])
        if not train_start < train_end == validation_start < validation_end:
            errors.append(f"{fold['fold']} is not a strict train-then-validation interval")
        if prior_validation_end is not None and validation_start != prior_validation_end:
            errors.append(f"{fold['fold']} validation is not contiguous with the previous fold")
        if prior_train_end is not None and train_end <= prior_train_end:
            errors.append(f"{fold['fold']} training window does not expand")
        prior_validation_end = validation_end
        prior_train_end = train_end
    if pd.Timestamp(folds[0]["validation_start"]) != pd.Timestamp("2025-01-01"):
        errors.append("development validation must start on 2025-01-01")
    if pd.Timestamp(folds[-1]["validation_end_exclusive"]) != pd.Timestamp(config["development_end_exclusive"]):
        errors.append("development validation must end at the sealed development cutoff")
    return errors


def audit_record(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_MODELING_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config.resolve()
    config = load_modeling_config(config_path)
    panel_path: Path = config["panel_path"]
    output_dir: Path = config["output_dir"]
    modeling_frame_path: Path = config["modeling_frame_path"]
    output_dir.mkdir(parents=True, exist_ok=True)
    modeling_frame_path.parent.mkdir(parents=True, exist_ok=True)
    if not panel_path.is_file():
        raise FileNotFoundError(f"Missing development panel: {panel_path}")

    frame = pd.read_parquet(panel_path).sort_values(["zone_id", "block_start"]).reset_index(drop=True)
    frame["block_start"] = pd.to_datetime(frame["block_start"])
    target = config["target"]
    start = pd.Timestamp(config["development_start"])
    end = pd.Timestamp(config["development_end_exclusive"])
    holidays = pd.DatetimeIndex(pd.to_datetime(config["holiday_dates"]))

    frame["month_of_year"] = frame["block_start"].dt.month.astype("int8")
    frame["day_of_week"] = frame["block_start"].dt.dayofweek.astype("int8")
    frame["block_hour"] = frame["block_start"].dt.hour.astype("int8")
    frame["is_holiday"] = frame["block_start"].dt.normalize().isin(holidays).astype("int8")
    frame["time_index_days"] = (
        (frame["block_start"] - start).dt.total_seconds() / 86_400
    ).astype("float64")
    frame = build_history_features(frame, config)
    calendar_names, history_names = feature_names(config)
    frame["features_ready"] = frame[history_names].notna().all(axis=1)

    max_lookback = maximum_lookback_blocks(config)
    expected_first_ready = start + pd.Timedelta(hours=max_lookback * int(config["block_hours"]))
    audit: list[dict[str, Any]] = []
    audit.append(audit_record("config_locked", config["status"] == "locked_before_model_fitting", config["status"]))
    audit.append(audit_record("panel_starts_at_development_start", frame["block_start"].min() == start, str(frame["block_start"].min())))
    audit.append(audit_record("panel_ends_before_development_cutoff", frame["block_start"].max() < end, str(frame["block_start"].max())))
    audit.append(audit_record("no_final_test_rows", not (frame["block_start"] >= pd.Timestamp(config["final_test_start"])).any(), "final-test rows found: 0"))
    audit.append(audit_record("unique_zone_block_key", not frame.duplicated(["zone_id", "block_start"]).any(), f"rows={len(frame):,}"))
    audit.append(audit_record("nonnegative_target", bool((frame[target] >= 0).all()), f"minimum target={frame[target].min()}"))
    first_ready_by_zone = frame.loc[frame["features_ready"]].groupby("zone_id")["block_start"].min()
    audit.append(audit_record("common_feature_warmup_is_exact", bool((first_ready_by_zone == expected_first_ready).all()), f"expected={expected_first_ready}"))
    audit.append(audit_record("no_missing_features_after_warmup", not frame.loc[frame["block_start"] >= expected_first_ready, calendar_names + history_names].isna().any().any(), f"features={len(calendar_names) + len(history_names)}"))

    semantic_errors: list[str] = []
    for feature in config["history_features"]:
        if feature["operation"] == "lag" and int(feature["lag_blocks"]) < 1:
            semantic_errors.append(feature["name"])
        if feature["operation"] == "mean_of_lags" and min(int(value) for value in feature["lag_blocks"]) < 1:
            semantic_errors.append(feature["name"])
        if feature["operation"] == "shifted_rolling" and int(feature["shift_blocks"]) < 1:
            semantic_errors.append(feature["name"])
    audit.append(audit_record("all_target_history_strictly_precedes_target", not semantic_errors, f"invalid={semantic_errors}"))

    active_features = {"zone_id", *calendar_names, *history_names}
    excluded_names = {item["feature"] for item in config["excluded_features"]}
    audit.append(audit_record("no_explicitly_excluded_feature_is_active", not (active_features & excluded_names), f"intersection={sorted(active_features & excluded_names)}"))
    fold_errors = validate_fold_contract(config)
    audit.append(audit_record("rolling_folds_are_chronological_and_contiguous", not fold_errors, f"errors={fold_errors}"))
    audit.append(audit_record("final_test_begins_at_development_cutoff", pd.Timestamp(config["final_test_start"]) == end, config["final_test_start"]))

    for feature in config["history_features"]:
        name = feature["name"]
        if feature["operation"] == "lag":
            expected = frame.groupby("zone_id", sort=False)[target].shift(int(feature["lag_blocks"]))
            matches = np.allclose(frame[name], expected, equal_nan=True)
            audit.append(audit_record(f"empirical_alignment_{name}", bool(matches), "matches same-zone backward shift"))

    availability = build_feature_availability(config)
    availability.to_csv(output_dir / "feature_availability.csv", index=False)
    pd.DataFrame(config["excluded_features"]).to_csv(output_dir / "excluded_features.csv", index=False)

    folds: list[dict[str, Any]] = []
    for fold in config["validation"]["folds"]:
        train_start = pd.Timestamp(fold["train_start"])
        train_end = pd.Timestamp(fold["train_end_exclusive"])
        validation_start = pd.Timestamp(fold["validation_start"])
        validation_end = pd.Timestamp(fold["validation_end_exclusive"])
        train_mask = frame["features_ready"] & frame["block_start"].between(train_start, train_end, inclusive="left")
        validation_mask = frame["features_ready"] & frame["block_start"].between(validation_start, validation_end, inclusive="left")
        folds.append(
            {
                **fold,
                "effective_train_start": max(train_start, expected_first_ready),
                "train_rows": int(train_mask.sum()),
                "validation_rows": int(validation_mask.sum()),
                "train_target_sum": int(frame.loc[train_mask, target].sum()),
                "validation_target_sum": int(frame.loc[validation_mask, target].sum()),
                "zones_in_train": int(frame.loc[train_mask, "zone_id"].nunique()),
                "zones_in_validation": int(frame.loc[validation_mask, "zone_id"].nunique()),
            }
        )
    fold_table = pd.DataFrame(folds)
    fold_table.to_csv(output_dir / "validation_folds.csv", index=False)

    failed = [item for item in audit if not item["passed"]]
    audit_payload = {
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": not failed,
        "config_version": config["version"],
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "scope": "development_only",
        "final_test_rows_used": 0,
        "checks_passed": len(audit) - len(failed),
        "checks_total": len(audit),
        "checks": audit,
    }
    write_json(output_dir / "leakage_audit.json", audit_payload)
    if failed:
        raise RuntimeError("Leakage audit failed: " + "; ".join(item["check"] for item in failed))

    ordered_columns = [
        "zone_id",
        "borough",
        "zone_name",
        "service_zone",
        "block_start",
        target,
        *calendar_names,
        *history_names,
        "features_ready",
    ]
    temporary = modeling_frame_path.with_suffix(modeling_frame_path.suffix + ".tmp")
    frame[ordered_columns].to_parquet(temporary, index=False, engine="pyarrow", compression="zstd")
    temporary.replace(modeling_frame_path)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_version": config["version"],
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "config_status": config["status"],
        "scope": "development_only",
        "panel_file": str(panel_path),
        "modeling_frame_file": str(modeling_frame_path),
        "modeling_frame_bytes": modeling_frame_path.stat().st_size,
        "rows": int(len(frame)),
        "zones": int(frame["zone_id"].nunique()),
        "target_sum": int(frame[target].sum()),
        "first_block": frame["block_start"].min(),
        "last_block": frame["block_start"].max(),
        "calendar_feature_count": len(calendar_names),
        "history_feature_count": len(history_names),
        "maximum_lookback_blocks": max_lookback,
        "warmup_hours": max_lookback * int(config["block_hours"]),
        "first_feature_ready_block": frame.loc[frame["features_ready"], "block_start"].min(),
        "feature_ready_rows": int(frame["features_ready"].sum()),
        "warmup_rows": int((~frame["features_ready"]).sum()),
        "validation_rows_total": int(fold_table["validation_rows"].sum()),
        "fold_count": int(len(fold_table)),
        "baseline_count": len(config["baselines"]),
        "candidate_model_count": len(config["models"]),
        "final_test_rows_used": 0,
        "leakage_audit_success": True,
    }
    write_json(output_dir / "feature_build_summary.json", summary)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
