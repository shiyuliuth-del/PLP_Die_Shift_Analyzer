@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
for %%E in ("%~dp0Die_Shift_Analyze.exe" "%~dp0dist\Die_Shift_Analyze.exe") do if exist "%%~fE" (
    start "" /d "%%~dpE." "%%~fE" %*
    exit /b
)
if not exist "%~dp0main.py" (
    echo Die_Shift_Analyze.exe and main.py were not found next to the launcher.
    if not defined CI pause
    exit /b 1
)
set "DSA_PYTHON=%DIE_SHIFT_PYTHON%"
if defined DSA_PYTHON goto selected
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "DSA_PYTHON=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined DSA_PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "DSA_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined DSA_PYTHON for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "DSA_PYTHON=%%P"
if not defined DSA_PYTHON for /f "delims=" %%P in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "DSA_PYTHON=%%P"
:selected
if not exist "%DSA_PYTHON%" (
    echo Python was not found. Set DIE_SHIFT_PYTHON to the existing python.exe.
    if not defined CI pause
    exit /b 1
)
for %%P in ("%DSA_PYTHON%") do if exist "%%~dpPpythonw.exe" set "DSA_PYTHON=%%~dpPpythonw.exe"
start "" "%DSA_PYTHON%" "%~dp0main.py" %*
exit /b
