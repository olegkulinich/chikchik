@echo off
rem Three stress tests in a row, unattended (~4.5 h):
rem   1. MPC-BE 90 min   2. ChikChik 90 min   3. ChikChik 90 min with OBS running
rem OBS must already have a scene with a Display Capture source (it captures while open).
rem Ctrl+Alt+Q stops the current test AND the rest of the chain.
setlocal
cd /d "%~dp0"
set MINUTES=90
if not "%~1"=="" set MINUTES=%~1
set OBS_DIR=C:\Program Files\obs-studio\bin\64bit

if not exist .venv\Scripts\python.exe (
    echo Creating .venv ^(first run only^)...
    python -m venv .venv || goto error
    .venv\Scripts\python -m pip install -q -r requirements.txt || goto error
)
set PY=.venv\Scripts\python

echo === 1/3 MPC-BE, %MINUTES% min ===
%PY% stress.py MPC-BE %MINUTES% --quiet
if errorlevel 2 goto stopped
timeout /t 10 /nobreak >nul

echo === 2/3 ChikChik, %MINUTES% min ===
%PY% stress.py ChikChik %MINUTES% --quiet
if errorlevel 2 goto stopped
timeout /t 10 /nobreak >nul

echo === 3/3 ChikChik with OBS, %MINUTES% min ===
if not exist "%OBS_DIR%\obs64.exe" (
    echo OBS not found in %OBS_DIR% - skipping test 3.
    goto done
)
start "" /d "%OBS_DIR%" obs64.exe --minimize-to-tray --disable-shutdown-check
timeout /t 20 /nobreak >nul
%PY% stress.py ChikChik %MINUTES% --quiet --note "OBS running (display capture)"
set RESULT=%errorlevel%
taskkill /im obs64.exe >nul 2>&1
timeout /t 10 /nobreak >nul
taskkill /f /im obs64.exe >nul 2>&1
if "%RESULT%"=="2" goto stopped

:done
%PY% -c "import winutil as w; w.message('Stress tests - done', 'All runs finished.\n\nResults: tools\\bench\\results\\stress_*\nSend summary.txt, stress.log and metrics.csv from each folder.')"
exit /b 0

:stopped
echo Stopped by user - the remaining tests were skipped.
pause
exit /b 2

:error
echo Setup failed. Delete the .venv folder and try again.
pause
exit /b 1
