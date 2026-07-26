@echo off
rem Double-click this to map your most recently played Scrap Mechanic world.
rem Everything else -- finding the game, finding the save, opening the result --
rem happens on its own.

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo   Python 3 was not found on this PC.
  echo   Install it from https://python.org/downloads ^(tick "Add to PATH"^),
  echo   then run this again.
  echo.
  pause
  exit /b 1
)

%PY% -c "import numpy, PIL" >nul 2>&1
if errorlevel 1 (
  echo.
  echo   First run: installing numpy and Pillow ^(one time, ~30 seconds^)...
  %PY% -m pip install --quiet --disable-pip-version-check --user numpy Pillow
  %PY% -c "import numpy, PIL" >nul 2>&1
  if errorlevel 1 (
    echo.
    echo   Could not install them automatically. Run this by hand:
    echo       %PY% -m pip install numpy Pillow
    echo.
    pause
    exit /b 1
  )
)

%PY% -m smmap %*
if errorlevel 1 (
  echo.
  pause
)
