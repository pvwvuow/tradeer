#Requires -Version 7
# The Velopack command-line tool, installed once per machine: build.ps1 (the clean-install
# test) and release.ps1 (the real packages) both need it, in the same job during a release.

function Install-Vpk([string]$Version) {
    $tools = Join-Path $env:USERPROFILE ".dotnet\tools"
    if (($env:PATH -split ";") -notcontains $tools) { $env:PATH = "$env:PATH;$tools" }
    $listed = (dotnet tool list --global) -join "`n"
    if ($listed -match "(?m)^vpk\s+$([regex]::Escape($Version))\s") {
        Write-Host "vpk $Version is already installed"
        return
    }
    $verb = if ($listed -match "(?m)^vpk\s") { "update" } else { "install" }
    dotnet tool $verb --global vpk --version $Version
    if ($LASTEXITCODE -ne 0) { throw "vpk $verb failed with exit code $LASTEXITCODE" }
}
