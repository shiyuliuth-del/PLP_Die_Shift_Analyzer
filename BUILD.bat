@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
echo Building with your existing Windows Python environment...
set "DSA_PYTHON=%DIE_SHIFT_PYTHON%"
if defined DSA_PYTHON goto selected
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "DSA_PYTHON=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined DSA_PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "DSA_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined DSA_PYTHON for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "DSA_PYTHON=%%P"
if not defined DSA_PYTHON for /f "delims=" %%P in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "DSA_PYTHON=%%P"
:selected
if not exist "%DSA_PYTHON%" goto failed
if /i "%~1"=="--admin-only" goto build_admin
"%DSA_PYTHON%" -m die_shift_studio.license_core || goto invalid_key
:build_admin
"%DSA_PYTHON%" -m PyInstaller --clean --noconfirm --distpath "%~dp0dist_admin" "%~dp0LicenseManager\LicenseManager.spec" || goto failed
if not exist "%~dp0dist_admin\LicenseManager.exe" goto failed
if /i "%~1"=="--admin-only" goto admin_ready
"%DSA_PYTHON%" -m die_shift_studio.dependencies || goto failed
"%DSA_PYTHON%" -m PyInstaller --clean --noconfirm "%~dp0Die_Shift_Analyze.spec" || goto failed
"%DSA_PYTHON%" -m PyInstaller --clean --noconfirm "%~dp0RealDataAnalyse\RealDataAnalyse.spec" || goto failed
if not exist "%~dp0dist\Die_Shift_Analyze.exe" goto failed
if not exist "%~dp0dist\RealDataAnalyse.exe" goto failed
"%DSA_PYTHON%" -m RealDataAnalyse.projects --install-case "%~dp0dist" || goto failed
for %%L in (DieShiftAnalyze_RUN.bat RealDataAnalyze_RUN.bat) do (
    copy /y "%~dp0%%L" "%~dp0dist\%%L" >nul || goto failed
)
echo Ready: "%~dp0dist\Die_Shift_Analyze.exe"
echo Ready: "%~dp0dist\RealDataAnalyse.exe"
:admin_ready
echo Administrator only: "%~dp0dist_admin\LicenseManager.exe"
if not defined CI pause
exit /b 0
:invalid_key
echo Client build needs the formal Ed25519 public key: public_key.pem next to BUILD.bat.
echo Use the LicenseManager Release download, or run BUILD.bat --admin-only to build the administrator tool.
if not defined CI pause
exit /b 1
:failed
echo Build failed. Use an existing Python environment with the packages in requirements.txt.
echo Set DIE_SHIFT_PYTHON to its python.exe if needed.
if not defined CI pause
exit /b 1
