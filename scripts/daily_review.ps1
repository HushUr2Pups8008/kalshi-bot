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

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$dailyReviewPy = Join-Path $repoRoot "scripts\daily_review.py"

if (-not (Test-Path $dailyReviewPy)) {
    throw "daily_review.py not found at: $dailyReviewPy"
}

if (Test-Path $venvPython) {
    $pythonExe = $venvPython
}
else {
    $pythonExe = "python"
}

$argsList = @($dailyReviewPy)
if ($Since) {
    $argsList += @("--since", $Since)
}
if ($Until) {
    $argsList += @("--until", $Until)
}
if ($PSBoundParameters.ContainsKey("Top")) {
    $argsList += @("--top", $Top)
}
if ($ExcludeTest) {
    $argsList += "--exclude-test"
}

& $pythonExe @argsList
exit $LASTEXITCODE
