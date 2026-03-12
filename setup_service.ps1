# Run this script as Administrator (right-click PowerShell -> Run as Administrator)
# Installs Ollama LLM service and kalshi-bot as Windows Services using NSSM
# Ollama is registered first; kalshi-bot depends on it (starts after, stops before).

$NssmPath    = "C:\nssm\nssm.exe"
$Python      = "E:\VS_Code\kalshi-bot\.venv\Scripts\python.exe"
$Script      = "E:\VS_Code\kalshi-bot\main.py"
$WorkingDir  = "E:\VS_Code\kalshi-bot"
$LogDir      = "E:\VS_Code\kalshi-bot\logs"
$OllamaExe   = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
$OllamaModels = "$env:USERPROFILE\.ollama\models"

Write-Host "Configuring power plan for 24/7 operation..." -ForegroundColor Cyan
powercfg /change standby-timeout-ac 0     # Never sleep on AC power
powercfg /change monitor-timeout-ac 15    # Display off after 15 min (saves heat)
powercfg /hibernate off                   # Disable hibernate
Write-Host "Power plan configured." -ForegroundColor Green

# ── Ollama LLM service ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Installing Ollama LLM service..." -ForegroundColor Cyan

& $NssmPath stop  ollama 2>$null
& $NssmPath remove ollama confirm 2>$null

& $NssmPath install ollama $OllamaExe serve
& $NssmPath set ollama DisplayName "Ollama LLM Server"
& $NssmPath set ollama Description "Local LLM inference server (qwen2.5:3b) for kalshi-bot"
& $NssmPath set ollama Start SERVICE_AUTO_START
# OLLAMA_MODELS is required so the SYSTEM account can find user-installed models
& $NssmPath set ollama AppEnvironmentExtra "OLLAMA_MODELS=$OllamaModels"
& $NssmPath set ollama AppStdout "$LogDir\ollama_stdout.log"
& $NssmPath set ollama AppStderr "$LogDir\ollama_stderr.log"

Write-Host "Ollama service installed." -ForegroundColor Green

# ── kalshi-bot service ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Installing kalshi-bot Windows service..." -ForegroundColor Cyan

& $NssmPath stop  kalshi-bot 2>$null
& $NssmPath remove kalshi-bot confirm 2>$null

& $NssmPath install kalshi-bot $Python $Script
& $NssmPath set kalshi-bot AppDirectory $WorkingDir
& $NssmPath set kalshi-bot AppStdout "$LogDir\service_stdout.log"
& $NssmPath set kalshi-bot AppStderr "$LogDir\service_stderr.log"
& $NssmPath set kalshi-bot AppRotateFiles 1
& $NssmPath set kalshi-bot AppRotateBytes 10485760   # rotate at 10 MB
& $NssmPath set kalshi-bot AppExit Default Restart
& $NssmPath set kalshi-bot AppRestartDelay 5000      # wait 5s before restart
& $NssmPath set kalshi-bot Start SERVICE_AUTO_START

# Ensure kalshi-bot starts AFTER Ollama on every boot
sc.exe config kalshi-bot depend= ollama

Write-Host "kalshi-bot service installed (depends on ollama)." -ForegroundColor Green

# ── Start both services ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting services..." -ForegroundColor Cyan
& $NssmPath start ollama
Start-Sleep 3   # give Ollama a moment to bind its port
& $NssmPath start kalshi-bot

Write-Host ""
Write-Host "Done. Checking service status..." -ForegroundColor Cyan
& $NssmPath status ollama
& $NssmPath status kalshi-bot
