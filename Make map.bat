@echo off
rem Double-click this to map your Scrap Mechanic worlds. It opens a window with
rem your saves in it; press Make map and the rest -- finding the game, reading
rem the save, opening the result -- happens on its own.
rem
rem Pass any argument and it runs on the command line instead:
rem     "Make map.bat" --list
rem     "Make map.bat" --all --px 64

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

rem With no arguments, open the window -- and open it with the windowed
rem interpreter so no black console box is left sitting behind it.
if "%~1"=="" (
  %PY% -c "import tkinter" >nul 2>&1
  if not errorlevel 1 (
    set "PYW="
    pyw -3 -c "import sys" >nul 2>&1 && set "PYW=pyw -3"
    if not defined PYW (
      pythonw -c "import sys" >nul 2>&1 && set "PYW=pythonw"
    )
    if defined PYW (
      start "" !PYW! -m smmap --gui
      exit /b 0
    )
  )
)

%PY% -m smmap %*
if errorlevel 1 (
  echo.
  pause
)
