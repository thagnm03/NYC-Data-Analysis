"""Run Step 7 baselines and locked regression candidates on rolling validation folds."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, PolynomialFeatures, StandardScaler

from build_modeling_frame import load_modeling_config, write_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRAINING_CONFIG = PROJECT_ROOT / "config" / "training_config.json"
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".mplconfig"))
os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


DISPLAY_NAMES = {
    "baseline_previous_block": "Previous block",
    "baseline_previous_day": "Previous day",
    "baseline_previous_week": "Previous week",
    "baseline_seasonal_mean_4w": "4-week seasonal mean",
    "simple_linear_regression": "Simple linear regression",
    "multiple_linear_regression": "Multiple linear regression",
    "ridge_regression": "Ridge regression",
    "polynomial_regression_degree_2": "Polynomial regression (degree 2)",
}

MODEL_KIND = {
    "baseline_previous_block": "baseline",
    "baseline_previous_day": "baseline",
    "baseline_previous_week": "baseline",
    "baseline_seasonal_mean_4w": "baseline",
    "simple_linear_regression": "regression",
    "multiple_linear_regression": "regression",
    "ridge_regression": "regression",
    "polynomial_regression_degree_2": "regression",
}


def resolve_from_config(config_path: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def load_training_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "modeling_config_path",
        "expected_modeling_config_sha256",
        "output_dir",
        "figure_dpi",
        "linear_regression_tolerance",
        "ridge_solver",
        "ridge_tolerance",
        "ridge_max_iterations",
        "prediction_float_dtype",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise KeyError(f"Training config is missing keys: {missing}")
    config["modeling_config_path"] = resolve_from_config(path, config["modeling_config_path"])
    config["output_dir"] = resolve_from_config(path, config["output_dir"])
    return config


def calculate_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float | int | None]:
    y_true = np.asarray(y_true, dtype="float64")
    y_pred = np.asarray(y_pred, dtype="float64")
    errors = y_pred - y_true
    mae = float(mean_absolute_error(y_true, y_pred))
    mse = float(mean_squared_error(y_true, y_pred))
    denominator = float(np.abs(y_true).sum())
    if len(y_true) >= 2 and np.var(y_true) > 0:
        r2: float | None = float(r2_score(y_true, y_pred))
    else:
        r2 = None
    return {
        "n": int(len(y_true)),
        "target_sum": float(y_true.sum()),
        "prediction_sum": float(y_pred.sum()),
        "MAE": mae,
        "MSE": mse,
        "RMSE": float(math.sqrt(mse)),
        "WAPE_pct": None if denominator == 0 else float(100 * np.abs(errors).sum() / denominator),
        "R2": r2,
        "mean_bias": float(errors.mean()),
    }


def make_preprocessor(categorical: list[str], numeric: list[str], scale_numeric: bool) -> ColumnTransformer:
    transformers: list[tuple[str, Any, list[str]]] = []
    if categorical:
        transformers.append(
            (
                "categorical",
                OneHotEncoder(drop="first", handle_unknown="ignore", sparse_output=True, dtype=np.float64),
                categorical,
            )
        )
    if numeric:
        numeric_transformer: Any = StandardScaler() if scale_numeric else "passthrough"
        transformers.append(("numeric", numeric_transformer, numeric))
    return ColumnTransformer(transformers, remainder="drop", sparse_threshold=1.0)


def make_polynomial_preprocessor(model: dict[str, Any]) -> ColumnTransformer:
    return ColumnTransformer(
        [
            (
                "categorical",
                OneHotEncoder(drop="first", handle_unknown="ignore", sparse_output=True, dtype=np.float64),
                model["categorical_main_effects"],
            ),
            ("numeric_main", StandardScaler(), model["numeric_main_effects"]),
            (
                "polynomial_numeric",
                Pipeline(
                    [
                        ("polynomial", PolynomialFeatures(degree=int(model["degree"]), include_bias=bool(model["include_bias"]))),
                        ("scale", StandardScaler()),
                    ]
                ),
                model["polynomial_numeric_features"],
            ),
        ],
        remainder="drop",
        sparse_threshold=1.0,
    )


def clip_predictions(values: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(values, dtype="float64"), 0, None)


def assign_fold_segments(train: pd.DataFrame, validation: pd.DataFrame, target: str) -> pd.DataFrame:
    zone_totals = train.groupby("zone_id", as_index=False)[target].sum().sort_values([target, "zone_id"])
    zone_totals["activity_tier"] = pd.qcut(
        zone_totals[target].rank(method="first"), q=3, labels=["Low", "Medium", "High"]
    ).astype(str)
    tier_map = zone_totals.set_index("zone_id")["activity_tier"]

    time_profile = (
        train.groupby(["day_of_week", "block_hour"], as_index=False)[target]
        .mean()
        .sort_values([target, "day_of_week", "block_hour"], ascending=[False, True, True])
    )
    peak_cells = set(map(tuple, time_profile.head(14)[["day_of_week", "block_hour"]].to_numpy()))
    result = validation[["zone_id", "block_start", "borough", target]].copy()
    result["activity_tier"] = result["zone_id"].map(tier_map)
    result["peak_period"] = [
        "Peak" if (int(day), int(hour)) in peak_cells else "Off-peak"
        for day, hour in zip(validation["day_of_week"], validation["block_hour"])
    ]
    result["day_type"] = np.where(validation["day_of_week"] >= 5, "Weekend", "Weekday")
    result["target_state"] = np.where(validation[target] == 0, "Zero", "Positive")
    return result


def fold_masks(frame: pd.DataFrame, fold: dict[str, Any]) -> tuple[pd.Series, pd.Series]:
    train_mask = frame["features_ready"] & frame["block_start"].between(
        pd.Timestamp(fold["train_start"]), pd.Timestamp(fold["train_end_exclusive"]), inclusive="left"
    )
    validation_mask = frame["features_ready"] & frame["block_start"].between(
        pd.Timestamp(fold["validation_start"]), pd.Timestamp(fold["validation_end_exclusive"]), inclusive="left"
    )
    return train_mask, validation_mask


def compute_fold_metrics(predictions: pd.DataFrame, model_names: list[str], target: str) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for fold_name, group in predictions.groupby("fold", sort=True):
        y = group[target].to_numpy()
        for model_name in model_names:
            records.append(
                {
                    "fold": fold_name,
                    "model": model_name,
                    "display_name": DISPLAY_NAMES[model_name],
                    "model_kind": MODEL_KIND[model_name],
                    **calculate_metrics(y, group[f"pred__{model_name}"].to_numpy()),
                }
            )
    return pd.DataFrame(records)


def compute_segment_metrics(predictions: pd.DataFrame, model_names: list[str], target: str) -> pd.DataFrame:
    dimensions = ["activity_tier", "peak_period", "day_type", "borough", "target_state"]
    records: list[dict[str, Any]] = []
    for fold_name, fold_data in predictions.groupby("fold", sort=True):
        for dimension in dimensions:
            for segment, group in fold_data.groupby(dimension, sort=True):
                y = group[target].to_numpy()
                for model_name in model_names:
                    records.append(
                        {
                            "fold": fold_name,
                            "dimension": dimension,
                            "segment": str(segment),
                            "model": model_name,
                            "display_name": DISPLAY_NAMES[model_name],
                            **calculate_metrics(y, group[f"pred__{model_name}"].to_numpy()),
                        }
                    )
    return pd.DataFrame(records)


def aggregate_fold_metrics(fold_metrics: pd.DataFrame) -> pd.DataFrame:
    metric_columns = ["MAE", "MSE", "RMSE", "WAPE_pct", "R2", "mean_bias"]
    summary = (
        fold_metrics.groupby(["model", "display_name", "model_kind"], as_index=False)[metric_columns]
        .mean()
        .rename(columns={column: f"mean_{column}" for column in metric_columns})
    )
    summary["folds"] = fold_metrics.groupby("model")["fold"].nunique().reindex(summary["model"]).to_numpy()
    return summary


def polynomial_gate(
    predictions: pd.DataFrame,
    best_model: str,
    target: str,
    output_dir: Path,
) -> tuple[bool, dict[str, Any]]:
    y = predictions[target].to_numpy(dtype="float64")
    pred = predictions[f"pred__{best_model}"].to_numpy(dtype="float64")
    residual = y - pred
    global_mae = float(np.abs(residual).mean())
    ranks = pd.Series(pred).rank(method="first")
    deciles = pd.qcut(ranks, q=10, labels=False) + 1
    diagnostic = pd.DataFrame({"prediction": pred, "residual": residual, "decile": deciles})
    table = (
        diagnostic.groupby("decile", as_index=False)
        .agg(n=("residual", "size"), mean_prediction=("prediction", "mean"), mean_residual=("residual", "mean"), MAE=("residual", lambda values: float(np.abs(values).mean())))
    )
    threshold = 0.10 * global_mae
    table["qualifies"] = table["mean_residual"].abs() >= threshold
    table.to_csv(output_dir / "polynomial_residual_deciles.csv", index=False)
    qualifying = table.loc[table["qualifies"], "mean_residual"]
    gate_passed = len(qualifying) >= 3 and (qualifying > 0).any() and (qualifying < 0).any()
    payload = {
        "best_non_polynomial_regression": best_model,
        "pooled_oof_global_MAE": global_mae,
        "threshold_absolute_mean_residual": threshold,
        "qualifying_deciles": int(len(qualifying)),
        "qualifying_has_positive_residual": bool((qualifying > 0).any()),
        "qualifying_has_negative_residual": bool((qualifying < 0).any()),
        "gate_passed": bool(gate_passed),
    }
    return bool(gate_passed), payload


def plot_mean_mae(summary: pd.DataFrame, selected: str, figures: Path, dpi: int) -> None:
    ordered = summary.sort_values("mean_MAE", ascending=True)
    empirical_best = str(ordered.iloc[0]["model"])
    colors = [
        "#D95F02" if name == selected else "#54A24B" if name == empirical_best else "#4C78A8" if name == "baseline_previous_week" else "#9AA0A6"
        for name in ordered["model"]
    ]
    fig, ax = plt.subplots(figsize=(9.4, 5.5))
    bars = ax.barh(ordered["display_name"], ordered["mean_MAE"], color=colors)
    for bar, value in zip(bars, ordered["mean_MAE"]):
        ax.text(value, bar.get_y() + bar.get_height() / 2, f" {value:.2f}", va="center")
    best_bar = bars[0]
    ax.annotate("lowest observed MAE", (ordered.iloc[0]["mean_MAE"], best_bar.get_y() + best_bar.get_height() / 2), xytext=(55, -12), textcoords="offset points")
    selected_position = list(ordered["model"]).index(selected)
    selected_bar = bars[selected_position]
    ax.annotate("formal selection", (ordered.iloc[selected_position]["mean_MAE"], selected_bar.get_y() + selected_bar.get_height() / 2), xytext=(55, 8), textcoords="offset points")
    ax.set(title="Mean validation MAE across four rolling folds", xlabel="MAE (pickups per zone-block; lower is better)", ylabel="")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "mean_mae_comparison.png", dpi=dpi)
    plt.close(fig)


def plot_fold_mae(fold_metrics: pd.DataFrame, model_names: list[str], figures: Path, dpi: int) -> None:
    focus = ["baseline_previous_week", "baseline_seasonal_mean_4w", *[name for name in model_names if MODEL_KIND[name] == "regression"]]
    colors = ["#4C78A8", "#F2CF5B", "#72B7B2", "#54A24B", "#E45756", "#B279A2"]
    fig, ax = plt.subplots(figsize=(9.4, 5.4))
    for color, model_name in zip(colors, focus):
        group = fold_metrics[fold_metrics["model"] == model_name].sort_values("fold")
        ax.plot(group["fold"], group["MAE"], marker="o", linewidth=2, color=color, label=DISPLAY_NAMES[model_name])
    ax.set(title="Validation MAE by rolling fold", xlabel="Validation fold", ylabel="MAE (pickups per zone-block)")
    ax.legend(frameon=False, ncol=2)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "fold_mae_comparison.png", dpi=dpi)
    plt.close(fig)


def plot_low_tier(segment_metrics: pd.DataFrame, comparison_model: str, figures: Path, dpi: int) -> None:
    focus = segment_metrics[
        (segment_metrics["dimension"] == "activity_tier")
        & (segment_metrics["model"].isin(["baseline_previous_week", comparison_model]))
    ]
    summary = focus.groupby(["segment", "model"], as_index=False)["MAE"].mean()
    tiers = ["Low", "Medium", "High"]
    x = np.arange(len(tiers))
    width = 0.36
    fig, ax = plt.subplots(figsize=(8.8, 5.0))
    for offset, model_name, color in [
        (-width / 2, "baseline_previous_week", "#4C78A8"),
        (width / 2, comparison_model, "#D95F02"),
    ]:
        values = summary[summary["model"] == model_name].set_index("segment").reindex(tiers)["MAE"]
        bars = ax.bar(x + offset, values, width, color=color, label=DISPLAY_NAMES[model_name])
        ax.bar_label(bars, labels=[f"{value:.2f}" for value in values], padding=3)
    ax.set(xticks=x, xticklabels=tiers, title="Best regression versus weekly baseline by activity tier", xlabel="Activity tier defined from each fold's training data", ylabel="Mean fold MAE")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "activity_tier_mae.png", dpi=dpi)
    plt.close(fig)


def plot_ridge_alpha(alpha_summary: pd.DataFrame, selected_alpha: float, figures: Path, dpi: int) -> None:
    ordered = alpha_summary.sort_values("alpha")
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    ax.plot(ordered["alpha"], ordered["mean_MAE"], marker="o", linewidth=2, color="#4C78A8")
    selected = ordered[ordered["alpha"] == selected_alpha].iloc[0]
    ax.scatter([selected_alpha], [selected["mean_MAE"]], color="#D95F02", s=55, zorder=3)
    ax.annotate(f"selected α={selected_alpha:g}", (selected_alpha, selected["mean_MAE"]), xytext=(8, 8), textcoords="offset points")
    ax.set_xscale("log")
    ax.set(title="Ridge alpha selected by mean rolling-fold MAE", xlabel="Alpha (log scale)", ylabel="Mean validation MAE")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "ridge_alpha_curve.png", dpi=dpi)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_TRAINING_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    training_config_path = args.config.resolve()
    training_config = load_training_config(training_config_path)
    modeling_config_path: Path = training_config["modeling_config_path"]
    modeling_hash = hashlib.sha256(modeling_config_path.read_bytes()).hexdigest()
    if modeling_hash != training_config["expected_modeling_config_sha256"]:
        raise RuntimeError("Locked modeling config checksum changed; rebuild/review Step 6 before fitting")
    modeling_config = load_modeling_config(modeling_config_path)
    spec_verification = json.loads(
        (modeling_config["output_dir"] / "modeling_spec_verification.json").read_text(encoding="utf-8")
    )
    if not spec_verification.get("success") or spec_verification.get("config_sha256") != modeling_hash:
        raise RuntimeError("Step 6 independent verification is missing, failed, or stale")

    output_dir: Path = training_config["output_dir"]
    figures = output_dir / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    frame = pd.read_parquet(modeling_config["modeling_frame_path"])
    frame["block_start"] = pd.to_datetime(frame["block_start"])
    if (frame["block_start"] >= pd.Timestamp(modeling_config["final_test_start"])).any():
        raise RuntimeError("Final-test rows found in Step 7 modeling frame")
    target = modeling_config["target"]
    model_specs = {model["name"]: model for model in modeling_config["models"]}
    validation_frames: list[pd.DataFrame] = []
    ridge_alpha_records: list[dict[str, Any]] = []
    fit_records: list[dict[str, Any]] = []

    for fold_number, fold in enumerate(modeling_config["validation"]["folds"], start=1):
        fold_start = time.perf_counter()
        train_mask, validation_mask = fold_masks(frame, fold)
        train = frame.loc[train_mask].copy()
        validation = frame.loc[validation_mask].copy()
        prediction_frame = assign_fold_segments(train, validation, target)
        prediction_frame["fold"] = fold["fold"]
        prediction_frame["_frame_index"] = validation.index.to_numpy()
        y_train = train[target].to_numpy(dtype="float64")
        y_validation = validation[target].to_numpy(dtype="float64")

        for baseline in modeling_config["baselines"]:
            model_name = f"baseline_{baseline['name']}"
            prediction_frame[f"pred__{model_name}"] = clip_predictions(
                validation[baseline["prediction_column"]].to_numpy()
            )

        slr_spec = model_specs["simple_linear_regression"]
        tick = time.perf_counter()
        slr = LinearRegression(tol=float(training_config["linear_regression_tolerance"]))
        slr.fit(train[slr_spec["numeric_features"]], y_train)
        prediction_frame["pred__simple_linear_regression"] = clip_predictions(
            slr.predict(validation[slr_spec["numeric_features"]])
        )
        fit_records.append(
            {
                "fold": fold["fold"],
                "model": "simple_linear_regression",
                "train_rows": len(train),
                "validation_rows": len(validation),
                "transformed_features": len(slr_spec["numeric_features"]),
                "fit_seconds": time.perf_counter() - tick,
                "coefficient_lag_42": float(slr.coef_[0]),
                "intercept": float(slr.intercept_),
            }
        )

        mlr_spec = model_specs["multiple_linear_regression"]
        tick = time.perf_counter()
        mlr_preprocessor = make_preprocessor(
            mlr_spec["categorical_features"], mlr_spec["numeric_features"], scale_numeric=False
        )
        x_train_mlr = mlr_preprocessor.fit_transform(train)
        x_validation_mlr = mlr_preprocessor.transform(validation)
        mlr = LinearRegression(tol=float(training_config["linear_regression_tolerance"]))
        mlr.fit(x_train_mlr, y_train)
        prediction_frame["pred__multiple_linear_regression"] = clip_predictions(
            mlr.predict(x_validation_mlr)
        )
        fit_records.append(
            {
                "fold": fold["fold"],
                "model": "multiple_linear_regression",
                "train_rows": len(train),
                "validation_rows": len(validation),
                "transformed_features": int(x_train_mlr.shape[1]),
                "fit_seconds": time.perf_counter() - tick,
                "coefficient_lag_42": None,
                "intercept": float(mlr.intercept_),
            }
        )
        del x_train_mlr, x_validation_mlr, mlr_preprocessor, mlr

        ridge_spec = model_specs["ridge_regression"]
        tick = time.perf_counter()
        ridge_preprocessor = make_preprocessor(
            ridge_spec["categorical_features"], ridge_spec["numeric_features"], scale_numeric=True
        )
        x_train_ridge = ridge_preprocessor.fit_transform(train)
        x_validation_ridge = ridge_preprocessor.transform(validation)
        preprocessing_seconds = time.perf_counter() - tick
        for alpha in ridge_spec["alpha_grid"]:
            alpha_tick = time.perf_counter()
            ridge = Ridge(
                alpha=float(alpha),
                solver=training_config["ridge_solver"],
                tol=float(training_config["ridge_tolerance"]),
                max_iter=int(training_config["ridge_max_iterations"]),
            )
            ridge.fit(x_train_ridge, y_train)
            ridge_prediction = clip_predictions(ridge.predict(x_validation_ridge))
            prediction_frame[f"__ridge_alpha_{float(alpha):g}"] = ridge_prediction
            ridge_alpha_records.append(
                {
                    "fold": fold["fold"],
                    "alpha": float(alpha),
                    **calculate_metrics(y_validation, ridge_prediction),
                }
            )
            fit_records.append(
                {
                    "fold": fold["fold"],
                    "model": f"ridge_alpha_{float(alpha):g}",
                    "train_rows": len(train),
                    "validation_rows": len(validation),
                    "transformed_features": int(x_train_ridge.shape[1]),
                    "fit_seconds": time.perf_counter() - alpha_tick,
                    "preprocessing_seconds": preprocessing_seconds,
                    "coefficient_lag_42": None,
                    "intercept": float(ridge.intercept_),
                }
            )
        del x_train_ridge, x_validation_ridge, ridge_preprocessor, ridge
        validation_frames.append(prediction_frame)
        print(
            f"completed {fold['fold']} ({fold_number}/4): train={len(train):,}, "
            f"validation={len(validation):,}, elapsed={time.perf_counter() - fold_start:.1f}s",
            flush=True,
        )

    predictions = pd.concat(validation_frames, ignore_index=True)
    ridge_alpha_folds = pd.DataFrame(ridge_alpha_records)
    ridge_alpha_summary = (
        ridge_alpha_folds.groupby("alpha", as_index=False)[["MAE", "MSE", "RMSE", "WAPE_pct", "R2", "mean_bias"]]
        .mean()
        .rename(columns={column: f"mean_{column}" for column in ["MAE", "MSE", "RMSE", "WAPE_pct", "R2", "mean_bias"]})
        .sort_values(["mean_MAE", "alpha"], ascending=[True, False])
    )
    selected_alpha = float(ridge_alpha_summary.iloc[0]["alpha"])
    predictions["pred__ridge_regression"] = predictions[f"__ridge_alpha_{selected_alpha:g}"]
    predictions.drop(columns=[column for column in predictions if column.startswith("__ridge_alpha_")], inplace=True)
    ridge_alpha_folds.to_csv(output_dir / "ridge_alpha_fold_metrics.csv", index=False)
    ridge_alpha_summary.to_csv(output_dir / "ridge_alpha_summary.csv", index=False)

    baseline_names = [f"baseline_{item['name']}" for item in modeling_config["baselines"]]
    nonpoly_names = ["simple_linear_regression", "multiple_linear_regression", "ridge_regression"]
    preliminary_metrics = compute_fold_metrics(predictions, baseline_names + nonpoly_names, target)
    preliminary_summary = aggregate_fold_metrics(preliminary_metrics)
    best_nonpoly = (
        preliminary_summary[preliminary_summary["model"].isin(nonpoly_names)]
        .sort_values(["mean_MAE", "mean_RMSE", "model"])
        .iloc[0]["model"]
    )
    gate_passed, gate_payload = polynomial_gate(predictions, str(best_nonpoly), target, output_dir)
    polynomial_executed = False
    polynomial_name = "polynomial_regression_degree_2"
    if gate_passed:
        poly_spec = model_specs[polynomial_name]
        predictions[f"pred__{polynomial_name}"] = np.nan
        for fold_number, fold in enumerate(modeling_config["validation"]["folds"], start=1):
            tick = time.perf_counter()
            train_mask, validation_mask = fold_masks(frame, fold)
            train = frame.loc[train_mask]
            validation = frame.loc[validation_mask]
            preprocessor = make_polynomial_preprocessor(poly_spec)
            x_train = preprocessor.fit_transform(train)
            x_validation = preprocessor.transform(validation)
            model = LinearRegression(tol=float(training_config["linear_regression_tolerance"]))
            model.fit(x_train, train[target].to_numpy(dtype="float64"))
            fold_prediction = clip_predictions(model.predict(x_validation))
            row_mask = predictions["fold"] == fold["fold"]
            if int(row_mask.sum()) != len(fold_prediction):
                raise RuntimeError(f"Polynomial prediction row mismatch in {fold['fold']}")
            predictions.loc[row_mask, f"pred__{polynomial_name}"] = fold_prediction
            fit_records.append(
                {
                    "fold": fold["fold"],
                    "model": polynomial_name,
                    "train_rows": len(train),
                    "validation_rows": len(validation),
                    "transformed_features": int(x_train.shape[1]),
                    "fit_seconds": time.perf_counter() - tick,
                    "coefficient_lag_42": None,
                    "intercept": float(model.intercept_),
                }
            )
            print(
                f"completed polynomial {fold['fold']} ({fold_number}/4): elapsed={time.perf_counter() - tick:.1f}s",
                flush=True,
            )
            del x_train, x_validation, preprocessor, model
        polynomial_executed = True

    model_names = baseline_names + nonpoly_names + ([polynomial_name] if polynomial_executed else [])
    fold_metrics = compute_fold_metrics(predictions, model_names, target)
    fold_metrics.to_csv(output_dir / "fold_metrics.csv", index=False)
    mean_metrics = aggregate_fold_metrics(fold_metrics)
    previous_week_mae = float(
        mean_metrics.loc[mean_metrics["model"] == "baseline_previous_week", "mean_MAE"].iloc[0]
    )
    mean_metrics["uplift_vs_previous_week_pct"] = 100 * (
        previous_week_mae - mean_metrics["mean_MAE"]
    ) / previous_week_mae
    mean_metrics = mean_metrics.sort_values("mean_MAE").reset_index(drop=True)
    mean_metrics.to_csv(output_dir / "mean_fold_metrics.csv", index=False)

    segment_metrics = compute_segment_metrics(predictions, model_names, target)
    segment_metrics.to_csv(output_dir / "segment_metrics.csv", index=False)
    segment_summary = (
        segment_metrics.groupby(["dimension", "segment", "model", "display_name"], as_index=False)[
            ["MAE", "MSE", "RMSE", "WAPE_pct", "R2", "mean_bias"]
        ]
        .mean()
        .rename(columns={column: f"mean_{column}" for column in ["MAE", "MSE", "RMSE", "WAPE_pct", "R2", "mean_bias"]})
    )
    segment_summary.to_csv(output_dir / "mean_segment_metrics.csv", index=False)

    baseline_fold_mae = fold_metrics[fold_metrics["model"] == "baseline_previous_week"].set_index("fold")["MAE"]
    baseline_low_mae = segment_metrics[
        (segment_metrics["model"] == "baseline_previous_week")
        & (segment_metrics["dimension"] == "activity_tier")
        & (segment_metrics["segment"] == "Low")
    ]["MAE"].mean()
    best_nonpoly_mae = float(
        mean_metrics.loc[mean_metrics["model"] == best_nonpoly, "mean_MAE"].iloc[0]
    )
    polynomial_improvement = None
    if polynomial_executed:
        polynomial_mae = float(
            mean_metrics.loc[mean_metrics["model"] == polynomial_name, "mean_MAE"].iloc[0]
        )
        polynomial_improvement = 100 * (best_nonpoly_mae - polynomial_mae) / best_nonpoly_mae
    gate_payload.update(
        {
            "polynomial_executed": polynomial_executed,
            "best_non_polynomial_mean_fold_MAE": best_nonpoly_mae,
            "polynomial_mean_fold_MAE": None if not polynomial_executed else polynomial_mae,
            "polynomial_improvement_vs_best_non_polynomial_pct": polynomial_improvement,
            "minimum_required_improvement_pct": float(modeling_config["model_selection"]["polynomial_minimum_mae_improvement_pct"]),
        }
    )
    write_json(output_dir / "polynomial_gate.json", gate_payload)

    complexity_rank = {
        "simple_linear_regression": 1,
        "multiple_linear_regression": 2,
        "ridge_regression": 3,
        "polynomial_regression_degree_2": 4,
    }
    candidate_rows: list[dict[str, Any]] = []
    for model_name in [name for name in model_names if MODEL_KIND[name] == "regression"]:
        row = mean_metrics.loc[mean_metrics["model"] == model_name].iloc[0]
        model_fold_mae = fold_metrics[fold_metrics["model"] == model_name].set_index("fold")["MAE"]
        wins = int((model_fold_mae < baseline_fold_mae).sum())
        low_mae = float(
            segment_metrics[
                (segment_metrics["model"] == model_name)
                & (segment_metrics["dimension"] == "activity_tier")
                & (segment_metrics["segment"] == "Low")
            ]["MAE"].mean()
        )
        complexity_explainable = True
        if model_name == polynomial_name:
            complexity_explainable = bool(
                gate_passed
                and polynomial_improvement is not None
                and polynomial_improvement
                >= float(modeling_config["model_selection"]["polynomial_minimum_mae_improvement_pct"])
            )
        passes_mean = bool(row["mean_MAE"] < previous_week_mae)
        passes_folds = wins >= 3
        passes_low = bool(
            low_mae
            <= baseline_low_mae
            * (1 + float(modeling_config["model_selection"]["low_tier_degradation_threshold_pct"]) / 100)
        )
        candidate_rows.append(
            {
                "model": model_name,
                "display_name": DISPLAY_NAMES[model_name],
                "mean_MAE": float(row["mean_MAE"]),
                "mean_RMSE": float(row["mean_RMSE"]),
                "uplift_vs_previous_week_pct": float(row["uplift_vs_previous_week_pct"]),
                "fold_MAE_wins_vs_previous_week": wins,
                "mean_low_tier_MAE": low_mae,
                "baseline_mean_low_tier_MAE": float(baseline_low_mae),
                "low_tier_MAE_ratio": low_mae / baseline_low_mae if baseline_low_mae else None,
                "passes_mean_MAE_rule": passes_mean,
                "passes_three_of_four_folds_rule": passes_folds,
                "passes_low_tier_rule": passes_low,
                "complexity_explainable": complexity_explainable,
                "qualified_to_replace_baseline": passes_mean and passes_folds and passes_low and complexity_explainable,
                "complexity_rank": complexity_rank[model_name],
            }
        )
    candidates = pd.DataFrame(candidate_rows)
    candidates.to_csv(output_dir / "model_selection_candidates.csv", index=False)
    qualified = candidates[candidates["qualified_to_replace_baseline"]].sort_values(
        ["mean_MAE", "complexity_rank", "mean_RMSE", "model"]
    )
    if len(qualified):
        selected_model = str(qualified.iloc[0]["model"])
        selection_reason = "lowest mean fold MAE among candidates passing every locked replacement rule"
    else:
        selected_model = "baseline_previous_week"
        selection_reason = "no regression candidate passed every locked replacement rule"

    selection = {
        "selected_model": selected_model,
        "selected_display_name": DISPLAY_NAMES[selected_model],
        "selection_reason": selection_reason,
        "primary_baseline": "baseline_previous_week",
        "primary_baseline_mean_fold_MAE": previous_week_mae,
        "selected_model_mean_fold_MAE": float(
            mean_metrics.loc[mean_metrics["model"] == selected_model, "mean_MAE"].iloc[0]
        ),
        "selected_model_uplift_vs_previous_week_pct": float(
            mean_metrics.loc[mean_metrics["model"] == selected_model, "uplift_vs_previous_week_pct"].iloc[0]
        ),
        "selected_ridge_alpha": selected_alpha,
        "polynomial_gate_passed": gate_passed,
        "polynomial_executed": polynomial_executed,
        "polynomial_retention_improvement_passed": bool(
            polynomial_executed
            and polynomial_improvement is not None
            and polynomial_improvement
            >= float(modeling_config["model_selection"]["polynomial_minimum_mae_improvement_pct"])
        ),
        "development_only": True,
        "final_test_rows_used": 0,
        "modeling_config_sha256": modeling_hash,
    }
    write_json(output_dir / "model_selection.json", selection)

    prediction_columns = [f"pred__{name}" for name in model_names]
    predictions[
        ["fold", "zone_id", "block_start", "borough", target, "activity_tier", "peak_period", "day_type", "target_state", *prediction_columns]
    ].to_parquet(output_dir / "validation_predictions.parquet", index=False, compression="zstd")
    pd.DataFrame(fit_records).to_csv(output_dir / "fit_diagnostics.csv", index=False)

    dpi = int(training_config["figure_dpi"])
    plt.style.use("seaborn-v0_8-whitegrid")
    plot_mean_mae(mean_metrics, selected_model, figures, dpi)
    plot_fold_mae(fold_metrics, model_names, figures, dpi)
    segment_comparison_model = selected_model if MODEL_KIND[selected_model] == "regression" else str(best_nonpoly)
    plot_low_tier(segment_metrics, segment_comparison_model, figures, dpi)
    plot_ridge_alpha(ridge_alpha_summary, selected_alpha, figures, dpi)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "development_rolling_validation_only",
        "modeling_config_sha256": modeling_hash,
        "validation_rows": int(len(predictions)),
        "fold_count": int(predictions["fold"].nunique()),
        "models_evaluated": model_names,
        "selected_ridge_alpha": selected_alpha,
        "polynomial_gate_passed": gate_passed,
        "polynomial_executed": polynomial_executed,
        "selected_model": selected_model,
        "selected_model_mean_fold_MAE": selection["selected_model_mean_fold_MAE"],
        "selected_model_uplift_vs_previous_week_pct": selection["selected_model_uplift_vs_previous_week_pct"],
        "final_test_rows_used": 0,
        "elapsed_seconds": time.perf_counter() - started,
        "versions": {
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "scikit_learn": __import__("sklearn").__version__,
            "matplotlib": matplotlib.__version__,
        },
    }
    write_json(output_dir / "modeling_run_summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
