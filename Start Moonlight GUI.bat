@echo off
rem ============================================================
rem  MOONLIGHT - GUI launcher
rem  Double-click this file to open the MOONLIGHT bot control panel.
rem ============================================================
setlocal

rem Always run from the folder that holds this .bat (the repo root).
cd /d "%~dp0"

rem Uses whichever "python" is on PATH (it needs the GUI deps installed).
set "PY=python"

echo.
echo  Starting MOONLIGHT GUI...
echo  Python : %PY%
echo  Close the MOONLIGHT window to stop.
echo.

"%PY%" -m moonlight %*
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
