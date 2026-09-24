"""Verify configured NYC TLC raw-file coverage and Parquet readability."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from download_data import DEFAULT_CONFIG, load_config, month_range, sha256, valid_parquet_container


REQUIRED_MODELING_COLUMNS = {"tpep_pickup_datetime", "PULocationID"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--rehash",
        action="store_true",
        help="Recompute all SHA-256 digests and compare them with the manifest.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    months = month_range(config.start_month, config.end_month)
    manifest_path = config.raw_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_files = {item["file"]: item for item in manifest["files"]}

    expected_names = [f"yellow_tripdata_{month}.parquet" for month in months]
    missing = [name for name in expected_names if not (config.raw_dir / name).is_file()]
    unexpected = sorted(
        path.name
        for path in config.raw_dir.glob("yellow_tripdata_*.parquet")
        if path.name not in expected_names
    )
    if missing:
        raise RuntimeError(f"Missing configured monthly files: {missing}")

    file_results: list[dict[str, Any]] = []
    errors: list[str] = []
    total_rows = 0
    total_bytes = 0
    schema_signatures: dict[str, list[str]] = {}
    for name in expected_names:
        path = config.raw_dir / name
        result: dict[str, Any] = {
            "file": name,
            "bytes": path.stat().st_size,
            "container_valid": valid_parquet_container(path),
        }
        try:
            parquet = pq.ParquetFile(path)
            columns = parquet.schema.names
            result.update(
                {
                    "readable": True,
                    "rows": parquet.metadata.num_rows,
                    "row_groups": parquet.metadata.num_row_groups,
                    "column_count": len(columns),
                    "required_columns_present": REQUIRED_MODELING_COLUMNS.issubset(columns),
                }
            )
            signature = hashlib_signature(columns)
            schema_signatures.setdefault(signature, columns)
            result["schema_signature"] = signature
            if parquet.metadata.num_rows <= 0:
                errors.append(f"{name}: contains no rows")
            if not result["required_columns_present"]:
                errors.append(f"{name}: missing one of {sorted(REQUIRED_MODELING_COLUMNS)}")
        except Exception as exc:  # verification must report every unreadable file
            result.update({"readable": False, "error": f"{type(exc).__name__}: {exc}"})
            errors.append(f"{name}: unreadable ({exc})")

        manifest_record = manifest_files.get(name)
        result["manifest_record_present"] = manifest_record is not None
        result["size_matches_manifest"] = bool(
            manifest_record and int(manifest_record["bytes"]) == path.stat().st_size
        )
        if not result["container_valid"]:
            errors.append(f"{name}: invalid Parquet magic bytes")
        if not result["manifest_record_present"]:
            errors.append(f"{name}: missing manifest record")
        elif not result["size_matches_manifest"]:
            errors.append(f"{name}: file size does not match manifest")
        if args.rehash:
            digest = sha256(path)
            result["sha256"] = digest
            result["sha256_matches_manifest"] = bool(
                manifest_record and digest == manifest_record.get("sha256")
            )
            if not result["sha256_matches_manifest"]:
                errors.append(f"{name}: SHA-256 does not match manifest")
        total_rows += int(result.get("rows", 0))
        total_bytes += path.stat().st_size
        file_results.append(result)
        print(
            f"verified {name}: rows={result.get('rows', 0):,}, "
            f"columns={result.get('column_count', 0)}, bytes={path.stat().st_size:,}",
            flush=True,
        )

    zone_path = config.raw_dir / "taxi_zone_lookup.csv"
    if not zone_path.is_file():
        errors.append("taxi_zone_lookup.csv: missing")
        zone_result: dict[str, Any] = {"file": zone_path.name, "readable": False}
    else:
        with zone_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            columns = reader.fieldnames or []
        zone_result = {
            "file": zone_path.name,
            "readable": True,
            "rows": len(rows),
            "columns": columns,
            "required_columns_present": {"LocationID", "Borough", "Zone"}.issubset(columns),
        }
        if not zone_result["required_columns_present"]:
            errors.append("taxi_zone_lookup.csv: required columns missing")

    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_file": str(config_path),
        "period": {
            "start_month": config.start_month,
            "end_month": config.end_month,
            "expected_months": len(months),
        },
        "success": not errors,
        "missing_files": missing,
        "unexpected_monthly_files": unexpected,
        "monthly_file_count": len(file_results),
        "total_parquet_bytes": total_bytes,
        "total_raw_rows_from_metadata": total_rows,
        "schema_variant_count": len(schema_signatures),
        "schema_variants": schema_signatures,
        "files": file_results,
        "zone_lookup": zone_result,
        "errors": errors,
    }
    report_path = config.raw_dir / "verification_report.json"
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(report_path)
    print(
        f"verification success={report['success']} files={len(file_results)} "
        f"rows={total_rows:,} schema_variants={len(schema_signatures)}\n"
        f"report: {report_path}",
        flush=True,
    )
    if errors:
        raise RuntimeError("Download verification failed: " + "; ".join(errors))


def hashlib_signature(columns: list[str]) -> str:
    import hashlib

    return hashlib.sha256("\x1f".join(columns).encode("utf-8")).hexdigest()[:12]


if __name__ == "__main__":
    main()
