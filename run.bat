@echo off
REM Planet X - SPHEREx Explorer: one-click start on Windows.
REM First run creates a private virtual environment (.venv) and installs 5 small packages.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo Python 3.10+ is required: https://www.python.org/downloads/
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import astropy, numpy, fastapi, uvicorn, httpx" 2>nul
if errorlevel 1 (
  echo Installing dependencies ^(one time^)...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 (
    echo Installing dependencies failed. Check your internet connection.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m backend %*
endlocal
