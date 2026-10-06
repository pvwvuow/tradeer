#Requires -Version 7
# Build, package with Velopack (installer, full package and a delta from the previous
# release), then upload everything to the GitHub release of the tag (ADR 113).
#
# The lint, type check, tests and the clean-install test are not run here again: branch
# protection merged this code only after ci, build and the Linux tests passed on its PR, and
# the release commit adds nothing but the version and the CHANGELOG. The build is still
# self-checked (build.ps1). Set RELEASE_RUN_CHECKS=1 to run every check anyway.
param(
    [Parameter(Mandatory = $true)][string]$Tag
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path "$PSScriptRoot/../..").Path
Set-Location $root
$version = $Tag.TrimStart("v")
$repo = $env:GITHUB_REPOSITORY
$repoUrl = "https://github.com/$repo"
$packId = "MT5TradingWorkstation"
$vpkVersion = "1.2.0"  # keep equal to the velopack pin in pyproject.toml

if ($env:RELEASE_RUN_CHECKS -eq "1") {
    & "$PSScriptRoot/check.ps1"
    & "$PSScriptRoot/build.ps1"
}
else {
    & "$PSScriptRoot/build.ps1" -SkipInstallTest
}

# Install-Vpk skips the install when build.ps1's clean-install test installed vpk already.
Write-Host "::group::Install vpk $vpkVersion"
. "$PSScriptRoot/vpk.ps1"
Install-Vpk $vpkVersion
Write-Host "::endgroup::"

$releases = Join-Path $root "dist/velopack"
New-Item -ItemType Directory -Force -Path $releases | Out-Null

# The newest earlier release with a Velopack feed: its full package lets vpk build the delta.
Write-Host "::group::Previous release for the delta"
$previous = $null
if ($env:GH_TOKEN) {
    $list = gh release list --repo $repo --limit 30 --json tagName,isDraft | ConvertFrom-Json
    foreach ($item in $list) {
        if ($item.isDraft -or $item.tagName -eq $Tag) { continue }
        $assets = (gh release view $item.tagName --repo $repo --json assets | ConvertFrom-Json).assets
        $names = @($assets | ForEach-Object { $_.name })
        if ($names -contains "releases.win.json") { $previous = $item.tagName; break }
    }
}
if ($previous) {
    Write-Host "Delta from $previous"
    gh release download $previous --repo $repo --dir $releases --pattern "*-full.nupkg" --clobber
    if ($LASTEXITCODE -ne 0) { Write-Host "::warning::Could not download $previous; this release has no delta"; $previous = $null }
}
else {
    Write-Host "No earlier Velopack release: this one is full only."
}
Write-Host "::endgroup::"

# Release notes for the installer and the update dialog: this version's CHANGELOG section.
$notes = Join-Path $root "dist/release-notes.md"
$changelog = Get-Content (Join-Path $root "CHANGELOG.md") -Raw
$pattern = "(?ms)^## \[?$([regex]::Escape($version))\]?.*?(?=^## |\z)"
$match = [regex]::Match($changelog, $pattern)
$text = if ($match.Success) { $match.Value.Trim() } else { "Version $version" }
Set-Content -Path $notes -Value $text -Encoding utf8

Write-Host "::group::vpk pack"
vpk pack `
    --packId $packId `
    --packVersion $version `
    --packDir (Join-Path $root "dist/MT5TradingWorkstation") `
    --mainExe "MT5TradingWorkstation.exe" `
    --packTitle "MT5 Trading Workstation" `
    --packAuthors "pvwvuow" `
    --releaseNotes $notes `
    --skipVeloAppCheck `
    --outputDir $releases
if ($LASTEXITCODE -ne 0) { throw "vpk pack failed" }
Get-ChildItem $releases | ForEach-Object { Write-Host "$($_.Name)  $([math]::Round($_.Length / 1MB, 1)) MB" }
Write-Host "::endgroup::"

# checksums.txt and latest.json (spec J1) next to the Velopack files.
$setupName = "$packId-win-Setup.exe"
$extra = Join-Path $root "dist/release"
New-Item -ItemType Directory -Force -Path $extra | Out-Null
$lines = foreach ($file in Get-ChildItem $releases -File) {
    if ($previous -and $file.Name -like "*-full.nupkg" -and $file.Name -notlike "*-$version-full.nupkg") { continue }
    $hash = (Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($file.Name)"
}
Set-Content -Path (Join-Path $extra "checksums.txt") -Value $lines -Encoding utf8
$setup = Join-Path $releases $setupName
$latest = [ordered]@{
    version               = $version
    notes_url             = "$repoUrl/releases/tag/$Tag"
    installer_url         = "$repoUrl/releases/download/$Tag/$setupName"
    installer_sha256      = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLowerInvariant()
    published_at          = (Get-Date).ToUniversalTime().ToString("o")
    min_supported_version = "0.12.0"
    delta_from            = if ($previous) { $previous.TrimStart("v") } else { "" }
}
$latest | ConvertTo-Json | Set-Content -Path (Join-Path $extra "latest.json") -Encoding utf8

if ($env:GH_TOKEN) {
    Write-Host "::group::Upload"
    vpk upload github --outputDir $releases --repoUrl $repoUrl --token $env:GH_TOKEN `
        --tag $Tag --releaseName $Tag --merge --publish
    if ($LASTEXITCODE -ne 0) { throw "vpk upload failed" }
    gh release upload $Tag (Get-ChildItem $extra -File).FullName --repo $repo --clobber
    if ($LASTEXITCODE -ne 0) { throw "gh release upload failed" }
    Write-Host "::endgroup::"
}
