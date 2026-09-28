@echo off
setlocal
cd /d "%~dp0"
if not exist results mkdir results
call RUN.bat --smoke-test results\windows_source_smoke.json
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m unittest discover -s tests -p test_v13.py -v > results\windows_v13_tests.log 2>&1
if errorlevel 1 goto failed
type results\windows_source_smoke.json
echo Windows source tests passed. Details: results\windows_v13_tests.log
pause
exit /b 0
:failed
type results\windows_v13_tests.log
pause
exit /b 1
