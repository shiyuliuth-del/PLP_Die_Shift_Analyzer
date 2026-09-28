@echo off
rem Select an existing environment only. No venv creation and no pip/network.
set "DSA_PYTHON="
if defined DIE_SHIFT_PYTHON if exist "%DIE_SHIFT_PYTHON%" set "DSA_PYTHON=%DIE_SHIFT_PYTHON%"
if not defined DSA_PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "DSA_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined DSA_PYTHON for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "DSA_PYTHON=%%P"
if not defined DSA_PYTHON for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "DSA_PYTHON=%%P"
if not defined DSA_PYTHON (
  echo No existing Python runtime. Set DIE_SHIFT_PYTHON or use the packaged EXE.
  exit /b 1
)
exit /b 0
