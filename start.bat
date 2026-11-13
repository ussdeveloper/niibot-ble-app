@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [Niibot] Tworze srodowisko wirtualne .venv ...
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
echo [Niibot] Instalacja nie powiodla sie. Sprawdz, czy Python 3.10+ jest w PATH.
pause
exit /b 1
