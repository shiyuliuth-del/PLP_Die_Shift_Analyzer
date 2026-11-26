@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
echo Building LicenseManager with your existing Windows Python environment...
set "DSA_PYTHON=%DIE_SHIFT_PYTHON%"
if defined DSA_PYTHON goto selected
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "DSA_PYTHON=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined DSA_PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "DSA_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined DSA_PYTHON for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "DSA_PYTHON=%%P"
if not defined DSA_PYTHON for /f "delims=" %%P in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "DSA_PYTHON=%%P"
:selected
if not exist "%DSA_PYTHON%" goto failed
"%DSA_PYTHON%" -m PyInstaller --clean --noconfirm --workpath "%~dp0build_admin" --distpath "%~dp0dist_admin" "%~dp0LicenseManager\LicenseManager.spec" || goto failed
if not exist "%~dp0dist_admin\LicenseManager.exe" goto failed
echo Ready: "%~dp0dist_admin\LicenseManager.exe"
if not defined CI pause
exit /b 0
:failed
echo LicenseManager build failed. The Python environment needs PyInstaller, cryptography and PyQt6 or PyQt5.
echo Set DIE_SHIFT_PYTHON to its python.exe if needed.
if not defined CI pause
exit /b 1
