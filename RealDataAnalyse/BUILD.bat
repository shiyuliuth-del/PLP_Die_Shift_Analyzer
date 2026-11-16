@echo off
setlocal DisableDelayedExpansion
call "%~dp0RUN.bat" build
if errorlevel 1 goto failed
if not exist "%~dp0..\dist\RealDataAnalyse.exe" goto failed
echo Ready: "%~dp0..\dist\RealDataAnalyse.exe"
if not defined CI pause
exit /b 0
:failed
echo Build failed. Install RealDataAnalyse requirements in your existing environment.
if not defined CI pause
exit /b 1
