# Push the working devops repo into the bare clone Jenkins reads (file:///repo).
$ErrorActionPreference = "Stop"
$JenkinsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $JenkinsDir "..\..")).Path
$BareRepo = Join-Path $JenkinsDir "repo-bare"

if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
    Write-Error "No git repo at $RepoRoot. Run setup-jenkins.ps1 first."
}

$branch = (git -C $RepoRoot rev-parse --abbrev-ref HEAD).Trim()
if (-not (Test-Path (Join-Path $BareRepo "HEAD"))) {
    Write-Host "Creating bare repo at $BareRepo ..."
    git clone --bare $RepoRoot $BareRepo
} else {
    Write-Host "Updating bare repo (branch $branch) ..."
    git -C $RepoRoot push --force $BareRepo "${branch}:${branch}"
}

Write-Host "Bare repo ready. Jenkins Git URL: file:///repo (branch */$branch)"
