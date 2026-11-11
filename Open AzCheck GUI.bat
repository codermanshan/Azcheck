@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title azcheck setup
echo ========================================
echo   azcheck - Amazon.ae List Underneath
echo ========================================
echo.

REM Prefer "py -3" launcher on Windows, then "python"
set "PY="
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if %errorlevel%==0 set "PY=py -3"
)
if not defined PY (
  where python >nul 2>&1
  if %errorlevel%==0 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if %errorlevel%==0 set "PY=python"
  )
)

if not defined PY (
  echo [ERROR] Python 3.10+ was not found on this machine.
  echo.
  echo 1^) Install Python from: https://www.python.org/downloads/
  echo 2^) During setup, CHECK "Add python.exe to PATH"
  echo 3^) Re-open this file after install.
  echo.
  start "" "https://www.python.org/downloads/"
  pause
  exit /b 1
)

echo Using: %PY%
%PY% --version
echo.

echo Installing / updating required packages...
%PY% -m pip install --upgrade pip >nul 2>&1
%PY% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo.
  echo [ERROR] Could not install packages. Check your internet connection and try again.
  pause
  exit /b 1
)

echo.
echo Starting azcheck web UI...
echo Browser should open at http://127.0.0.1:8765/
echo Keep this window open while using the app. Press Ctrl+C to stop.
echo.
%PY% "%~dp0webapp.py"
set "EXITCODE=%errorlevel%"
if not "%EXITCODE%"=="0" (
  echo.
  echo [ERROR] azcheck exited with code %EXITCODE%.
  pause
)
exit /b %EXITCODE%
