@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0.." || exit /b 1
if /i "%~1"=="build" goto interpreter
if exist "%~dp0..\dist\RealDataAnalyse.exe" (
    start "" "%~dp0..\dist\RealDataAnalyse.exe" %*
    exit /b
)
:interpreter
set "RDA_PYTHON=%DIE_SHIFT_PYTHON%"
if defined RDA_PYTHON goto selected
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "RDA_PYTHON=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined RDA_PYTHON if exist "%~dp0..\.venv\Scripts\python.exe" set "RDA_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not defined RDA_PYTHON for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "RDA_PYTHON=%%P"
if not defined RDA_PYTHON for /f "delims=" %%P in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "RDA_PYTHON=%%P"
:selected
if not exist "%RDA_PYTHON%" (
    echo Python not found. Set DIE_SHIFT_PYTHON to the existing python.exe.
    if not defined CI pause
    exit /b 1
)
if /i "%~1"=="build" (
    "%RDA_PYTHON%" -m PyInstaller --clean --noconfirm "%~dp0RealDataAnalyse.spec"
    if errorlevel 1 exit /b 1
    "%RDA_PYTHON%" -m RealDataAnalyse.projects --install-case "%~dp0..\dist"
    exit /b
)
for %%P in ("%RDA_PYTHON%") do if exist "%%~dpPpythonw.exe" set "RDA_PYTHON=%%~dpPpythonw.exe"
start "" "%RDA_PYTHON%" "%~dp0main.py" %*
exit /b
