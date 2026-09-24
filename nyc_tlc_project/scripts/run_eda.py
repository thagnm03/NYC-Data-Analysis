"""Run hypothesis-driven EDA on the sealed 2024-2025 development panel."""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EDA_CONFIG = PROJECT_ROOT / "config" / "eda_config.json"
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".mplconfig"))
os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TIER_COLORS = {"Low": "#4C78A8", "Medium": "#F2CF5B", "High": "#E45756"}
YEAR_COLORS = {2024: "#4C78A8", 2025: "#E45756"}


def resolve_from_config(config_path: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def load_eda_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "panel_path",
        "output_dir",
        "figure_dpi",
        "development_start",
        "development_end_exclusive",
        "activity_tier_labels",
        "lags_in_blocks",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise KeyError(f"EDA config is missing keys: {missing}")
    raw["panel_path"] = resolve_from_config(path, raw["panel_path"])
    raw["output_dir"] = resolve_from_config(path, raw["output_dir"])
    if len(raw["activity_tier_labels"]) != 3:
        raise ValueError("This project expects exactly three activity tiers")
    return raw


def write_json(path: Path, payload: dict[str, Any]) -> None:
    def safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [safe(item) for item in value]
        if isinstance(value, tuple):
            return [safe(item) for item in value]
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if hasattr(value, "item"):
            return safe(value.item())
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(safe(payload), indent=2), encoding="utf-8")
    temporary.replace(path)


def add_calendar(panel: pd.DataFrame) -> pd.DataFrame:
    frame = panel.copy()
    frame["block_start"] = pd.to_datetime(frame["block_start"])
    frame["date"] = frame["block_start"].dt.floor("D")
    frame["year"] = frame["block_start"].dt.year
    frame["month"] = frame["block_start"].dt.to_period("M").astype(str)
    frame["month_number"] = frame["block_start"].dt.month
    frame["day_of_week"] = frame["block_start"].dt.dayofweek
    frame["hour"] = frame["block_start"].dt.hour
    frame["is_weekend"] = frame["day_of_week"] >= 5
    return frame


def build_activity_tiers(panel: pd.DataFrame, labels: list[str]) -> pd.DataFrame:
    training = (
        panel[panel["year"] == 2024]
        .groupby(["zone_id", "borough", "zone_name"], as_index=False)
        .agg(training_trips=("trips", "sum"), training_zero_cells=("trips", lambda values: int((values == 0).sum())))
        .sort_values(["training_trips", "zone_id"])
    )
    training["activity_tier"] = pd.qcut(
        training["training_trips"].rank(method="first"),
        q=3,
        labels=labels,
    ).astype(str)
    return training


def temporal_tables(panel: pd.DataFrame, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    daily = panel.groupby("date", as_index=False).agg(trips=("trips", "sum"))
    daily["year"] = daily["date"].dt.year
    daily["day_of_week"] = daily["date"].dt.dayofweek
    daily["previous_week_trips"] = daily["trips"].shift(7)
    daily["change_vs_previous_week"] = daily["trips"] - daily["previous_week_trips"]
    daily["change_vs_previous_week_pct"] = 100 * daily["change_vs_previous_week"] / daily["previous_week_trips"]
    daily["rolling_28d_median"] = daily["trips"].rolling(28, center=True, min_periods=14).median()
    daily.to_csv(output_dir / "daily_demand.csv", index=False)

    monthly = (
        panel.groupby("month", as_index=False)
        .agg(
            trips=("trips", "sum"),
            panel_rows=("trips", "size"),
            zero_cells=("trips", lambda values: int((values == 0).sum())),
            calendar_days=("date", "nunique"),
        )
    )
    monthly["year"] = monthly["month"].str[:4].astype(int)
    monthly["month_number"] = monthly["month"].str[5:7].astype(int)
    monthly["daily_average_trips"] = monthly["trips"] / monthly["calendar_days"]
    monthly["zero_cell_pct"] = 100 * monthly["zero_cells"] / monthly["panel_rows"]
    monthly.to_csv(output_dir / "monthly_demand.csv", index=False)

    yearly = (
        daily.groupby("year", as_index=False)
        .agg(trips=("trips", "sum"), calendar_days=("date", "nunique"), daily_mean=("trips", "mean"), daily_median=("trips", "median"), daily_std=("trips", "std"))
    )
    yearly.to_csv(output_dir / "yearly_demand.csv", index=False)
    return daily, monthly, yearly


def spatial_tables(
    panel: pd.DataFrame,
    tiers: pd.DataFrame,
    output_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    by_year = panel.pivot_table(
        index=["zone_id", "borough", "zone_name"],
        columns="year",
        values="trips",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    by_year.columns.name = None
    by_year = by_year.rename(columns={2024: "trips_2024", 2025: "trips_2025"})
    overall = (
        panel.groupby(["zone_id", "borough", "zone_name"], as_index=False)
        .agg(
            trips=("trips", "sum"),
            mean_trips_per_cell=("trips", "mean"),
            std_trips_per_cell=("trips", "std"),
            zero_cells=("trips", lambda values: int((values == 0).sum())),
            panel_rows=("trips", "size"),
        )
    )
    overall["zero_cell_pct"] = 100 * overall["zero_cells"] / overall["panel_rows"]
    overall["coefficient_of_variation"] = overall["std_trips_per_cell"] / overall["mean_trips_per_cell"].replace(0, np.nan)
    zone_summary = overall.merge(by_year, on=["zone_id", "borough", "zone_name"], how="left")
    zone_summary = zone_summary.merge(tiers[["zone_id", "activity_tier", "training_trips"]], on="zone_id", how="left")
    zone_summary["year_over_year_change_pct"] = 100 * (
        zone_summary["trips_2025"] - zone_summary["trips_2024"]
    ) / zone_summary["trips_2024"].replace(0, np.nan)
    zone_summary = zone_summary.sort_values("trips", ascending=False).reset_index(drop=True)
    zone_summary["rank"] = np.arange(1, len(zone_summary) + 1)
    zone_summary["share"] = zone_summary["trips"] / zone_summary["trips"].sum()
    zone_summary["cumulative_share"] = zone_summary["share"].cumsum()
    zone_summary.to_csv(output_dir / "zone_summary.csv", index=False)

    borough = (
        panel.groupby(["year", "borough"], as_index=False)
        .agg(trips=("trips", "sum"), panel_rows=("trips", "size"), zero_cells=("trips", lambda values: int((values == 0).sum())), zones=("zone_id", "nunique"))
    )
    borough["share_within_year"] = borough["trips"] / borough.groupby("year")["trips"].transform("sum")
    borough["zero_cell_pct"] = 100 * borough["zero_cells"] / borough["panel_rows"]
    borough.to_csv(output_dir / "borough_summary.csv", index=False)

    panel_tiered = panel.merge(tiers[["zone_id", "activity_tier"]], on="zone_id", how="left")
    tier_profile = (
        panel_tiered.groupby(["year", "activity_tier"], as_index=False, observed=True)
        .agg(
            zones=("zone_id", "nunique"),
            panel_rows=("trips", "size"),
            trips=("trips", "sum"),
            mean_trips_per_cell=("trips", "mean"),
            median_trips_per_cell=("trips", "median"),
            zero_cells=("trips", lambda values: int((values == 0).sum())),
        )
    )
    tier_profile["zero_cell_pct"] = 100 * tier_profile["zero_cells"] / tier_profile["panel_rows"]
    tier_profile["share_within_year"] = tier_profile["trips"] / tier_profile.groupby("year")["trips"].transform("sum")
    tier_profile.to_csv(output_dir / "activity_tier_profile.csv", index=False)
    tiers.to_csv(output_dir / "activity_tiers.csv", index=False)
    return zone_summary, borough, tier_profile


def time_of_week_table(panel: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    frame = (
        panel.groupby(["year", "day_of_week", "hour"], as_index=False)
        .agg(mean_trips_per_zone_cell=("trips", "mean"), total_trips=("trips", "sum"), zero_cell_pct=("trips", lambda values: 100 * (values == 0).mean()))
    )
    frame["day_name"] = frame["day_of_week"].map(dict(enumerate(DAY_LABELS)))
    frame.to_csv(output_dir / "time_of_week_profile.csv", index=False)
    return frame


def lag_correlations(panel: pd.DataFrame, lags: dict[str, int], output_dir: Path) -> pd.DataFrame:
    ordered = panel.sort_values(["zone_id", "block_start"])[["zone_id", "block_start", "trips"]].copy()
    rows: list[dict[str, Any]] = []
    grouped = ordered.groupby("zone_id", sort=False)["trips"]
    for name, blocks in lags.items():
        lag_column = f"lag_{name}"
        ordered[lag_column] = grouped.shift(int(blocks))
        valid = ordered[["zone_id", "trips", lag_column]].dropna().copy()
        raw_correlation = valid["trips"].corr(valid[lag_column])
        valid["trips_centered"] = valid["trips"] - valid.groupby("zone_id")["trips"].transform("mean")
        valid["lag_centered"] = valid[lag_column] - valid.groupby("zone_id")[lag_column].transform("mean")
        centered_correlation = valid["trips_centered"].corr(valid["lag_centered"])
        def safe_zone_correlation(group: pd.DataFrame) -> float:
            if group["trips"].nunique() < 2 or group[lag_column].nunique() < 2:
                return np.nan
            return float(group["trips"].corr(group[lag_column]))

        zone_correlations = valid.groupby("zone_id").apply(
            safe_zone_correlation,
            include_groups=False,
        ).dropna()
        rows.append(
            {
                "lag": name,
                "blocks": int(blocks),
                "hours": int(blocks) * 4,
                "n_pairs": int(len(valid)),
                "zones_with_correlation": int(len(zone_correlations)),
                "pooled_raw_correlation": float(raw_correlation),
                "pooled_within_zone_centered_correlation": float(centered_correlation),
                "zone_correlation_p25": float(zone_correlations.quantile(0.25)),
                "zone_correlation_median": float(zone_correlations.median()),
                "zone_correlation_p75": float(zone_correlations.quantile(0.75)),
            }
        )
        ordered.drop(columns=[lag_column], inplace=True)
    result = pd.DataFrame(rows)
    result.to_csv(output_dir / "lag_correlations.csv", index=False)
    return result


def anomaly_table(daily: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    valid = daily.dropna(subset=["previous_week_trips"]).copy()
    valid["absolute_change_pct"] = valid["change_vs_previous_week_pct"].abs()
    anomalies = valid.nlargest(30, "absolute_change_pct")[
        ["date", "trips", "previous_week_trips", "change_vs_previous_week", "change_vs_previous_week_pct", "absolute_change_pct"]
    ].sort_values("absolute_change_pct", ascending=False)
    anomalies.to_csv(output_dir / "largest_daily_changes_vs_previous_week.csv", index=False)
    return anomalies


def plot_daily(daily: pd.DataFrame, figures: Path, dpi: int) -> None:
    fig, ax = plt.subplots(figsize=(12, 4.8))
    ax.plot(daily["date"], daily["trips"], color="#9AA0A6", linewidth=0.7, alpha=0.65, label="Daily trips")
    ax.plot(daily["date"], daily["rolling_28d_median"], color="#176B87", linewidth=2.0, label="Centered 28-day median")
    ax.set(title="Daily pickup volume changes materially across the two-year development period", xlabel="Date", ylabel="Recorded pickups per day")
    ax.legend(frameon=False, ncol=2)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "daily_demand.png", dpi=dpi)
    plt.close(fig)


def plot_monthly(monthly: pd.DataFrame, figures: Path, dpi: int) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.8))
    for year, group in monthly.groupby("year"):
        ax.plot(group["month_number"], group["daily_average_trips"], marker="o", linewidth=2, color=YEAR_COLORS[int(year)], label=str(year))
    ax.set(xticks=range(1, 13), xticklabels=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], title="Daily-average pickup volume is higher in 2025 than 2024", xlabel="Month", ylabel="Average recorded pickups per day")
    ax.legend(title="Year", frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "monthly_daily_average.png", dpi=dpi)
    plt.close(fig)


def plot_time_of_week(profile: pd.DataFrame, figures: Path, dpi: int) -> None:
    years = sorted(profile["year"].unique())
    vmax = profile["mean_trips_per_zone_cell"].max()
    fig, axes = plt.subplots(1, len(years), figsize=(12, 4.6), sharey=True)
    if len(years) == 1:
        axes = [axes]
    image = None
    for ax, year in zip(axes, years):
        pivot = profile[profile["year"] == year].pivot(index="day_of_week", columns="hour", values="mean_trips_per_zone_cell").reindex(index=range(7), columns=[0, 4, 8, 12, 16, 20])
        image = ax.imshow(pivot, aspect="auto", cmap="YlGnBu", vmin=0, vmax=vmax)
        ax.set(title=str(year), xlabel="4-hour block start", xticks=range(6), xticklabels=["00", "04", "08", "12", "16", "20"], yticks=range(7), yticklabels=DAY_LABELS)
    axes[0].set_ylabel("Day of week")
    fig.suptitle("Time-of-week structure repeats across years, with a higher 2025 level")
    fig.subplots_adjust(left=0.08, right=0.87, bottom=0.13, top=0.84, wspace=0.16)
    if image is not None:
        color_axis = fig.add_axes([0.90, 0.17, 0.014, 0.61])
        fig.colorbar(image, cax=color_axis, label="Mean pickups per zone-block")
    fig.savefig(figures / "time_of_week_heatmaps.png", dpi=dpi)
    plt.close(fig)


def plot_concentration(zone: pd.DataFrame, figures: Path, dpi: int) -> None:
    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.plot(zone["rank"], 100 * zone["cumulative_share"], color="#176B87", linewidth=2)
    for rank in (10, 20):
        value = 100 * zone.loc[zone["rank"] == rank, "cumulative_share"].iloc[0]
        ax.scatter([rank], [value], color="#D95F02", zorder=3)
        ax.annotate(f"Top {rank}: {value:.1f}%", (rank, value), xytext=(8, -14 if rank == 10 else 8), textcoords="offset points")
    zones_80 = int((zone["cumulative_share"] < 0.8).sum() + 1)
    ax.axhline(80, color="#777777", linewidth=1, linestyle="--")
    ax.annotate(f"{zones_80} zones reach 80%", (zones_80, 80), xytext=(8, -16), textcoords="offset points")
    ax.set(title="Pickup volume is concentrated in a minority of zones", xlabel="Zones ordered by total pickups", ylabel="Cumulative share of pickups (%)", xlim=(1, len(zone)), ylim=(0, 102))
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "zone_concentration.png", dpi=dpi)
    plt.close(fig)


def plot_top_zones(zone: pd.DataFrame, figures: Path, dpi: int) -> None:
    top = zone.head(20).sort_values("share")
    fig, ax = plt.subplots(figsize=(9, 6.5))
    ax.barh(top["zone_name"], 100 * top["share"], color="#4C78A8")
    ax.set(title="Top 20 pickup zones by two-year share", xlabel="Share of recorded pickups (%)", ylabel="")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "top20_zone_share.png", dpi=dpi)
    plt.close(fig)


def plot_tier_zero_rate(tier_profile: pd.DataFrame, figures: Path, dpi: int) -> None:
    labels = ["Low", "Medium", "High"]
    x = np.arange(len(labels))
    width = 0.36
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    for offset, year in zip((-width / 2, width / 2), sorted(tier_profile["year"].unique())):
        values = tier_profile[tier_profile["year"] == year].set_index("activity_tier").reindex(labels)["zero_cell_pct"]
        ax.bar(x + offset, values, width, label=str(year), color=YEAR_COLORS[int(year)])
    ax.set(xticks=x, xticklabels=labels, title="Sparse zone-blocks are concentrated in low-activity zones", xlabel="Activity tier fixed from 2024 training volume", ylabel="Zero-demand cells (%)")
    ax.legend(title="Year", frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "activity_tier_zero_rate.png", dpi=dpi)
    plt.close(fig)


def plot_year_comparison(zone: pd.DataFrame, figures: Path, dpi: int) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 6.2))
    for tier in ["Low", "Medium", "High"]:
        group = zone[zone["activity_tier"] == tier]
        ax.scatter(group["trips_2024"] + 1, group["trips_2025"] + 1, s=28, alpha=0.72, color=TIER_COLORS[tier], label=tier)
    minimum = min((zone["trips_2024"] + 1).min(), (zone["trips_2025"] + 1).min())
    maximum = max((zone["trips_2024"] + 1).max(), (zone["trips_2025"] + 1).max())
    ax.plot([minimum, maximum], [minimum, maximum], color="#666666", linewidth=1, linestyle="--", label="Equal volume")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set(title="Most zones record higher pickup volume in 2025", xlabel="2024 pickups + 1 (log scale)", ylabel="2025 pickups + 1 (log scale)")
    ax.legend(title="2024 activity tier", frameon=False)
    ax.grid(alpha=0.22, which="both")
    fig.tight_layout()
    fig.savefig(figures / "zone_year_comparison.png", dpi=dpi)
    plt.close(fig)


def plot_lag_correlations(lags: pd.DataFrame, figures: Path, dpi: int) -> None:
    labels = [value.replace("previous_", "").replace("_", " ") for value in lags["lag"]]
    y = np.arange(len(lags))
    median = lags["zone_correlation_median"].to_numpy()
    lower = median - lags["zone_correlation_p25"].to_numpy()
    upper = lags["zone_correlation_p75"].to_numpy() - median
    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.errorbar(median, y, xerr=np.vstack([lower, upper]), fmt="o", color="#176B87", ecolor="#7A9E9F", capsize=4, label="Median zone correlation (IQR)")
    ax.scatter(lags["pooled_within_zone_centered_correlation"], y, marker="D", color="#D95F02", label="Pooled within-zone correlation")
    ax.set(yticks=y, yticklabels=labels, title="Weekly and multi-week lags preserve the strongest within-zone signal", xlabel="Pearson correlation", ylabel="Historical lag")
    ax.invert_yaxis()
    ax.axvline(0, color="#777777", linewidth=0.8)
    ax.legend(frameon=False)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "lag_correlations.png", dpi=dpi)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_EDA_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config.resolve()
    config = load_eda_config(config_path)
    panel_path: Path = config["panel_path"]
    output_dir: Path = config["output_dir"]
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    if not panel_path.is_file():
        raise FileNotFoundError(f"Missing development panel: {panel_path}")

    panel = add_calendar(pd.read_parquet(panel_path))
    start = pd.Timestamp(config["development_start"])
    end = pd.Timestamp(config["development_end_exclusive"])
    if panel["block_start"].min() != start or panel["block_start"].max() >= end:
        raise RuntimeError("Panel falls outside the sealed development window")
    if (panel["year"] >= 2026).any():
        raise RuntimeError("Final-test year found in EDA input")

    tiers = build_activity_tiers(panel, config["activity_tier_labels"])
    daily, monthly, yearly = temporal_tables(panel, output_dir)
    zone, borough, tier_profile = spatial_tables(panel, tiers, output_dir)
    time_profile = time_of_week_table(panel, output_dir)
    lags = lag_correlations(panel, config["lags_in_blocks"], output_dir)
    anomalies = anomaly_table(daily, output_dir)

    dpi = int(config["figure_dpi"])
    plt.style.use("seaborn-v0_8-whitegrid")
    plot_daily(daily, figures, dpi)
    plot_monthly(monthly, figures, dpi)
    plot_time_of_week(time_profile, figures, dpi)
    plot_concentration(zone, figures, dpi)
    plot_top_zones(zone, figures, dpi)
    plot_tier_zero_rate(tier_profile, figures, dpi)
    plot_year_comparison(zone, figures, dpi)
    plot_lag_correlations(lags, figures, dpi)

    profiles = {
        year: time_profile[time_profile["year"] == year]
        .sort_values(["day_of_week", "hour"])["mean_trips_per_zone_cell"]
        .to_numpy()
        for year in sorted(time_profile["year"].unique())
    }
    profile_years = sorted(profiles)
    time_profile_correlation = float(np.corrcoef(profiles[profile_years[0]], profiles[profile_years[1]])[0, 1])
    top_10_share = float(zone.head(10)["share"].sum())
    top_20_share = float(zone.head(20)["share"].sum())
    zones_to_80 = int((zone["cumulative_share"] < 0.8).sum() + 1)
    year_rows = yearly.set_index("year")
    daily_mean_change_pct = float(100 * (year_rows.loc[2025, "daily_mean"] / year_rows.loc[2024, "daily_mean"] - 1))
    zero_by_year = panel.groupby("year")["trips"].apply(lambda values: 100 * (values == 0).mean())
    weekly = lags.loc[lags["lag"] == "previous_week"].iloc[0]
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "development_panel_only",
        "panel_rows": int(len(panel)),
        "zones": int(panel["zone_id"].nunique()),
        "time_blocks": int(panel["block_start"].nunique()),
        "target_sum": int(panel["trips"].sum()),
        "years": sorted(int(value) for value in panel["year"].unique()),
        "final_test_rows_used": 0,
        "activity_tier_counts": {str(key): int(value) for key, value in tiers["activity_tier"].value_counts().items()},
        "top_10_zone_share_pct": 100 * top_10_share,
        "top_20_zone_share_pct": 100 * top_20_share,
        "zones_to_reach_80_pct": zones_to_80,
        "zone_hhi": float((zone["share"] ** 2).sum()),
        "time_of_week_profile_correlation_2024_2025": time_profile_correlation,
        "daily_mean_change_2025_vs_2024_pct": daily_mean_change_pct,
        "zero_cell_pct_by_year": {str(int(key)): float(value) for key, value in zero_by_year.items()},
        "previous_week_lag": {
            "pooled_raw_correlation": float(weekly["pooled_raw_correlation"]),
            "pooled_within_zone_centered_correlation": float(weekly["pooled_within_zone_centered_correlation"]),
            "median_zone_correlation": float(weekly["zone_correlation_median"]),
        },
        "largest_daily_change_vs_previous_week": anomalies.iloc[0].to_dict(),
        "output_tables": sorted(path.name for path in output_dir.glob("*.csv")),
        "output_figures": sorted(path.name for path in figures.glob("*.png")),
    }
    write_json(output_dir / "eda_summary.json", summary)
    write_json(
        output_dir / "eda_run_metadata.json",
        {
            "generated_at_utc": summary["generated_at_utc"],
            "config_file": str(config_path),
            "panel_file": str(panel_path),
            "panel_file_bytes": panel_path.stat().st_size,
            "pandas_version": pd.__version__,
            "numpy_version": np.__version__,
            "matplotlib_version": matplotlib.__version__,
        },
    )
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
