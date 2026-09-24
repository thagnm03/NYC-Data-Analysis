"""Verify the Step 9 report, notebook, README, and reproducibility package."""

from __future__ import annotations

import ast
import hashlib
import json
import platform
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "reproducibility"
NOTEBOOK_PATH = PROJECT_ROOT / "notebooks" / "NYC_TLC_predictive_analysis.ipynb"
REPORT_PATH = PROJECT_ROOT / "FINAL_REPORT.md"
README_PATH = PROJECT_ROOT / "README.md"
EXECUTED_NOTEBOOK_PATH = OUTPUT_DIR / "NYC_TLC_predictive_analysis_executed.ipynb"

FROZEN_HASHES = {
    "config/modeling_config.json": "9a70f5987d653abef71ede2a20ce31a3801339b8c0dab8ab74a3096865130fe7",
    "config/training_config.json": "3bda0eb733295ba242f196697dadf469d82f80c09ae29714b459351a52fcf181",
    "outputs/modeling/model_selection.json": "10a2a7c394154e2313544c7785a5a386247d9c1295cd97241df82d36941a517f",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


def package_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def main() -> None:
    checks: list[dict[str, Any]] = []

    required_files = [
        NOTEBOOK_PATH,
        REPORT_PATH,
        README_PATH,
        PROJECT_ROOT / "requirements-notebook.txt",
        PROJECT_ROOT / "requirements-lock.txt",
        PROJECT_ROOT / "scripts" / "run_pipeline.ps1",
        PROJECT_ROOT / "STEP_08_REPORT.md",
        EXECUTED_NOTEBOOK_PATH,
    ]
    missing = [str(path.relative_to(PROJECT_ROOT)) for path in required_files if not path.exists()]
    checks.append(check("step9_files_exist", not missing, f"missing={missing}"))

    notebook = load_json(NOTEBOOK_PATH)
    cells = notebook.get("cells", [])
    code_cells = [cell for cell in cells if cell.get("cell_type") == "code"]
    markdown_cells = [cell for cell in cells if cell.get("cell_type") == "markdown"]
    checks.append(check("notebook_format", notebook.get("nbformat") == 4 and isinstance(cells, list), f"nbformat={notebook.get('nbformat')}"))
    checks.append(check("notebook_has_narrative_and_code", len(markdown_cells) >= 8 and len(code_cells) >= 8, f"markdown={len(markdown_cells)}; code={len(code_cells)}"))

    syntax_errors: list[str] = []
    for index, cell in enumerate(code_cells, start=1):
        source = "".join(cell.get("source", []))
        try:
            ast.parse(source, filename=f"notebook_code_cell_{index}")
        except SyntaxError as exc:
            syntax_errors.append(f"cell {index}: {exc}")
    checks.append(check("notebook_code_compiles", not syntax_errors, f"errors={syntax_errors}"))

    notebook_code = "\n".join("".join(cell.get("source", [])) for cell in code_cells)
    prohibited_notebook_tokens = ["read_parquet(", ".fit(", "yellow_tripdata_"]
    prohibited_found = [token for token in prohibited_notebook_tokens if token in notebook_code]
    checks.append(check("notebook_is_read_only_presentation", not prohibited_found, f"prohibited={prohibited_found}"))

    executed_notebook = load_json(EXECUTED_NOTEBOOK_PATH)
    executed_code_cells = [cell for cell in executed_notebook.get("cells", []) if cell.get("cell_type") == "code"]
    execution_errors = [
        output.get("ename", "unknown error")
        for cell in executed_code_cells
        for output in cell.get("outputs", [])
        if output.get("output_type") == "error"
    ]
    all_executed = bool(executed_code_cells) and all(cell.get("execution_count") is not None for cell in executed_code_cells)
    checks.append(check("executed_notebook_passes", all_executed and not execution_errors, f"code_cells={len(executed_code_cells)}; errors={execution_errors}"))

    report = REPORT_PATH.read_text(encoding="utf-8")
    readme = README_PATH.read_text(encoding="utf-8")
    report_sections = [
        "## 1. Bối cảnh và câu hỏi nghiên cứu",
        "## 3. Data understanding và chất lượng dữ liệu",
        "## 5. EDA và problem discovery",
        "## 8. Development rolling-validation results",
        "## 9. Frozen final evaluation",
        "## 10. Error analysis",
        "## 12. Giới hạn",
        "## 13. Tái lập",
    ]
    missing_sections = [section for section in report_sections if section not in report]
    checks.append(check("final_report_complete", not missing_sections, f"missing_sections={missing_sections}"))

    output_references = [
        "outputs/audit/audit_summary.json",
        "outputs/panel/panel_build_summary.json",
        "outputs/eda/eda_summary.json",
        "outputs/modeling/mean_fold_metrics.csv",
        "outputs/final_evaluation/final_metrics.csv",
        "outputs/final_evaluation/frozen_decision.json",
    ]
    missing_references = [reference for reference in output_references if reference not in readme]
    checks.append(check("readme_traceability_map", not missing_references, f"missing={missing_references}"))
    checks.append(check("readme_pipeline_commands", "run_pipeline.ps1 -Stage all" in readme and "verify_step9.py" in readme and "jupyter nbconvert" in readme, "all/verify/notebook commands documented"))

    frozen_errors = []
    for relative_path, expected_hash in FROZEN_HASHES.items():
        actual_hash = sha256(PROJECT_ROOT / relative_path)
        if actual_hash != expected_hash:
            frozen_errors.append(f"{relative_path}: {actual_hash}")
    checks.append(check("frozen_hashes_unchanged", not frozen_errors, f"errors={frozen_errors}"))

    verification_paths = [
        "outputs/audit/audit_verification.json",
        "outputs/panel/panel_integrity_report.json",
        "outputs/eda/eda_verification.json",
        "outputs/modeling_spec/modeling_spec_verification.json",
        "outputs/modeling/modeling_verification.json",
        "outputs/final_evaluation/final_evaluation_verification.json",
    ]
    verification_errors = []
    for relative_path in verification_paths:
        payload = load_json(PROJECT_ROOT / relative_path)
        if not payload.get("success", False) or payload.get("errors"):
            verification_errors.append(relative_path)
    checks.append(check("upstream_verifiers_pass", not verification_errors, f"errors={verification_errors}"))

    audit = load_json(PROJECT_ROOT / "outputs/audit/audit_summary.json")
    panel = load_json(PROJECT_ROOT / "outputs/panel/panel_build_summary.json")
    eda = load_json(PROJECT_ROOT / "outputs/eda/eda_summary.json")
    final_summary = load_json(PROJECT_ROOT / "outputs/final_evaluation/final_evaluation_summary.json")
    frozen_decision = load_json(PROJECT_ROOT / "outputs/final_evaluation/frozen_decision.json")
    development_metrics = pd.read_csv(PROJECT_ROOT / "outputs/modeling/mean_fold_metrics.csv")
    final_metrics = pd.read_csv(PROJECT_ROOT / "outputs/final_evaluation/final_metrics.csv")

    data_reconciles = (
        audit["raw_rows"] == 89_892_322
        and panel["panel_target_sum"] == 89_642_441
        and panel["panel_rows"] == 1_144_746
        and eda["final_test_rows_used"] == 0
        and final_summary["test_rows"] == 236_466
        and final_summary["test_target_sum"] == 18_969_497
    )
    checks.append(check("headline_data_reconciles", data_reconciles, "audit/panel/EDA/final summaries"))

    dev_weekly = development_metrics.loc[development_metrics["model"] == "baseline_previous_week", "mean_MAE"].item()
    dev_winner = development_metrics.sort_values("mean_MAE").iloc[0]
    final_weekly = final_metrics.loc[final_metrics["method"] == "baseline_previous_week"].iloc[0]
    final_winner = final_metrics.sort_values("MAE").iloc[0]
    metrics_reconcile = (
        abs(dev_weekly - 16.390639243192133) < 1e-10
        and dev_winner["model"] == "baseline_seasonal_mean_4w"
        and abs(float(final_weekly["MAE"]) - 16.217599993233698) < 1e-10
        and final_winner["method"] == "ridge_regression"
    )
    checks.append(check("headline_metrics_reconcile", metrics_reconcile, "development and final metric tables"))

    decision_ok = (
        frozen_decision["formal_selected_method_before_final_test"] == "baseline_previous_week"
        and frozen_decision["formal_selected_method_after_final_test"] == "baseline_previous_week"
        and frozen_decision["selection_changed"] is False
        and frozen_decision["final_test_not_used_for_reselection"] is True
    )
    checks.append(check("frozen_decision_preserved", decision_ok, "formal selection unchanged"))

    required_report_values = ["89.892.322", "89.642.441", "14,88", "16,22", "20,22%", "14,17", "29/29"]
    missing_values = [value for value in required_report_values if value not in report]
    checks.append(check("report_contains_traced_headlines", not missing_values, f"missing={missing_values}"))

    notebook_packages = ["nbformat", "nbclient", "nbconvert", "jupyterlab"]
    missing_packages = [name for name in notebook_packages if package_version(name) is None]
    checks.append(check("notebook_environment_available", not missing_packages, f"missing={missing_packages}"))

    manifest_targets = [
        "PROJECT_BRIEF.md",
        "CLEANING_LOG.md",
        "DATA_DICTIONARY.md",
        "FINAL_REPORT.md",
        "README.md",
        "PROGRESS.md",
        "requirements-notebook.txt",
        "requirements-lock.txt",
        "notebooks/NYC_TLC_predictive_analysis.ipynb",
        "scripts/run_pipeline.ps1",
        "scripts/verify_step9.py",
        "config/modeling_config.json",
        "config/training_config.json",
        "config/final_evaluation_config.json",
    ]
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            name: package_version(name)
            for name in ["pandas", "numpy", "pyarrow", "duckdb", "matplotlib", "seaborn", "scikit-learn", "nbformat", "nbclient", "nbconvert", "jupyterlab"]
        },
        "files": {
            relative_path: {
                "sha256": sha256(PROJECT_ROOT / relative_path),
                "bytes": (PROJECT_ROOT / relative_path).stat().st_size,
            }
            for relative_path in manifest_targets
        },
        "frozen_protocol_hashes": FROZEN_HASHES,
    }

    success = all(item["passed"] for item in checks)
    verification = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "checks_passed": sum(item["passed"] for item in checks),
        "checks_total": len(checks),
        "errors": [item["detail"] for item in checks if not item["passed"]],
        "checks": checks,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "reproducibility_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "step9_verification.json").write_text(json.dumps(verification, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(verification, indent=2, ensure_ascii=False))
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
