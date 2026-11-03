@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
if /i "%~1"=="build" goto interpreter
if exist "%~dp0Die_Shift_Analyze.exe" (
    start "" "%~dp0Die_Shift_Analyze.exe" %*
    exit /b
)
:interpreter
set "DSA_PYTHON=%DIE_SHIFT_PYTHON%"
if defined DSA_PYTHON goto selected
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "DSA_PYTHON=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined DSA_PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "DSA_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined DSA_PYTHON for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "DSA_PYTHON=%%P"
if not defined DSA_PYTHON for /f "delims=" %%P in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "DSA_PYTHON=%%P"
:selected
if not exist "%DSA_PYTHON%" (
    echo Python was not found. Set DIE_SHIFT_PYTHON to the existing python.exe.
    pause
    exit /b 1
)
if /i "%~1"=="build" (
    "%DSA_PYTHON%" -m PyInstaller --clean --noconfirm "%~dp0Die_Shift_Analyze.spec"
    if errorlevel 1 exit /b 1
    exit /b 0
)
for %%P in ("%DSA_PYTHON%") do if exist "%%~dpPpythonw.exe" set "DSA_PYTHON=%%~dpPpythonw.exe"
start "" "%DSA_PYTHON%" "%~dp0main.py" %*
exit /b
