@echo off
rem ============================================================
rem  MOONLIGHT - GUI launcher
rem  Double-click this file to open the MOONLIGHT bot control panel.
rem  It opens without a console window: the log is on the GUI's
rem  Dashboard and Diagnostics tabs and in logs\bot.log.
rem ============================================================
setlocal

rem Always run from the folder that holds this .bat (the repo root).
cd /d "%~dp0"

rem pyw (the py launcher's no-console twin, installed with Python from
rem python.org) picks the newest Python 3. Plain "pythonw" is the fallback.
set "PY=pyw -3"
where pyw >nul 2>nul || set "PY=pythonw"

rem "start" returns at once, so this window closes. A failed start shows
rem a message box and writes logs\bot.log.
start "" %PY% -m moonlight %*
