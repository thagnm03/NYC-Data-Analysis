"""Run development-only data understanding and quality audit for NYC TLC.

This script intentionally excludes the configured 2026 final-test files. It
profiles each development-month file independently, which distinguishes a
structurally absent column from a present-but-null column and makes file-month
timestamp contamination explicit.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from download_data import DEFAULT_CONFIG, load_config, month_range


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_CONFIG = PROJECT_ROOT / "config" / "audit_config.json"


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_path(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def next_month(month: str) -> str:
    year, number = map(int, month.split("-"))
    index = year * 12 + number
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def resolve_from_config(config_path: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def load_audit_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "development_start_month",
        "development_end_month",
        "output_dir",
        "interim_dir",
        "duckdb_threads",
        "duckdb_memory_limit",
        "valid_pickup_zone_min",
        "valid_pickup_zone_max",
        "semantic_zone_buckets",
        "duration_extreme_hours",
        "distance_extreme_miles",
        "passenger_count_extreme",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise KeyError(f"Audit config is missing keys: {missing}")
    month_range(raw["development_start_month"], raw["development_end_month"])
    raw["output_dir"] = resolve_from_config(path, raw["output_dir"])
    raw["interim_dir"] = resolve_from_config(path, raw["interim_dir"])
    return raw


def schema_profile(files: list[tuple[str, Path]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    variant_rows: list[dict[str, Any]] = []
    seen_variants: dict[tuple[tuple[str, str], ...], str] = {}
    for month, path in files:
        schema = pq.ParquetFile(path).schema_arrow
        signature_tuple = tuple((field.name, str(field.type)) for field in schema)
        if signature_tuple not in seen_variants:
            seen_variants[signature_tuple] = f"schema_{len(seen_variants) + 1}"
            variant_rows.append(
                {
                    "schema_variant": seen_variants[signature_tuple],
                    "first_month": month,
                    "column_count": len(schema),
                    "columns": " | ".join(field.name for field in schema),
                }
            )
        variant = seen_variants[signature_tuple]
        for position, field in enumerate(schema):
            rows.append(
                {
                    "month": month,
                    "schema_variant": variant,
                    "position": position,
                    "column": field.name,
                    "arrow_type": str(field.type),
                    "nullable": field.nullable,
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(variant_rows)


def audit_one_month(
    connection: duckdb.DuckDBPyConnection,
    month: str,
    path: Path,
    columns: list[str],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    month_start = f"{month}-01"
    month_end = f"{next_month(month)}-01"
    identifiers = ", ".join(quote_identifier(column) for column in columns)
    null_expressions = [
        f"count_if({quote_identifier(column)} IS NULL)::BIGINT AS {quote_identifier('null__' + column)}"
        for column in columns
    ]
    zone_min = int(config["valid_pickup_zone_min"])
    zone_max = int(config["valid_pickup_zone_max"])
    semantic = ", ".join(str(int(value)) for value in config["semantic_zone_buckets"])
    duration_seconds = int(float(config["duration_extreme_hours"]) * 3600)
    distance_extreme = float(config["distance_extreme_miles"])
    passenger_extreme = float(config["passenger_count_extreme"])

    fixed_expressions = [
        "count(*)::BIGINT AS rows",
        f"(count(*) - count(DISTINCT hash({identifiers})))::BIGINT AS hash_duplicate_rows",
        "min(tpep_pickup_datetime) AS pickup_min",
        "max(tpep_pickup_datetime) AS pickup_max",
        "min(tpep_dropoff_datetime) AS dropoff_min",
        "max(tpep_dropoff_datetime) AS dropoff_max",
        "count_if(tpep_dropoff_datetime < tpep_pickup_datetime)::BIGINT AS dropoff_before_pickup",
        "count_if(date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime) <= 0)::BIGINT AS duration_nonpositive",
        f"count_if(date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime) > {duration_seconds})::BIGINT AS duration_over_threshold",
        f"count_if(tpep_pickup_datetime < TIMESTAMP '{month_start}' OR tpep_pickup_datetime >= TIMESTAMP '{month_end}')::BIGINT AS pickup_outside_file_month",
        "count_if(PULocationID NOT BETWEEN 1 AND 265)::BIGINT AS pu_zone_outside_1_265",
        "count_if(DOLocationID NOT BETWEEN 1 AND 265)::BIGINT AS do_zone_outside_1_265",
        f"count_if(PULocationID IN ({semantic}))::BIGINT AS pu_semantic_bucket",
        f"count_if(DOLocationID IN ({semantic}))::BIGINT AS do_semantic_bucket",
        f"count_if(PULocationID IS NULL OR PULocationID NOT BETWEEN {zone_min} AND {zone_max})::BIGINT AS pu_not_modeling_zone",
        "count_if(trip_distance <= 0)::BIGINT AS distance_nonpositive",
        f"count_if(trip_distance > {distance_extreme})::BIGINT AS distance_over_threshold",
        "count_if(fare_amount < 0)::BIGINT AS fare_negative",
        "count_if(total_amount < 0)::BIGINT AS total_negative",
        "count_if(passenger_count <= 0)::BIGINT AS passenger_nonpositive",
        f"count_if(passenger_count > {passenger_extreme})::BIGINT AS passenger_over_threshold",
        f"count_if(tpep_pickup_datetime >= TIMESTAMP '{month_start}' AND tpep_pickup_datetime < TIMESTAMP '{month_end}' AND PULocationID BETWEEN {zone_min} AND {zone_max})::BIGINT AS target_valid_rows",
    ]
    query = (
        "SELECT\n  "
        + ",\n  ".join(fixed_expressions + null_expressions)
        + f"\nFROM read_parquet({quote_path(path)})"
    )
    row = connection.execute(query).df().iloc[0].to_dict()
    missingness: list[dict[str, Any]] = []
    for column in columns:
        null_count = int(row.pop(f"null__{column}"))
        missingness.append(
            {
                "month": month,
                "column": column,
                "rows_applicable": int(row["rows"]),
                "null_count": null_count,
                "null_pct": 100 * null_count / int(row["rows"]),
            }
        )
    row["month"] = month
    row["file"] = path.name
    row["target_excluded_rows"] = int(row["rows"]) - int(row["target_valid_rows"])
    row["target_excluded_pct"] = 100 * row["target_excluded_rows"] / int(row["rows"])
    return row, missingness


def overall_missingness(monthly_missingness: pd.DataFrame, schema: pd.DataFrame, total_months: int) -> pd.DataFrame:
    grouped = (
        monthly_missingness.groupby("column", as_index=False)
        .agg(
            months_present=("month", "nunique"),
            rows_applicable=("rows_applicable", "sum"),
            null_count=("null_count", "sum"),
        )
        .sort_values("column")
    )
    grouped["months_absent"] = total_months - grouped["months_present"]
    grouped["null_pct_when_present"] = 100 * grouped["null_count"] / grouped["rows_applicable"]
    first_last = schema.groupby("column")["month"].agg(first_month="min", last_month="max").reset_index()
    return grouped.merge(first_last, on="column", how="left")


def categorical_counts(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    frames = []
    for column in ["VendorID", "RatecodeID", "store_and_fwd_flag", "payment_type", "passenger_count"]:
        identifier = quote_identifier(column)
        frame = connection.execute(
            f"""
            SELECT {column!r} AS column,
                   coalesce(CAST({identifier} AS VARCHAR), '<NULL>') AS value,
                   count(*)::BIGINT AS rows
            FROM dev_raw
            GROUP BY {identifier}
            ORDER BY rows DESC
            """
        ).df()
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    result["share_pct_within_column"] = result.groupby("column")["rows"].transform(lambda values: 100 * values / values.sum())
    return result


def numeric_summary(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    expressions = {
        "passenger_count": "CAST(passenger_count AS DOUBLE)",
        "trip_distance_miles": "CAST(trip_distance AS DOUBLE)",
        "duration_minutes": "date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime) / 60.0",
        "fare_amount": "CAST(fare_amount AS DOUBLE)",
        "total_amount": "CAST(total_amount AS DOUBLE)",
    }
    rows: list[dict[str, Any]] = []
    for variable, expression in expressions.items():
        result = connection.execute(
            f"""
            SELECT
              count({expression})::BIGINT AS non_null_count,
              avg({expression})::DOUBLE AS mean,
              stddev_samp({expression})::DOUBLE AS std,
              min({expression})::DOUBLE AS min,
              approx_quantile({expression}, 0.01)::DOUBLE AS approx_p01,
              approx_quantile({expression}, 0.25)::DOUBLE AS approx_p25,
              approx_quantile({expression}, 0.50)::DOUBLE AS approx_median,
              approx_quantile({expression}, 0.75)::DOUBLE AS approx_p75,
              approx_quantile({expression}, 0.99)::DOUBLE AS approx_p99,
              max({expression})::DOUBLE AS max
            FROM dev_raw
            """
        ).df().iloc[0].to_dict()
        result["variable"] = variable
        rows.append(result)
    columns = [
        "variable",
        "non_null_count",
        "mean",
        "std",
        "min",
        "approx_p01",
        "approx_p25",
        "approx_median",
        "approx_p75",
        "approx_p99",
        "max",
    ]
    return pd.DataFrame(rows)[columns]


def vendor_quality(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    frame = connection.execute(
        """
        SELECT
          coalesce(CAST(VendorID AS VARCHAR), '<NULL>') AS vendor_id,
          count(*)::BIGINT AS rows,
          count_if(passenger_count IS NULL)::BIGINT AS passenger_null,
          count_if(
            passenger_count IS NULL AND RatecodeID IS NULL AND
            store_and_fwd_flag IS NULL AND congestion_surcharge IS NULL AND
            Airport_fee IS NULL
          )::BIGINT AS five_field_null_bundle,
          count_if(payment_type = 0)::BIGINT AS payment_type_zero,
          count_if(fare_amount < 0)::BIGINT AS fare_negative,
          count_if(total_amount < 0)::BIGINT AS total_negative,
          count_if(date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime) <= 0)::BIGINT AS duration_nonpositive,
          count_if(trip_distance <= 0)::BIGINT AS distance_nonpositive
        FROM dev_raw
        GROUP BY VendorID
        ORDER BY rows DESC
        """
    ).df()
    for column in frame.columns:
        if column not in {"vendor_id", "rows"}:
            frame[column + "_pct"] = 100 * frame[column] / frame["rows"]
    return frame


def missing_bundle_summary(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    result = connection.execute(
        """
        SELECT
          count_if(
            passenger_count IS NULL AND RatecodeID IS NULL AND
            store_and_fwd_flag IS NULL AND congestion_surcharge IS NULL AND
            Airport_fee IS NULL
          )::BIGINT AS all_five_null,
          count_if(
            (passenger_count IS NULL)::INTEGER +
            (RatecodeID IS NULL)::INTEGER +
            (store_and_fwd_flag IS NULL)::INTEGER +
            (congestion_surcharge IS NULL)::INTEGER +
            (Airport_fee IS NULL)::INTEGER BETWEEN 1 AND 4
          )::BIGINT AS partial_bundle,
          count_if(payment_type = 0 AND passenger_count IS NULL)::BIGINT AS payment_zero_and_bundle,
          count_if(payment_type = 0 AND passenger_count IS NOT NULL)::BIGINT AS payment_zero_without_bundle,
          count_if(payment_type <> 0 AND passenger_count IS NULL)::BIGINT AS bundle_without_payment_zero
        FROM dev_raw
        """
    ).df().iloc[0]
    return pd.DataFrame(
        [{"metric": metric, "rows": int(value)} for metric, value in result.items()]
    )


def verify_duplicate_groups(
    connection: duckdb.DuckDBPyConnection,
    files: list[tuple[str, Path]],
    monthly: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    zone_min = int(config["valid_pickup_zone_min"])
    zone_max = int(config["valid_pickup_zone_max"])
    duplicate_months = set(monthly.loc[monthly["hash_duplicate_rows"] > 0, "month"])
    for month, path in files:
        if month not in duplicate_months:
            continue
        columns = [field.name for field in pq.ParquetFile(path).schema_arrow]
        identifiers = ", ".join(quote_identifier(column) for column in columns)
        month_start = f"{month}-01"
        month_end = f"{next_month(month)}-01"
        result = connection.execute(
            f"""
            WITH duplicate_groups AS (
              SELECT
                count(*)::BIGINT AS group_size,
                bool_and(
                  tpep_pickup_datetime >= TIMESTAMP '{month_start}' AND
                  tpep_pickup_datetime < TIMESTAMP '{month_end}' AND
                  PULocationID BETWEEN {zone_min} AND {zone_max}
                ) AS target_valid
              FROM read_parquet({quote_path(path)})
              GROUP BY {identifiers}
              HAVING count(*) > 1
            )
            SELECT
              count(*)::BIGINT AS duplicate_groups,
              coalesce(sum(group_size - 1), 0)::BIGINT AS exact_duplicate_rows,
              coalesce(sum(CASE WHEN target_valid THEN group_size - 1 ELSE 0 END), 0)::BIGINT
                AS target_valid_exact_duplicate_rows
            FROM duplicate_groups
            """
        ).df().iloc[0].to_dict()
        result["month"] = month
        rows.append(result)
    return pd.DataFrame(rows, columns=[
        "month",
        "duplicate_groups",
        "exact_duplicate_rows",
        "target_valid_exact_duplicate_rows",
    ])


def zone_lookup_audit(raw_dir: Path) -> dict[str, Any]:
    path = raw_dir / "taxi_zone_lookup.csv"
    frame = pd.read_csv(path)
    ids = pd.to_numeric(frame["LocationID"], errors="coerce")
    return {
        "file": str(path),
        "rows": int(len(frame)),
        "columns": frame.columns.tolist(),
        "location_id_null": int(ids.isna().sum()),
        "location_id_duplicates": int(ids.duplicated().sum()),
        "location_id_min": int(ids.min()),
        "location_id_max": int(ids.max()),
        "contains_264_unknown": bool((ids == 264).any()),
        "contains_265_outside_nyc": bool((ids == 265).any()),
        "borough_counts": {str(key): int(value) for key, value in frame["Borough"].value_counts(dropna=False).items()},
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return json_safe(value.item())
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(json_safe(payload), indent=2), encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--audit-config", type=Path, default=DEFAULT_AUDIT_CONFIG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    project_config_path = args.project_config.resolve()
    audit_config_path = args.audit_config.resolve()
    project_config = load_config(project_config_path)
    audit_config = load_audit_config(audit_config_path)
    months = month_range(
        audit_config["development_start_month"],
        audit_config["development_end_month"],
    )
    files = [
        (month, project_config.raw_dir / f"yellow_tripdata_{month}.parquet")
        for month in months
    ]
    missing = [str(path) for _, path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing development files: {missing}")

    output_dir: Path = audit_config["output_dir"]
    interim_dir: Path = audit_config["interim_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    interim_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = interim_dir / "duckdb_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    schema, schema_variants = schema_profile(files)
    schema.to_csv(output_dir / "schema_by_month.csv", index=False)
    schema_variants.to_csv(output_dir / "schema_variants.csv", index=False)

    connection = duckdb.connect()
    connection.execute(f"SET threads={int(audit_config['duckdb_threads'])}")
    connection.execute(f"SET memory_limit='{audit_config['duckdb_memory_limit']}'")
    connection.execute(f"SET temp_directory={quote_path(temp_dir)}")

    monthly_rows: list[dict[str, Any]] = []
    missingness_rows: list[dict[str, Any]] = []
    for index, (month, path) in enumerate(files, start=1):
        tick = time.perf_counter()
        columns = [field.name for field in pq.ParquetFile(path).schema_arrow]
        audit_row, missing_rows = audit_one_month(connection, month, path, columns, audit_config)
        monthly_rows.append(audit_row)
        missingness_rows.extend(missing_rows)
        print(
            f"audited {month} ({index}/{len(files)}): rows={int(audit_row['rows']):,}, "
            f"target_valid={int(audit_row['target_valid_rows']):,}, "
            f"duplicates={int(audit_row['hash_duplicate_rows']):,}, "
            f"elapsed={time.perf_counter() - tick:.1f}s",
            flush=True,
        )

    monthly = pd.DataFrame(monthly_rows).sort_values("month")
    monthly["raw_rows_change_pct"] = 100 * monthly["rows"].pct_change()
    monthly["target_valid_change_pct"] = 100 * monthly["target_valid_rows"].pct_change()
    monthly.to_csv(output_dir / "monthly_audit.csv", index=False)
    yearly = monthly.assign(year=monthly["month"].str[:4]).groupby("year", as_index=False).agg(
        raw_rows=("rows", "sum"),
        target_valid_rows=("target_valid_rows", "sum"),
        target_excluded_rows=("target_excluded_rows", "sum"),
    )
    yearly["target_valid_pct"] = 100 * yearly["target_valid_rows"] / yearly["raw_rows"]
    yearly.to_csv(output_dir / "yearly_counts.csv", index=False)

    monthly_missingness = pd.DataFrame(missingness_rows).sort_values(["column", "month"])
    monthly_missingness.to_csv(output_dir / "missingness_by_month.csv", index=False)
    missingness_summary = overall_missingness(monthly_missingness, schema, len(months))
    missingness_summary.to_csv(output_dir / "missingness_overall.csv", index=False)

    source_list = ", ".join(quote_path(path) for _, path in files)
    connection.execute(
        f"CREATE OR REPLACE VIEW dev_raw AS "
        f"SELECT * FROM read_parquet([{source_list}], union_by_name=true)"
    )
    category_frame = categorical_counts(connection)
    category_frame.to_csv(output_dir / "categorical_value_counts.csv", index=False)
    numeric_frame = numeric_summary(connection)
    numeric_frame.to_csv(output_dir / "numeric_summary.csv", index=False)
    vendor_frame = vendor_quality(connection)
    vendor_frame.to_csv(output_dir / "quality_by_vendor.csv", index=False)
    bundle_frame = missing_bundle_summary(connection)
    bundle_frame.to_csv(output_dir / "missing_bundle_summary.csv", index=False)
    duplicate_frame = verify_duplicate_groups(connection, files, monthly, audit_config)
    duplicate_frame.to_csv(output_dir / "duplicate_groups_summary.csv", index=False)

    zone_audit = zone_lookup_audit(project_config.raw_dir)
    write_json(output_dir / "zone_lookup_audit.json", zone_audit)

    total_rows = int(monthly["rows"].sum())
    total_target_valid = int(monthly["target_valid_rows"].sum())
    totals = {
        column: int(monthly[column].sum())
        for column in [
            "hash_duplicate_rows",
            "dropoff_before_pickup",
            "duration_nonpositive",
            "duration_over_threshold",
            "pickup_outside_file_month",
            "pu_zone_outside_1_265",
            "do_zone_outside_1_265",
            "pu_semantic_bucket",
            "do_semantic_bucket",
            "pu_not_modeling_zone",
            "distance_nonpositive",
            "distance_over_threshold",
            "fare_negative",
            "total_negative",
            "passenger_nonpositive",
            "passenger_over_threshold",
            "target_excluded_rows",
        ]
    }
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "development_only",
        "development_period": {
            "start_month": months[0],
            "end_month": months[-1],
            "months": len(months),
        },
        "final_test_excluded": {
            "start_month": "2026-01",
            "end_month": "2026-05",
        },
        "raw_rows": total_rows,
        "target_valid_rows": total_target_valid,
        "target_valid_pct": 100 * total_target_valid / total_rows,
        "schema_variants": int(len(schema_variants)),
        "columns_union": sorted(schema["column"].unique().tolist()),
        "audit_totals": totals,
        "verified_exact_duplicate_rows": int(duplicate_frame["exact_duplicate_rows"].sum()),
        "target_valid_exact_duplicate_rows": int(
            duplicate_frame["target_valid_exact_duplicate_rows"].sum()
        ),
        "zone_lookup": zone_audit,
        "elapsed_seconds": time.perf_counter() - started,
    }
    write_json(output_dir / "audit_summary.json", summary)
    write_json(
        output_dir / "audit_run_metadata.json",
        {
            "generated_at_utc": summary["generated_at_utc"],
            "project_config": str(project_config_path),
            "audit_config": str(audit_config_path),
            "output_dir": str(output_dir),
            "duckdb_version": duckdb.__version__,
            "pandas_version": pd.__version__,
            "files": [str(path) for _, path in files],
            "excluded_final_test_files": [
                str(project_config.raw_dir / f"yellow_tripdata_{month}.parquet")
                for month in month_range("2026-01", "2026-05")
            ],
        },
    )
    connection.close()
    print(
        f"audit complete: rows={total_rows:,}, target_valid={total_target_valid:,} "
        f"({100 * total_target_valid / total_rows:.4f}%), "
        f"elapsed={time.perf_counter() - started:.1f}s\n"
        f"outputs: {output_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
