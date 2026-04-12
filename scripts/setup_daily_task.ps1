<#
Registers a Windows Scheduled Task that runs scripts/daily_review.ps1 once per day.

Examples:
  powershell -ExecutionPolicy Bypass -File .\setup_daily_task.ps1
  powershell -ExecutionPolicy Bypass -File .\setup_daily_task.ps1 -Time 14:30
#>

param(
    [string]$Time = "09:00",
    [string]$TaskName = "KalshiBotDailyReview"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$dailyReviewPath = Join-Path $repoRoot "scripts\daily_review.ps1"

if (-not (Test-Path $dailyReviewPath)) {
    throw "daily_review.ps1 not found at: $dailyReviewPath"
}

try {
    $runAt = [datetime]::ParseExact($Time, "HH:mm", $null)
} catch {
    throw "Invalid -Time value '$Time'. Use 24-hour HH:mm format, for example 09:00 or 14:30."
}

$powerShellExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path $powerShellExe)) {
    $powerShellExe = "powershell.exe"
}

$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$escapedScriptPath = '"' + $dailyReviewPath + '"'
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -File $escapedScriptPath"

$action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $actionArgs -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $runAt

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId $currentIdentity `
    -LogonType S4U `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Runs the Kalshi bot daily review reporting wrapper once per day." `
    -Force

Write-Host ""
Write-Host "Scheduled task registered."
Write-Host "  Name : $TaskName"
Write-Host "  Time : $Time"
Write-Host "  Task : $dailyReviewPath"
Write-Host "  Note : S4U logon is best-effort and may depend on local system/account support."
Write-Host ""
Write-Host "Verify:"
Write-Host "  Get-ScheduledTask -TaskName `"$TaskName`""
Write-Host ""
Write-Host "Remove:"
Write-Host "  Unregister-ScheduledTask -TaskName `"$TaskName`" -Confirm:`$false"
