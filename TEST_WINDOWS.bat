@echo off
setlocal
cd /d "%~dp0"
call SELECT_PYTHON.bat
if errorlevel 1 goto failed
if not exist results mkdir results
"%DSA_PYTHON%" main.py --smoke-test results\windows_source_smoke.json
if errorlevel 1 goto failed
set "DIE_SHIFT_NATIVE_BACKEND=desktop"
"%DSA_PYTHON%" main.py --smoke-test results\windows_desktop_smoke.json
if errorlevel 1 goto failed
"%DSA_PYTHON%" -m unittest discover -s tests -p test_v14.py -v > results\windows_v14_tests.log 2>&1
if errorlevel 1 goto failed
"%DSA_PYTHON%" -m unittest discover -s tests -p test_v15.py -v > results\windows_v15_tests.log 2>&1
if errorlevel 1 goto failed
"%DSA_PYTHON%" -m unittest discover -s tests -p test_v16.py -v > results\windows_v16_tests.log 2>&1
if errorlevel 1 goto failed
"%DSA_PYTHON%" -m unittest discover -s tests -p test_v17.py -v > results\windows_v17_tests.log 2>&1
if errorlevel 1 goto failed
"%DSA_PYTHON%" -m unittest discover -s tests -p test_v18.py -v > results\windows_v18_tests.log 2>&1
if errorlevel 1 goto failed
type results\windows_desktop_smoke.json
echo Windows offline source tests passed.
pause
exit /b 0
:failed
echo Offline validation failed. See results and startup-error.log.
pause
exit /b 1
