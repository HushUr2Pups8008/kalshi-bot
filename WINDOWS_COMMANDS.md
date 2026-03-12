# Kalshi Bot — Windows Quick Reference

---

## Service Control
> Run from **elevated PowerShell** (right-click -> Run as Administrator)

```powershell
# kalshi-bot
Start-Service kalshi-bot
Stop-Service kalshi-bot
Restart-Service kalshi-bot
C:\nssm\nssm.exe status kalshi-bot

# Ollama LLM server (kalshi-bot depends on this — start it first)
Start-Service ollama
Stop-Service ollama
C:\nssm\nssm.exe status ollama
```

> **Dependency order:** `ollama` must be running before `kalshi-bot`.
> On reboot this is automatic. If you manually stop `ollama`, stop `kalshi-bot` first.

---

## Watching Logs
> Run from **Git Bash** terminal in VS Code

```bash
# Main log -- everything the bot does
tail -f e:/VS_Code/kalshi-bot/logs/bot.log

# Trade log -- signals, opportunities, paper trades (raw JSON)
tail -f e:/VS_Code/kalshi-bot/logs/trades.jsonl

# Ollama LLM server logs
tail -f e:/VS_Code/kalshi-bot/logs/ollama_stdout.log
tail -f e:/VS_Code/kalshi-bot/logs/ollama_stderr.log
```

---

## Running the Bot Directly (Dev Mode)
> Stop the service first, then run in Git Bash for colored console output

```powershell
# Elevated PowerShell
Stop-Service kalshi-bot
```
```bash
# Git Bash
cd "e:/VS_Code/kalshi-bot"
.venv/Scripts/python main.py
```
> `Ctrl+C` to stop. Restart the service when stepping away.

---

## Updating the Codebase
```bash
cd "e:/VS_Code/kalshi-bot"
git pull
```
Then restart the service from elevated PowerShell:
```powershell
Restart-Service kalshi-bot
```
> No need to restart Ollama unless you changed the model.

---

## Bot CLI Commands
```bash
cd "e:/VS_Code/kalshi-bot"

# Print full paper trading performance report
.venv/Scripts/python main.py --report

# Print source credibility table
.venv/Scripts/python main.py --credibility

# Manually resolve a paper trade (YES or NO)
.venv/Scripts/python main.py --resolve TICKER YES
.venv/Scripts/python main.py --resolve TICKER NO

# Switch to live trading (interactive -- requires typing CONFIRM)
.venv/Scripts/python main.py --go-live
```

---

## Wiping Paper Trade History (Fresh Start)
```bash
rm "e:/VS_Code/kalshi-bot/data/paper_trades.db"
```
> The DB will be recreated automatically on next start with a clean slate.

---

## Reinstall Both Services (after major changes)
> Elevated PowerShell

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
& "E:\VS_Code\kalshi-bot\setup_service.ps1"
```
> This installs/reinstalls **both** `ollama` and `kalshi-bot` and sets the dependency.

---

## Ollama Model Management
```bash
# List installed models
"C:\Users\jrp52\AppData\Local\Programs\Ollama\ollama.exe" list

# Pull a new/updated model (run while Ollama service is running)
"C:\Users\jrp52\AppData\Local\Programs\Ollama\ollama.exe" pull qwen2.5:3b
```
> To change the model, update `OLLAMA_MODEL=<model>` in `.env` and restart kalshi-bot.

---

## Git -- Push Changes to GitLab
```bash
cd "e:/VS_Code/kalshi-bot"
git add -p               # stage changes interactively
git commit -m "message"
git push
```
