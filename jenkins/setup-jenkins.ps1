# Start local Jenkins (Docker) for the EC2 start utility lab.
$ErrorActionPreference = "Stop"
$JenkinsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $JenkinsDir "..\..")).Path

Write-Host "Repo root: $RepoRoot"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is required. Install Docker Desktop and try again."
}

if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
    Write-Host "Initializing git repo (required for Jenkins Pipeline from SCM)..."
    Push-Location $RepoRoot
    git init -b main
    git add .
    git commit -m "Initial commit for Jenkins pipeline"
    Pop-Location
} else {
    Write-Host "Git repo already exists at $RepoRoot"
}

& (Join-Path $JenkinsDir "sync-bare-repo.ps1")

Push-Location $JenkinsDir
docker compose pull
docker compose up -d
Pop-Location

Write-Host ""
Write-Host "Jenkins UI: http://localhost:8080"
Write-Host "Unlock password: docker exec ec2-start-jenkins cat /var/jenkins_home/secrets/initialAdminPassword"
Write-Host ""
Write-Host "Jenkins job Git URL: file:///repo  Branch: */main  Script: ec2-start-utility/Jenkinsfile"
Write-Host "After code changes, run: .\sync-bare-repo.ps1"
Write-Host "See README section 'Local Jenkins (Docker on Windows)'."
