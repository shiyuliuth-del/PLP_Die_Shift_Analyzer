@echo off
setlocal
cd /d "%~dp0"
call SELECT_PYTHON.bat
if errorlevel 1 goto failed
"%DSA_PYTHON%" -m die_shift_studio.dependencies
if errorlevel 1 goto failed
"%DSA_PYTHON%" -m PyInstaller --clean --noconfirm Die_Shift_Analyze.spec
if errorlevel 1 goto failed
start /wait "" dist\Die_Shift_Analyze.exe --smoke-test dist\windows_smoke.json
if errorlevel 1 goto failed
"%DSA_PYTHON%" -c "import json; p=json.load(open('dist/windows_smoke.json',encoding='utf-8')); assert p['status']=='PASS',p; print('Frozen GUI smoke: PASS')"
if errorlevel 1 goto failed
echo Ready: dist\Die_Shift_Analyze.exe
pause
exit /b 0
:failed
echo Offline build failed. Use an existing complete build environment with PyInstaller.
echo No packages were installed. See output and startup-error.log.
pause
exit /b 1
