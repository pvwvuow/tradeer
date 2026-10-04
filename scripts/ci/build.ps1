#Requires -Version 7
# Build the one-folder Windows app with PyInstaller, verify it with --self-check, zip it, then
# install it with the real Setup.exe on this clean machine and check it again (install-test.ps1).
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

# The Persian font (spec F1: Vazirmatn 33.003, SIL Open Font License), pinned to one
# google/fonts commit and checked by its SHA-256 before it is bundled.
$fontCommit = "6f9713a50c628d79f60259319d05fa0a239a9a7f"
$fontSha256 = "696249a2c74b39ffdef55de4df2809c5b639d3ff80d618d8160a095d2fd49dca"
$fontDir = Join-Path $root "app/ui/fonts"

Invoke-Step "Install" { python -m pip install -e ".[dev]" }
Invoke-Step "Persian font" {
    New-Item -ItemType Directory -Force -Path $fontDir | Out-Null
    $base = "https://raw.githubusercontent.com/google/fonts/$fontCommit/ofl/vazirmatn"
    $font = Join-Path $fontDir "Vazirmatn.ttf"
    Invoke-WebRequest -Uri "$base/Vazirmatn%5Bwght%5D.ttf" -OutFile $font
    Invoke-WebRequest -Uri "$base/OFL.txt" -OutFile (Join-Path $fontDir "OFL.txt")
    $hash = (Get-FileHash -LiteralPath $font -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $fontSha256) { throw "Vazirmatn font has SHA-256 $hash, expected $fontSha256" }
    Write-Host "Vazirmatn.ttf $((Get-Item -LiteralPath $font).Length) bytes, SHA-256 ok"
}
Invoke-Step "Source self-check" { python -m app --self-check }
Invoke-Step "PyInstaller" {
    # numpy is only imported at C level inside MetaTrader5's compiled extension,
    # which PyInstaller's static analysis cannot see, so collect it explicitly.
    pyinstaller --noconfirm --clean --onedir --windowed `
        --name MT5TradingWorkstation `
        --paths "$root" `
        --hidden-import MetaTrader5 `
        --hidden-import keyring.backends.Windows `
        --hidden-import velopack `
        --collect-submodules MetaTrader5 `
        --collect-all numpy `
        --collect-all lightgbm `
        --collect-submodules scipy.sparse `
        --add-data "$root/app/calendar/mql5/CalendarExporter.mq5;app/calendar/mql5" `
        --add-data "$fontDir;app/ui/fonts" `
        --distpath "$root/dist" `
        --workpath "$root/build/pyinstaller" `
        "$root/run_app.py"
}

$bundledFont = Join-Path $root "dist/MT5TradingWorkstation/_internal/app/ui/fonts/Vazirmatn.ttf"
if (-not (Test-Path -LiteralPath $bundledFont)) { throw "The Persian font is missing from the build" }

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

# Phase 16c: the installer a user downloads must install and start on a clean Windows PC.
& "$PSScriptRoot/install-test.ps1"
