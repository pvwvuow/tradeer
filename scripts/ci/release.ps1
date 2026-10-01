#Requires -Version 7
# Test, build, create the Inno Setup installer, checksums and latest.json, upload to the release.
param(
    [Parameter(Mandatory = $true)][string]$Tag
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path "$PSScriptRoot/../..").Path
Set-Location $root
$version = $Tag.TrimStart("v")

& "$PSScriptRoot/check.ps1"
& "$PSScriptRoot/build.ps1"

choco install innosetup -y --no-progress
if ($LASTEXITCODE -ne 0) { throw "Inno Setup install failed" }
$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6/ISCC.exe"
& $iscc "/DMyAppVersion=$version" (Join-Path $root "installer/MT5TradingWorkstation.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }

$release = Join-Path $root "dist/release"
New-Item -ItemType Directory -Force -Path $release | Out-Null
$installerName = "MT5TradingWorkstation-Setup-$version.exe"
Copy-Item (Join-Path $root "dist/installer/$installerName") (Join-Path $release $installerName)
$portableName = "MT5TradingWorkstation-$version-portable.zip"
Copy-Item (Join-Path $root "dist/MT5TradingWorkstation-portable.zip") (Join-Path $release $portableName)

$lines = foreach ($file in Get-ChildItem $release -File) {
    $hash = (Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($file.Name)"
}
Set-Content -Path (Join-Path $release "checksums.txt") -Value $lines -Encoding utf8

$installerHash = (Get-FileHash (Join-Path $release $installerName) -Algorithm SHA256).Hash
$repo = $env:GITHUB_REPOSITORY
$latest = [ordered]@{
    version               = $version
    notes_url             = "https://github.com/$repo/releases/tag/$Tag"
    installer_url         = "https://github.com/$repo/releases/download/$Tag/$installerName"
    installer_sha256      = $installerHash.ToLowerInvariant()
    published_at          = (Get-Date).ToUniversalTime().ToString("o")
    min_supported_version = "0.1.0"
}
$latest | ConvertTo-Json | Set-Content -Path (Join-Path $release "latest.json") -Encoding utf8

if ($env:GH_TOKEN) {
    gh release view $Tag *> $null
    if ($LASTEXITCODE -ne 0) {
        gh release create $Tag --title $Tag --generate-notes
        if ($LASTEXITCODE -ne 0) { throw "gh release create failed" }
    }
    $assets = (Get-ChildItem $release -File).FullName
    gh release upload $Tag @assets --clobber
    if ($LASTEXITCODE -ne 0) { throw "gh release upload failed" }
}
