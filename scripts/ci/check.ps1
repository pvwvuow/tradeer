#Requires -Version 7
# Install, lint, type-check and test. Called by ci/workflows/ci.yml and by release.ps1.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "::group::$Name"
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
    Write-Host "::endgroup::"
}

Invoke-Step "Upgrade pip" { python -m pip install --upgrade pip }
Invoke-Step "Install" { python -m pip install -e ".[dev]" }
Invoke-Step "Ruff lint" { ruff check . }
Invoke-Step "Ruff format" { ruff format --check . }
Invoke-Step "Mypy" { mypy }
Invoke-Step "Pytest" { pytest --cov=app --cov-report=xml --cov-report=term-missing }
