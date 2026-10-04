#Requires -Version 7
# Clean-install test (spec G3 phase 16): pack the build with Velopack, install it with
# Setup.exe exactly as a user does (per user, no admin rights, nothing installed before) and
# run the installed app's self-check. GitHub's Windows runners are a new virtual machine for
# every job, so this is a clean Windows PC. The packages made here are never uploaded.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path "$PSScriptRoot/../..").Path
Set-Location $root
. "$PSScriptRoot/vpk.ps1"

$packId = "MT5TradingWorkstation"
$pin = Select-String -Path (Join-Path $root "pyproject.toml") -Pattern 'velopack==([0-9][0-9.]*)'
if (-not $pin) { throw "The velopack pin is missing from pyproject.toml" }
Install-Vpk $pin.Matches[0].Groups[1].Value
$version = (python -c "from app.__version__ import __version__; print(__version__)").Trim()

$packDir = Join-Path $root "dist/$packId"
$output = Join-Path $root "dist/install-test"
if (Test-Path $output) { Remove-Item $output -Recurse -Force }
New-Item -ItemType Directory -Force -Path $output | Out-Null

Write-Host "::group::vpk pack $version (for the install test only)"
vpk pack `
    --packId $packId `
    --packVersion $version `
    --packDir $packDir `
    --mainExe "$packId.exe" `
    --packTitle "MT5 Trading Workstation" `
    --packAuthors "pvwvuow" `
    --skipVeloAppCheck `
    --outputDir $output
if ($LASTEXITCODE -ne 0) { throw "vpk pack failed with exit code $LASTEXITCODE" }
Write-Host "::endgroup::"

$setup = Join-Path $output "$packId-win-Setup.exe"
if (-not (Test-Path -LiteralPath $setup)) { throw "vpk pack made no $packId-win-Setup.exe" }
$target = Join-Path ([System.IO.Path]::GetTempPath()) "tw-clean-install"
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
$setupLog = Join-Path $root "dist/install-test-setup.log"

# --silent: no dialogs and no start of the app after the install (Velopack Setup 1.2).
Write-Host "::group::Install with Setup.exe --silent"
$setupArgs = @("--silent", "--installto", $target, "--log", $setupLog)
$process = Start-Process -FilePath $setup -ArgumentList $setupArgs -PassThru
$null = $process.Handle  # keeps the exit code readable after the process ends
if (-not $process.WaitForExit(300000)) {
    $process.Kill($true)
    throw "Setup.exe did not finish within 5 minutes"
}
$process.WaitForExit()
if (Test-Path $setupLog) { Get-Content $setupLog -Tail 40 }
if ($process.ExitCode -ne 0) { throw "Setup.exe failed with exit code $($process.ExitCode)" }
Write-Host "::endgroup::"

$installed = Join-Path $target "current/$packId.exe"
$required = @(
    (Join-Path $target "Update.exe"),
    $installed,
    (Join-Path $target "current/_internal/app/ui/fonts/Vazirmatn.ttf")
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path)) { throw "The installed app is missing $path" }
}
Write-Host "Installed to $target"

$programs = [Environment]::GetFolderPath("Programs")
$shortcut = Get-ChildItem -Path $programs -Filter "*.lnk" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.BaseName -like "MT5 Trading Workstation*" } |
    Select-Object -First 1
if ($shortcut) { Write-Host "Start menu shortcut: $($shortcut.FullName)" }
else { Write-Host "::warning::Setup.exe made no Start menu shortcut" }
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$packId"
if (Test-Path $uninstallKey) { Write-Host "Listed in Installed apps (uninstall entry found)" }
else { Write-Host "::warning::No uninstall entry under $uninstallKey" }

Write-Host "::group::Installed self-check"
$report = Join-Path $root "dist/installed-self-check.txt"
$checkArgs = @("--self-check", "--report-file", $report)
$check = Start-Process -FilePath $installed -ArgumentList $checkArgs -Wait -PassThru
if (Test-Path $report) { Get-Content $report }
if ($check.ExitCode -ne 0) { throw "Installed self-check failed with exit code $($check.ExitCode)" }
Write-Host "::endgroup::"
Write-Host "Clean install test passed: Setup.exe installed $version and its self-check passed"
