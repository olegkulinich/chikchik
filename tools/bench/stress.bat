@echo off
rem stress.bat — see the docstring of stress.py. Makes .venv here on first run.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Creating .venv ^(first run only^)...
    python -m venv .venv
    if errorlevel 1 goto error
    .venv\Scripts\python -m pip install -q -r requirements.txt
    if errorlevel 1 goto error
)
.venv\Scripts\python stress.py %*
pause
exit /b 0
:error
echo Setup failed. Delete the .venv folder and try again.
pause
exit /b 1
