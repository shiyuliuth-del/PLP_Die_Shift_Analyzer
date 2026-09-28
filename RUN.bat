@echo off
setlocal
cd /d "%~dp0"
call SELECT_PYTHON.bat
if errorlevel 1 goto failed
"%DSA_PYTHON%" main.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo Offline startup failed. See output and startup-error.log. No dependencies were installed.
pause
exit /b 1
