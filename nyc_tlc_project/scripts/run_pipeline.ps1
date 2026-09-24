param(
    [ValidateSet("download", "development", "final", "verify", "all")]
    [string]$Stage = "verify",
    [switch]$RehashRaw
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $ProjectRoot
$Python = Join-Path $WorkspaceRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python. Create it and install requirements first."
}

function Invoke-PythonStep {
    param(
        [string]$Label,
        [string[]]$Arguments
    )
    Write-Host "`n[$Label]" -ForegroundColor Cyan
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Step failed: $Label (exit code $LASTEXITCODE)"
    }
}

function Invoke-Download {
    Invoke-PythonStep "Download raw data" @((Join-Path $PSScriptRoot "download_data.py"))
    $verifyArgs = @((Join-Path $PSScriptRoot "verify_download.py"))
    if ($RehashRaw) { $verifyArgs += "--rehash" }
    Invoke-PythonStep "Verify raw data" $verifyArgs
}

function Invoke-Development {
    Invoke-PythonStep "Audit development data" @((Join-Path $PSScriptRoot "audit_data.py"))
    Invoke-PythonStep "Verify audit" @((Join-Path $PSScriptRoot "verify_audit.py"))
    Invoke-PythonStep "Build development panel" @((Join-Path $PSScriptRoot "build_panel.py"))
    Invoke-PythonStep "Verify panel" @((Join-Path $PSScriptRoot "verify_panel.py"))
    Invoke-PythonStep "Run development EDA" @((Join-Path $PSScriptRoot "run_eda.py"))
    Invoke-PythonStep "Verify EDA" @((Join-Path $PSScriptRoot "verify_eda.py"))
    Invoke-PythonStep "Build modeling frame" @((Join-Path $PSScriptRoot "build_modeling_frame.py"))
    Invoke-PythonStep "Verify modeling specification" @((Join-Path $PSScriptRoot "verify_modeling_spec.py"))
    Invoke-PythonStep "Run rolling-fold models" @((Join-Path $PSScriptRoot "run_models.py"))
    Invoke-PythonStep "Verify model development" @((Join-Path $PSScriptRoot "verify_models.py"))
}

function Invoke-Final {
    Invoke-PythonStep "Build frozen final test" @((Join-Path $PSScriptRoot "build_final_test.py"))
    Invoke-PythonStep "Run frozen final evaluation" @((Join-Path $PSScriptRoot "run_final_evaluation.py"))
    Invoke-PythonStep "Verify final evaluation" @((Join-Path $PSScriptRoot "verify_final_evaluation.py"))
}

function Invoke-Verification {
    $verifyDownloadArgs = @((Join-Path $PSScriptRoot "verify_download.py"))
    if ($RehashRaw) { $verifyDownloadArgs += "--rehash" }
    Invoke-PythonStep "Verify raw data" $verifyDownloadArgs
    Invoke-PythonStep "Verify audit" @((Join-Path $PSScriptRoot "verify_audit.py"))
    Invoke-PythonStep "Verify panel" @((Join-Path $PSScriptRoot "verify_panel.py"))
    Invoke-PythonStep "Verify EDA" @((Join-Path $PSScriptRoot "verify_eda.py"))
    Invoke-PythonStep "Verify modeling specification" @((Join-Path $PSScriptRoot "verify_modeling_spec.py"))
    Invoke-PythonStep "Verify model development" @((Join-Path $PSScriptRoot "verify_models.py"))
    Invoke-PythonStep "Verify final evaluation" @((Join-Path $PSScriptRoot "verify_final_evaluation.py"))
}

switch ($Stage) {
    "download" { Invoke-Download }
    "development" { Invoke-Development }
    "final" { Invoke-Final }
    "verify" { Invoke-Verification }
    "all" {
        Invoke-Download
        Invoke-Development
        Invoke-Final
    }
}

Write-Host "`nPipeline stage '$Stage' completed successfully." -ForegroundColor Green
