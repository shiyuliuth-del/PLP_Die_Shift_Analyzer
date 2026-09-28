@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m die_shift_studio.dependencies >nul 2>&1
if errorlevel 1 (
  echo Installing / repairing application dependencies...
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m die_shift_studio.dependencies
if errorlevel 1 goto failed
.venv\Scripts\python.exe main.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. See output and startup-error.log.
pause
exit /b 1
