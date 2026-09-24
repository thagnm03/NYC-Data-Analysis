"""Verify presentation deliverables for project Step 10."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DELIVERABLES = PROJECT_ROOT / "deliverables"
PPTX = DELIVERABLES / "NYC_TLC_Predictive_Analysis_Presentation.pptx"
SPEAKER_NOTES = DELIVERABLES / "SPEAKER_NOTES.md"
Q_AND_A = DELIVERABLES / "Q_AND_A.md"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "presentation"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    checks: list[dict[str, object]] = []

    for path in (PPTX, SPEAKER_NOTES, Q_AND_A):
        checks.append(
            {
                "check": f"exists:{path.name}",
                "passed": path.is_file() and path.stat().st_size > 0,
                "value": path.stat().st_size if path.is_file() else 0,
            }
        )

    with zipfile.ZipFile(PPTX) as package:
        names = package.namelist()
        slides = [name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
        notes = [name for name in names if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml")]
        charts = [
            name
            for name in names
            if name.endswith(".xml") and Path(name).name.startswith("chart") and "/charts/" in name
        ]

    checks.extend(
        [
            {"check": "slide_count", "passed": len(slides) == 12, "value": len(slides)},
            {"check": "embedded_notes_count", "passed": len(notes) == 12, "value": len(notes)},
            {"check": "native_chart_count", "passed": len(charts) == 4, "value": len(charts)},
        ]
    )

    notes_text = SPEAKER_NOTES.read_text(encoding="utf-8")
    qa_text = Q_AND_A.read_text(encoding="utf-8")
    checks.extend(
        [
            {
                "check": "speaker_notes_cover_all_slides",
                "passed": all(f"## Slide {number}" in notes_text for number in range(1, 13)),
                "value": 12,
            },
            {
                "check": "qa_has_at_least_15_questions",
                "passed": sum(line.startswith("## ") and line[3:4].isdigit() for line in qa_text.splitlines()) >= 15,
                "value": sum(line.startswith("## ") and line[3:4].isdigit() for line in qa_text.splitlines()),
            },
            {
                "check": "key_final_metrics_present",
                "passed": all(value in notes_text for value in ("16,22", "20,22%", "0,935")),
                "value": ["16,22", "20,22%", "0,935"],
            },
        ]
    )

    passed = all(bool(item["passed"]) for item in checks)
    result = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "deck": {
            "path": str(PPTX.relative_to(PROJECT_ROOT)),
            "size_bytes": PPTX.stat().st_size,
            "sha256": sha256(PPTX),
        },
        "checks": checks,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "step10_verification.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if not passed:
        failed = [item["check"] for item in checks if not item["passed"]]
        raise SystemExit(f"Step 10 verification failed: {failed}")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
