<#
.SYNOPSIS
    Run a shell command on an EC2 instance through AWS Systems Manager.
    No SSH key or inbound port 22 is needed.

.DESCRIPTION
    Requires: AWS CLI v2 signed in (aws login), and an instance that is
    "Online" in Systems Manager (SSM agent + an instance role with the
    AmazonSSMManagedInstanceCore policy).

.EXAMPLE
    .\scripts\ssm-run.ps1 -InstanceId i-0da98ca5ffe3a0edf -Command "sudo systemctl is-active sample-app"
#>
param(
    [Parameter(Mandatory = $true)] [string] $InstanceId,
    [Parameter(Mandatory = $true)] [string] $Command,
    [string] $Region = "ap-southeast-2",
    [int] $TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

# Passing JSON inline to the AWS CLI is unreliable in PowerShell, so use a file.
$paramFile = New-TemporaryFile
try {
    @{ commands = @($Command) } | ConvertTo-Json -Compress |
        Set-Content -Path $paramFile.FullName -Encoding ascii

    $commandId = aws ssm send-command `
        --region $Region `
        --instance-ids $InstanceId `
        --document-name AWS-RunShellScript `
        --parameters "file://$($paramFile.FullName)" `
        --query Command.CommandId `
        --output text

    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Remove-Item $paramFile.FullName -ErrorAction SilentlyContinue
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$result = $null

do {
    Start-Sleep -Seconds 2
    $json = aws ssm get-command-invocation `
        --region $Region `
        --command-id $commandId `
        --instance-id $InstanceId `
        --output json 2>$null

    if ($LASTEXITCODE -eq 0) {
        $result = ($json -join "`n") | ConvertFrom-Json
    }
} while (
    ((-not $result) -or ($result.Status -in @("Pending", "InProgress", "Delayed"))) -and
    ((Get-Date) -lt $deadline)
)

if (-not $result) {
    Write-Error "No result from instance $InstanceId within ${TimeoutSeconds}s (command $commandId). Is it Online in Systems Manager > Fleet Manager?"
    exit 1
}

if ($result.StandardOutputContent) { Write-Output $result.StandardOutputContent.TrimEnd() }
if ($result.StandardErrorContent) { [Console]::Error.WriteLine($result.StandardErrorContent.TrimEnd()) }

if ($result.Status -ne "Success") {
    Write-Host "Command status: $($result.Status)"
    exit 1
}
