@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv-build
if errorlevel 1 goto failed
.venv-build\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto failed
.venv-build\Scripts\python.exe -m pip install -r requirements.txt "pyinstaller>=6.10,<7"
if errorlevel 1 goto failed
.venv-build\Scripts\python.exe -m PyInstaller --clean --noconfirm Die_Shift_Analyze.spec
if errorlevel 1 goto failed
start /wait "" dist\Die_Shift_Analyze.exe --smoke-test dist\windows_smoke.json
if errorlevel 1 goto failed
.venv-build\Scripts\python.exe -c "import json; p=json.load(open('dist/windows_smoke.json',encoding='utf-8')); assert p['status']=='PASS',p; print('Frozen GUI smoke: PASS')"
if errorlevel 1 goto failed
echo Ready: dist\Die_Shift_Analyze.exe
pause
exit /b 0
:failed
echo Build or executable smoke test failed. See output and startup-error.log.
pause
exit /b 1
