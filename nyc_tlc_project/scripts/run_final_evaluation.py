"""Run the frozen Step 8 final evaluation and error analysis on Jan-May 2026."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from build_final_test import DEFAULT_FINAL_CONFIG, load_final_config
from build_modeling_frame import load_modeling_config, write_json
from run_models import (
    DISPLAY_NAMES,
    assign_fold_segments,
    calculate_metrics,
    clip_predictions,
    load_training_config,
    make_preprocessor,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".mplconfig"))
os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


FINAL_METHODS = [
    "baseline_previous_block",
    "baseline_previous_day",
    "baseline_previous_week",
    "baseline_seasonal_mean_4w",
    "ridge_regression",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metric_table(predictions: pd.DataFrame, target: str, methods: list[str]) -> pd.DataFrame:
    rows = []
    for method in methods:
        rows.append(
            {
                "method": method,
                "display_name": DISPLAY_NAMES[method],
                **calculate_metrics(predictions[target].to_numpy(), predictions[f"pred__{method}"].to_numpy()),
            }
        )
    return pd.DataFrame(rows).sort_values("MAE").reset_index(drop=True)


def segment_table(predictions: pd.DataFrame, target: str, methods: list[str]) -> pd.DataFrame:
    dimensions = ["month", "activity_tier", "peak_period", "day_type", "borough", "target_state", "block_hour"]
    records: list[dict[str, Any]] = []
    for dimension in dimensions:
        for segment, group in predictions.groupby(dimension, sort=True):
            for method in methods:
                records.append(
                    {
                        "dimension": dimension,
                        "segment": str(segment),
                        "method": method,
                        "display_name": DISPLAY_NAMES[method],
                        **calculate_metrics(group[target].to_numpy(), group[f"pred__{method}"].to_numpy()),
                    }
                )
    return pd.DataFrame(records)


def plot_final_mae(metrics: pd.DataFrame, formal: str, figures: Path, dpi: int) -> None:
    ordered = metrics.sort_values("MAE", ascending=True)
    empirical_best = str(ordered.iloc[0]["method"])
    colors = [
        "#D95F02" if method == formal else "#54A24B" if method == empirical_best else "#9AA0A6"
        for method in ordered["method"]
    ]
    fig, ax = plt.subplots(figsize=(9.0, 4.8))
    bars = ax.barh(ordered["display_name"], ordered["MAE"], color=colors)
    ax.bar_label(bars, labels=[f"{value:.2f}" for value in ordered["MAE"]], padding=4)
    ax.set(title="Final-test MAE for frozen methods", xlabel="MAE (pickups per zone-block; lower is better)", ylabel="")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "final_mae_comparison.png", dpi=dpi)
    plt.close(fig)


def plot_monthly_mae(segments: pd.DataFrame, methods: list[str], figures: Path, dpi: int) -> None:
    focus = segments[(segments["dimension"] == "month") & segments["method"].isin(methods)].copy()
    colors = ["#4C78A8", "#F2CF5B", "#E45756"]
    fig, ax = plt.subplots(figsize=(9.2, 4.9))
    for method, color in zip(methods, colors):
        group = focus[focus["method"] == method].sort_values("segment")
        ax.plot(group["segment"], group["MAE"], marker="o", linewidth=2, color=color, label=DISPLAY_NAMES[method])
    ax.set(title="Final-test MAE by month", xlabel="Final-test month", ylabel="MAE (pickups per zone-block)")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "final_monthly_mae.png", dpi=dpi)
    plt.close(fig)


def plot_tier_mae(segments: pd.DataFrame, methods: list[str], figures: Path, dpi: int) -> None:
    focus = segments[(segments["dimension"] == "activity_tier") & segments["method"].isin(methods)]
    tiers = ["Low", "Medium", "High"]
    x = np.arange(len(tiers))
    width = 0.25
    colors = ["#4C78A8", "#F2CF5B", "#E45756"]
    fig, ax = plt.subplots(figsize=(9.0, 5.2))
    for position, (method, color) in enumerate(zip(methods, colors)):
        values = focus[focus["method"] == method].set_index("segment").reindex(tiers)["MAE"]
        bars = ax.bar(x + (position - 1) * width, values, width, color=color, label=DISPLAY_NAMES[method])
        ax.bar_label(bars, labels=[f"{value:.2f}" for value in values], padding=3)
    ax.set(xticks=x, xticklabels=tiers, title="Final-test error remains strongly segmented by zone activity", xlabel="Activity tier fixed from all development training data", ylabel="MAE")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "final_activity_tier_mae.png", dpi=dpi)
    plt.close(fig)


def plot_daily_error(daily: pd.DataFrame, figures: Path, dpi: int) -> None:
    fig, ax = plt.subplots(figsize=(11.0, 4.8))
    ax.plot(daily["date"], daily["selected_MAE"], linewidth=0.9, color="#9AA0A6", alpha=0.75, label="Daily MAE")
    ax.plot(daily["date"], daily["selected_MAE_14d_median"], linewidth=2.2, color="#D95F02", label="Trailing 14-day median")
    ax.set(title="Daily error of the formal previous-week method", xlabel="Date", ylabel="MAE across 261 zones × 6 blocks")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "final_daily_error.png", dpi=dpi)
    plt.close(fig)


def plot_calibration(calibration: pd.DataFrame, figures: Path, dpi: int) -> None:
    maximum = float(max(calibration["mean_prediction"].max(), calibration["mean_actual"].max()))
    fig, ax = plt.subplots(figsize=(6.5, 5.8))
    ax.plot([0, maximum], [0, maximum], color="#777777", linestyle="--", linewidth=1, label="Perfect agreement")
    ax.plot(calibration["mean_prediction"], calibration["mean_actual"], marker="o", linewidth=2, color="#4C78A8", label="Previous-week deciles")
    for _, row in calibration.loc[calibration["prediction_decile"].isin([1, 5, 10])].iterrows():
        ax.annotate(str(int(row["prediction_decile"])), (row["mean_prediction"], row["mean_actual"]), xytext=(5, 4), textcoords="offset points")
    ax.set(title="Calibration by prediction decile", xlabel="Mean predicted pickups", ylabel="Mean actual pickups")
    ax.legend(frameon=False)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "final_calibration_deciles.png", dpi=dpi)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_FINAL_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    config_path = args.config.resolve()
    config = load_final_config(config_path)
    modeling_config = load_modeling_config(config["modeling_config_path"])
    training_config = load_training_config(config["training_config_path"])
    selection = json.loads(config["model_selection_path"].read_text(encoding="utf-8"))
    build_audit = json.loads((config["build_output_dir"] / "final_build_audit.json").read_text(encoding="utf-8"))
    if not build_audit.get("success"):
        raise RuntimeError("Final-test build audit did not pass")
    if sha256(config["modeling_config_path"]) != config["expected_modeling_config_sha256"]:
        raise RuntimeError("Modeling config checksum changed")
    if sha256(config["training_config_path"]) != config["expected_training_config_sha256"]:
        raise RuntimeError("Training config checksum changed")
    if sha256(config["model_selection_path"]) != config["expected_model_selection_sha256"]:
        raise RuntimeError("Frozen model selection checksum changed")
    if selection["selected_model"] != config["formal_selected_method"]:
        raise RuntimeError("Frozen selected method changed")
    if float(selection["selected_ridge_alpha"]) != float(config["frozen_ridge_alpha"]):
        raise RuntimeError("Frozen Ridge alpha changed")

    output_dir: Path = config["evaluation_output_dir"]
    figures = output_dir / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    development = pd.read_parquet(modeling_config["modeling_frame_path"])
    final = pd.read_parquet(config["final_modeling_frame_path"])
    development["block_start"] = pd.to_datetime(development["block_start"])
    final["block_start"] = pd.to_datetime(final["block_start"])
    final_start = pd.Timestamp(config["final_test_start"])
    final_end = pd.Timestamp(config["final_test_end_exclusive"])
    if final["block_start"].min() != final_start or final["block_start"].max() >= final_end:
        raise RuntimeError("Final modeling frame is outside the authorized test period")
    target = modeling_config["target"]
    train = development[development["features_ready"]].copy()
    segments = assign_fold_segments(train, final, target)
    predictions = final[["zone_id", "zone_name", "block_start", "block_hour", target]].copy()
    predictions = predictions.merge(
        segments[["zone_id", "block_start", "activity_tier", "peak_period", "day_type", "target_state"]],
        on=["zone_id", "block_start"], how="left", validate="one_to_one"
    )
    predictions["borough"] = final["borough"].to_numpy()
    predictions["month"] = predictions["block_start"].dt.to_period("M").astype(str)

    baseline_sources = {
        "baseline_previous_block": "lag_1",
        "baseline_previous_day": "lag_6",
        "baseline_previous_week": "lag_42",
        "baseline_seasonal_mean_4w": "seasonal_mean_4w",
    }
    for method, source in baseline_sources.items():
        predictions[f"pred__{method}"] = clip_predictions(final[source].to_numpy())

    ridge_spec = next(model for model in modeling_config["models"] if model["name"] == "ridge_regression")
    preprocessor = make_preprocessor(
        ridge_spec["categorical_features"], ridge_spec["numeric_features"], scale_numeric=True
    )
    tick = time.perf_counter()
    x_train = preprocessor.fit_transform(train)
    x_final = preprocessor.transform(final)
    ridge = Ridge(
        alpha=float(config["frozen_ridge_alpha"]),
        solver=training_config["ridge_solver"],
        tol=float(training_config["ridge_tolerance"]),
        max_iter=int(training_config["ridge_max_iterations"]),
    )
    ridge.fit(x_train, train[target].to_numpy(dtype="float64"))
    predictions["pred__ridge_regression"] = clip_predictions(ridge.predict(x_final))
    ridge_fit_seconds = time.perf_counter() - tick

    metrics = metric_table(predictions, target, FINAL_METHODS)
    metrics.to_csv(output_dir / "final_metrics.csv", index=False)
    segment_metrics = segment_table(predictions, target, FINAL_METHODS)
    segment_metrics.to_csv(output_dir / "final_segment_metrics.csv", index=False)

    formal = config["formal_selected_method"]
    secondary = config["secondary_prespecified_benchmark"]
    diagnostic = config["diagnostic_regression"]
    formal_prediction = f"pred__{formal}"
    predictions["formal_error"] = predictions[formal_prediction] - predictions[target]
    predictions["formal_absolute_error"] = predictions["formal_error"].abs()
    predictions["date"] = predictions["block_start"].dt.floor("D")
    predictions["hour"] = predictions["block_start"].dt.hour

    daily = (
        predictions.groupby("date", as_index=False)
        .agg(
            actual_pickups=(target, "sum"),
            selected_prediction=(formal_prediction, "sum"),
            selected_MAE=("formal_absolute_error", "mean"),
            selected_absolute_error_sum=("formal_absolute_error", "sum"),
            selected_mean_bias=("formal_error", "mean"),
        )
    )
    daily["selected_WAPE_pct"] = 100 * daily["selected_absolute_error_sum"] / daily["actual_pickups"]
    daily["selected_MAE_14d_median"] = daily["selected_MAE"].rolling(14, min_periods=1).median()
    daily.to_csv(output_dir / "final_daily_error.csv", index=False)

    zone = (
        predictions.groupby(["zone_id", "zone_name", "borough", "activity_tier"], as_index=False)
        .agg(
            rows=(target, "size"),
            actual_pickups=(target, "sum"),
            zero_rate=(target, lambda values: float((values == 0).mean())),
            formal_MAE=("formal_absolute_error", "mean"),
            formal_RMSE=("formal_error", lambda values: float(np.sqrt(np.mean(np.square(values))))),
            formal_mean_bias=("formal_error", "mean"),
        )
    )
    for method in (secondary, diagnostic):
        absolute_error = (predictions[f"pred__{method}"] - predictions[target]).abs()
        method_mae = absolute_error.groupby(predictions["zone_id"]).mean()
        zone[f"{method}_MAE"] = zone["zone_id"].map(method_mae)
    zone["formal_WAPE_pct"] = 100 * zone["formal_MAE"] * zone["rows"] / zone["actual_pickups"].replace(0, np.nan)
    zone.sort_values("formal_MAE", ascending=False).to_csv(output_dir / "final_zone_error.csv", index=False)

    worst = predictions.nlargest(50, "formal_absolute_error")[
        ["zone_id", "zone_name", "borough", "block_start", target, formal_prediction, "formal_error", "formal_absolute_error", "activity_tier", "peak_period"]
    ]
    worst.to_csv(output_dir / "final_worst_zone_blocks.csv", index=False)
    daily.nlargest(20, "selected_MAE").to_csv(output_dir / "final_worst_days.csv", index=False)

    ranks = predictions[formal_prediction].rank(method="first")
    predictions["prediction_decile"] = pd.qcut(ranks, q=10, labels=False) + 1
    calibration = (
        predictions.groupby("prediction_decile", as_index=False)
        .agg(
            rows=(target, "size"),
            mean_prediction=(formal_prediction, "mean"),
            mean_actual=(target, "mean"),
            MAE=("formal_absolute_error", "mean"),
            mean_bias=("formal_error", "mean"),
        )
    )
    calibration.to_csv(output_dir / "final_calibration_deciles.csv", index=False)

    development_metrics = pd.read_csv(training_config["output_dir"] / "mean_fold_metrics.csv")
    comparison = metrics.merge(
        development_metrics[["model", "mean_MAE", "mean_RMSE", "mean_WAPE_pct"]],
        left_on="method", right_on="model", how="left"
    )
    comparison["final_minus_development_MAE"] = comparison["MAE"] - comparison["mean_MAE"]
    comparison["final_MAE_change_pct"] = 100 * comparison["final_minus_development_MAE"] / comparison["mean_MAE"]
    comparison.to_csv(output_dir / "development_vs_final_metrics.csv", index=False)

    empirical_best = str(metrics.iloc[0]["method"])
    frozen_decision = {
        "formal_selected_method_before_final_test": formal,
        "formal_selected_method_after_final_test": formal,
        "selection_changed": False,
        "secondary_prespecified_benchmark": secondary,
        "diagnostic_regression": diagnostic,
        "empirical_lowest_final_MAE_method_descriptive_only": empirical_best,
        "final_test_not_used_for_reselection": True,
        "modeling_config_sha256": sha256(config["modeling_config_path"]),
        "training_config_sha256": sha256(config["training_config_path"]),
        "model_selection_sha256": sha256(config["model_selection_path"]),
    }
    write_json(output_dir / "frozen_decision.json", frozen_decision)

    predictions[
        [
            "zone_id", "zone_name", "borough", "block_start", target, "month", "activity_tier", "peak_period", "day_type", "target_state",
            *[f"pred__{method}" for method in FINAL_METHODS], "formal_error", "formal_absolute_error",
        ]
    ].to_parquet(output_dir / "final_predictions.parquet", index=False, compression="zstd")

    dpi = int(config["figure_dpi"])
    plt.style.use("seaborn-v0_8-whitegrid")
    focus_methods = [formal, secondary, diagnostic]
    plot_final_mae(metrics, formal, figures, dpi)
    plot_monthly_mae(segment_metrics, focus_methods, figures, dpi)
    plot_tier_mae(segment_metrics, focus_methods, figures, dpi)
    plot_daily_error(daily, figures, dpi)
    plot_calibration(calibration, figures, dpi)

    formal_row = metrics[metrics["method"] == formal].iloc[0]
    secondary_row = metrics[metrics["method"] == secondary].iloc[0]
    diagnostic_row = metrics[metrics["method"] == diagnostic].iloc[0]
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "frozen_final_test_2026_01_through_2026_05",
        "test_rows": int(len(predictions)),
        "test_target_sum": int(predictions[target].sum()),
        "formal_selected_method": formal,
        "formal_selected_method_MAE": float(formal_row["MAE"]),
        "formal_selected_method_RMSE": float(formal_row["RMSE"]),
        "formal_selected_method_WAPE_pct": float(formal_row["WAPE_pct"]),
        "secondary_benchmark": secondary,
        "secondary_benchmark_MAE": float(secondary_row["MAE"]),
        "diagnostic_regression": diagnostic,
        "diagnostic_regression_MAE": float(diagnostic_row["MAE"]),
        "frozen_ridge_alpha": float(config["frozen_ridge_alpha"]),
        "ridge_training_rows": int(len(train)),
        "ridge_transformed_features": int(x_train.shape[1]),
        "empirical_lowest_final_MAE_method_descriptive_only": empirical_best,
        "frozen_decision_changed": False,
        "ridge_fit_seconds": ridge_fit_seconds,
        "elapsed_seconds": time.perf_counter() - started,
    }
    write_json(output_dir / "final_evaluation_summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
