<#
Usage:
  From the scripts/ directory on systems with PowerShell execution policy restrictions:
    powershell -ExecutionPolicy Bypass -File .\daily_review.ps1

  From the repo root:
    powershell -ExecutionPolicy Bypass -File .\scripts\daily_review.ps1
#>

param(
    [string]$Since,
    [string]$Until,
    [int]$Top,
    [switch]$ExcludeTest
)

$ErrorActionPreference = "Continue"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$reportsDir = Join-Path $repoRoot "logs\reports"
$null = New-Item -ItemType Directory -Path $reportsDir -Force
$reportPath = Join-Path $reportsDir ("daily_review_{0}.txt" -f (Get-Date -Format "yyyyMMdd"))

if (Test-Path $venvPython) {
    $pythonExe = $venvPython
} else {
    $pythonExe = "python"
}

$sharedArgs = @()
if ($Since) {
    $sharedArgs += @("--since", $Since)
}
if ($Until) {
    $sharedArgs += @("--until", $Until)
}
if ($PSBoundParameters.ContainsKey("Top")) {
    $sharedArgs += @("--top", $Top)
}
if ($ExcludeTest) {
    $sharedArgs += "--exclude-test"
}

$reports = @(
    @{
        Title = "Trade Log Summary"
        Script = "scripts/trade_log_summary.py"
    },
    @{
        Title = "Decision Funnel Summary"
        Script = "scripts/decision_funnel_summary.py"
    },
    @{
        Title = "Paper Performance Drilldown"
        Script = "scripts/paper_performance_drilldown.py"
    }
)

function Write-ReportLine {
    param(
        [string]$Text = ""
    )

    $Text | Tee-Object -FilePath $reportPath -Append
}

foreach ($report in $reports) {
    Write-ReportLine ""
    Write-ReportLine ("=" * 72)
    Write-ReportLine $report.Title
    Write-ReportLine ("=" * 72)

    $scriptPath = Join-Path $repoRoot $report.Script
    $args = @($scriptPath) + $sharedArgs

    try {
        & $pythonExe @args 2>&1 | Tee-Object -FilePath $reportPath -Append
        if ($LASTEXITCODE -ne 0) {
            Write-ReportLine ("WARNING: {0} exited with code {1}" -f $report.Script, $LASTEXITCODE)
        }
    } catch {
        Write-ReportLine ("WARNING: Failed to run {0}: {1}" -f $report.Script, $_.Exception.Message)
    }
}

Write-ReportLine ""
Write-ReportLine ("Daily review report saved to: {0}" -f $reportPath)
