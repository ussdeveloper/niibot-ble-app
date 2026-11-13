@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [Niibot] Creating the .venv virtual environment ...
  python -m venv .venv
  if errorlevel 1 goto :error
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\pip.exe" install --quiet -r requirements.txt
  if errorlevel 1 goto :error
)

if "%~1"=="" (
  ".venv\Scripts\python.exe" -m niibot panel
) else (
  ".venv\Scripts\python.exe" -m niibot %*
)
goto :eof

:error
echo.
echo [Niibot] The installation failed. Check that Python 3.10+ is on the PATH.
pause
exit /b 1
