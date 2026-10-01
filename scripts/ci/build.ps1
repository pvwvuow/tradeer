#Requires -Version 7
# Build the one-folder Windows app with PyInstaller, verify it with --self-check, zip it.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "::group::$Name"
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
    Write-Host "::endgroup::"
}

$root = (Resolve-Path "$PSScriptRoot/../..").Path
Set-Location $root

Invoke-Step "Install" { python -m pip install -e ".[dev]" }
Invoke-Step "Source self-check" { python -m app --self-check }
Invoke-Step "PyInstaller" {
    pyinstaller --noconfirm --clean --onedir --windowed `
        --name MT5TradingWorkstation `
        --paths "$root" `
        --hidden-import MetaTrader5 `
        --collect-submodules MetaTrader5 `
        --distpath "$root/dist" `
        --workpath "$root/build/pyinstaller" `
        "$root/run_app.py"
}

$exe = Join-Path $root "dist/MT5TradingWorkstation/MT5TradingWorkstation.exe"
$report = Join-Path $root "dist/self-check.txt"
Write-Host "::group::Frozen self-check"
$process = Start-Process -FilePath $exe -ArgumentList @("--self-check", "--report-file", $report) -Wait -PassThru
if (Test-Path $report) { Get-Content $report }
if ($process.ExitCode -ne 0) { throw "Frozen self-check failed with exit code $($process.ExitCode)" }
Write-Host "::endgroup::"

# Phase 2 acceptance on the real build: a forced crash must produce a masked crash report.
$crashReport = Join-Path $root "dist/crash-test.txt"
Write-Host "::group::Frozen crash test"
$crashArgs = @("--crash-test", "--profile", "ci-crash-test", "--report-file", $crashReport)
$process = Start-Process -FilePath $exe -ArgumentList $crashArgs -Wait -PassThru
if (Test-Path $crashReport) { Get-Content $crashReport }
if ($process.ExitCode -ne 0) { throw "Frozen crash test failed with exit code $($process.ExitCode)" }
Write-Host "::endgroup::"

$zip = Join-Path $root "dist/MT5TradingWorkstation-portable.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $root "dist/MT5TradingWorkstation") -DestinationPath $zip
Write-Host "Built $zip"
