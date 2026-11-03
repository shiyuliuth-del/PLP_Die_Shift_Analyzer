@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
echo Building Die Shift Analyze with your existing Windows Python environment...
call "%~dp0RUN.bat" build
if errorlevel 1 goto failed
if not exist "%~dp0dist\Die_Shift_Analyze.exe" goto failed
echo Ready: "%~dp0dist\Die_Shift_Analyze.exe"
if not defined CI pause
exit /b 0
:failed
echo Build failed. The existing environment needs the packages in requirements.txt.
echo Set DIE_SHIFT_PYTHON to its python.exe if needed.
if not defined CI pause
exit /b 1
