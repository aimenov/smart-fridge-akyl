# Create the GitHub repo and push (requires: gh auth login first)
# Usage from repo root: .\scripts\push-to-github.ps1

$ErrorActionPreference = "Stop"
$ghDir = Join-Path $env:ProgramFiles "GitHub CLI"
$gh = Join-Path $ghDir "gh.exe"
if (-not (Test-Path $gh)) {
    Write-Error "GitHub CLI not found. Install: winget install GitHub.cli"
}
$env:Path = "$ghDir;$env:Path"

& $gh auth status | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Not logged in. Run (browser opens):"
    Write-Host "  gh auth login -p https -h github.com -w"
    Write-Host ""
    exit 1
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$repoName = "smart-fridge-akyl"
Write-Host "Creating GitHub repo '$repoName' and pushing..."
& $gh repo create $repoName --public --source=. --remote=origin --push
exit $LASTEXITCODE
