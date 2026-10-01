#Requires -Version 7
# Install, lint, type-check and test. Called by .github/workflows/ci.yml and by release.ps1.
# Every check runs even after an earlier one failed, and the output of a failed check is
# repeated as a GitHub annotation, so the reason is visible without opening the job log.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "::group::$Name"
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
    Write-Host "::endgroup::"
}

function Write-Annotation([string]$Title, [string]$Text) {
    # A workflow command is one line: encode % and line breaks, and keep the message small.
    $limit = 12000
    if ($Text.Length -gt $limit) { $Text = $Text.Substring(0, $limit) + "`n(truncated, see the job log)" }
    $message = $Text.Replace("%", "%25").Replace("`r", "%0D").Replace("`n", "%0A")
    $safeTitle = $Title.Replace("%", "%25").Replace(":", "%3A").Replace(",", "%2C")
    Write-Host "::error title=$safeTitle::$message"
}

$failed = [System.Collections.Generic.List[string]]::new()

function Invoke-Check([string]$Name, [scriptblock]$Command) {
    # Native tools write to stderr; that must not stop the script before the report.
    $ErrorActionPreference = "Continue"
    Write-Host "::group::$Name"
    $global:LASTEXITCODE = 0
    $output = (& $Command 2>&1 | Out-String).TrimEnd()
    $code = $LASTEXITCODE
    Write-Host $output
    Write-Host "::endgroup::"
    if ($code -ne 0) {
        Write-Annotation "$Name failed" $output
        $failed.Add($Name)
    }
}

Invoke-Step "Upgrade pip" { python -m pip install --upgrade pip }
Invoke-Step "Install" { python -m pip install -e ".[dev]" }
Invoke-Check "Ruff lint" { ruff check --output-format=concise . }
Invoke-Check "Ruff format" { ruff format --diff . }
Invoke-Check "Mypy" { mypy }

# Pytest streams its output; tests/conftest.py writes the annotations for failed tests.
Write-Host "::group::Pytest"
$global:LASTEXITCODE = 0
pytest --cov=app --cov-report=xml --cov-report=term-missing
if ($LASTEXITCODE -ne 0) { $failed.Add("Pytest") }
Write-Host "::endgroup::"

if ($failed.Count -gt 0) { throw "Failed checks: $($failed -join ', ')" }
