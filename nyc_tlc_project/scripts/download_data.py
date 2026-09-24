"""Download a configurable contiguous range of NYC TLC Yellow Taxi files.

The downloader is idempotent, reuses valid local cache files when possible,
writes network downloads to ``.part`` files, and records SHA-256 checksums in
an atomic JSON manifest. It deliberately performs only file-level checks;
data-quality auditing belongs to the next project step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "project_config.json"


@dataclass(frozen=True)
class DownloadConfig:
    dataset_name: str
    start_month: str
    end_month: str
    base_url: str
    zone_lookup_url: str
    raw_dir: Path
    cache_dirs: tuple[Path, ...]
    download_workers: int
    request_timeout_seconds: int
    download_retries: int
    chunk_size_bytes: int
    user_agent: str


def parse_month(value: str) -> tuple[int, int]:
    try:
        year_text, month_text = value.split("-")
        year, month = int(year_text), int(month_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid month {value!r}; expected YYYY-MM") from exc
    if year < 2000 or not 1 <= month <= 12 or value != f"{year:04d}-{month:02d}":
        raise ValueError(f"Invalid month {value!r}; expected YYYY-MM")
    return year, month


def month_range(start: str, end: str) -> list[str]:
    start_year, start_month = parse_month(start)
    end_year, end_month = parse_month(end)
    start_index = start_year * 12 + start_month - 1
    end_index = end_year * 12 + end_month - 1
    if end_index < start_index:
        raise ValueError(f"end_month {end!r} is before start_month {start!r}")
    return [f"{index // 12:04d}-{index % 12 + 1:02d}" for index in range(start_index, end_index + 1)]


def resolve_from_config(config_path: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def load_config(config_path: Path) -> DownloadConfig:
    raw: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    required = {
        "dataset_name",
        "start_month",
        "end_month",
        "base_url",
        "zone_lookup_url",
        "raw_dir",
        "cache_dirs",
        "download_workers",
        "request_timeout_seconds",
        "download_retries",
        "chunk_size_mib",
        "user_agent",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise KeyError(f"Config is missing required keys: {missing}")
    month_range(raw["start_month"], raw["end_month"])
    workers = int(raw["download_workers"])
    retries = int(raw["download_retries"])
    if workers < 1 or retries < 1:
        raise ValueError("download_workers and download_retries must be positive")
    return DownloadConfig(
        dataset_name=str(raw["dataset_name"]),
        start_month=str(raw["start_month"]),
        end_month=str(raw["end_month"]),
        base_url=str(raw["base_url"]).rstrip("/"),
        zone_lookup_url=str(raw["zone_lookup_url"]),
        raw_dir=resolve_from_config(config_path, raw["raw_dir"]),
        cache_dirs=tuple(resolve_from_config(config_path, item) for item in raw["cache_dirs"]),
        download_workers=workers,
        request_timeout_seconds=int(raw["request_timeout_seconds"]),
        download_retries=retries,
        chunk_size_bytes=int(raw["chunk_size_mib"]) * 1024 * 1024,
        user_agent=str(raw["user_agent"]),
    )


def sha256(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_parquet_container(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 8:
        return False
    with path.open("rb") as handle:
        head = handle.read(4)
        handle.seek(-4, os.SEEK_END)
        tail = handle.read(4)
    return head == b"PAR1" and tail == b"PAR1"


def valid_csv(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        first_line = handle.readline()
    return "LocationID" in first_line and "Zone" in first_line


def link_or_copy(source: Path, destination: Path) -> str:
    try:
        os.link(source, destination)
        return "hardlinked_from_cache"
    except OSError:
        shutil.copy2(source, destination)
        return "copied_from_cache"


def find_cache_file(filename: str, cache_dirs: tuple[Path, ...], parquet: bool) -> Path | None:
    validator = valid_parquet_container if parquet else valid_csv
    for directory in cache_dirs:
        candidate = directory / filename
        if validator(candidate):
            return candidate
    return None


def download_with_resume(url: str, destination: Path, config: DownloadConfig) -> dict[str, str | int | None]:
    partial = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, config.download_retries + 1):
        resume_at = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": config.user_agent}
        if resume_at:
            headers["Range"] = f"bytes={resume_at}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=config.request_timeout_seconds) as response:
                status = getattr(response, "status", response.getcode())
                append = resume_at > 0 and status == 206
                mode = "ab" if append else "wb"
                if resume_at and not append:
                    resume_at = 0
                expected_remaining = response.headers.get("Content-Length")
                with partial.open(mode) as output:
                    downloaded_this_attempt = 0
                    next_notice = 64 * 1024 * 1024
                    while chunk := response.read(config.chunk_size_bytes):
                        output.write(chunk)
                        downloaded_this_attempt += len(chunk)
                        if downloaded_this_attempt >= next_notice:
                            total_mib = (resume_at + downloaded_this_attempt) / 1024**2
                            print(f"progress {destination.name}: {total_mib:,.1f} MiB", flush=True)
                            next_notice += 64 * 1024 * 1024
                if expected_remaining is not None and downloaded_this_attempt != int(expected_remaining):
                    raise OSError(
                        f"short response for {destination.name}: "
                        f"received {downloaded_this_attempt:,}, expected {int(expected_remaining):,} bytes"
                    )
                partial.replace(destination)
                return {
                    "http_status": int(status),
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                }
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
            if attempt == config.download_retries:
                break
            delay = min(2 ** (attempt - 1), 8)
            print(
                f"retry {attempt}/{config.download_retries} for {destination.name} "
                f"after {type(exc).__name__}: {exc}",
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError(f"Failed to download {url} after {config.download_retries} attempts") from last_error


def obtain_file(
    filename: str,
    url: str,
    config: DownloadConfig,
    *,
    parquet: bool,
    force: bool,
) -> dict[str, Any]:
    destination = config.raw_dir / filename
    validator = valid_parquet_container if parquet else valid_csv
    action: str
    http_metadata: dict[str, str | int | None] = {
        "http_status": None,
        "etag": None,
        "last_modified": None,
    }

    if destination.exists() and not force:
        if not validator(destination):
            raise RuntimeError(f"Existing file is invalid; inspect or use --force: {destination}")
        action = "reused_existing"
    else:
        if destination.exists():
            destination.unlink()
        cache = find_cache_file(filename, config.cache_dirs, parquet)
        if cache is not None:
            action = link_or_copy(cache, destination)
            print(f"{action} {filename} from {cache}", flush=True)
        else:
            print(f"downloading {filename}", flush=True)
            http_metadata = download_with_resume(url, destination, config)
            if not validator(destination):
                raise RuntimeError(f"Downloaded file failed container/header validation: {destination}")
            action = "downloaded"

    record = {
        "file": filename,
        "url": url,
        "bytes": destination.stat().st_size,
        "sha256": sha256(destination),
        "action": action,
        **http_metadata,
    }
    if parquet:
        record["month"] = filename.removeprefix("yellow_tripdata_").removesuffix(".parquet")
        record["parquet_magic_valid"] = True
    else:
        record["csv_header_valid"] = True
    print(f"ready {filename}: {record['bytes']:,} bytes ({action})", flush=True)
    return record


def write_manifest(config_path: Path, config: DownloadConfig, records: list[dict[str, Any]]) -> Path:
    months = month_range(config.start_month, config.end_month)
    manifest = {
        "manifest_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": config.dataset_name,
        "official_page": "https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page",
        "period": {
            "start_month": config.start_month,
            "end_month": config.end_month,
            "expected_months": len(months),
        },
        "raw_grain": "one completed Yellow Taxi trip per row",
        "config_file": str(config_path),
        "raw_dir": str(config.raw_dir),
        "total_bytes": sum(int(record["bytes"]) for record in records),
        "files": sorted(records, key=lambda item: item["file"]),
    }
    destination = config.raw_dir / "manifest.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace destination files. Valid cache files are still preferred before network downloads.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    config.raw_dir.mkdir(parents=True, exist_ok=True)
    months = month_range(config.start_month, config.end_month)
    print(
        f"dataset={config.dataset_name}\n"
        f"period={config.start_month}..{config.end_month} ({len(months)} months)\n"
        f"raw_dir={config.raw_dir}\n"
        f"workers={config.download_workers}",
        flush=True,
    )

    jobs = [
        (
            f"yellow_tripdata_{month}.parquet",
            f"{config.base_url}/yellow_tripdata_{month}.parquet",
            True,
        )
        for month in months
    ]
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=config.download_workers) as pool:
        futures = {
            pool.submit(
                obtain_file,
                filename,
                url,
                config,
                parquet=parquet,
                force=args.force,
            ): filename
            for filename, url, parquet in jobs
        }
        for future in as_completed(futures):
            records.append(future.result())

    zone_filename = "taxi_zone_lookup.csv"
    records.append(
        obtain_file(
            zone_filename,
            config.zone_lookup_url,
            config,
            parquet=False,
            force=args.force,
        )
    )
    manifest_path = write_manifest(config_path, config, records)
    print(
        f"complete: {len(months)} monthly Parquet files + zone lookup\n"
        f"manifest: {manifest_path}",
        flush=True,
    )


if __name__ == "__main__":
    main()
