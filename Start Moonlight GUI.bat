@echo off
rem ============================================================
rem  MOONLIGHT - GUI launcher
rem  Double-click this file to open the MOONLIGHT bot control panel.
rem ============================================================
setlocal

rem Always run from the folder that holds this .bat (the repo root).
cd /d "%~dp0"

rem The py launcher (installed with Python from python.org) picks the newest
rem Python 3. Plain "python" can be the Microsoft Store placeholder, which
rem only prints "Python not found". The Python needs the GUI deps installed.
set "PY=py -3"
where py >nul 2>nul || set "PY=python"

echo.
echo  Starting MOONLIGHT GUI...
echo  Python : %PY%
echo  Close the MOONLIGHT window to stop.
echo.

%PY% -m moonlight %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo  MOONLIGHT GUI closed.
) else (
    echo.
    echo  *** MOONLIGHT GUI exited with code %RC% ***
    echo  Look above for the error / traceback.
)
echo.
pause
