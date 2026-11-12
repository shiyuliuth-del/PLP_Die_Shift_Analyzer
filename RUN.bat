@echo off
setlocal
cd /d "%~dp0"
if not defined DSA_WINDOWLESS_LAUNCH (
  start "" "%SystemRoot%\System32\wscript.exe" "%~dp0RUN.vbs" %*
  exit /b
)
call SELECT_PYTHON.bat
if errorlevel 1 exit /b 2
for %%F in ("%DSA_PYTHON%") do if /i "%%~nxF"=="python.exe" if exist "%%~dpFpythonw.exe" set "DSA_PYTHON=%%~dpFpythonw.exe"
"%DSA_PYTHON%" main.py %*
exit /b %errorlevel%
