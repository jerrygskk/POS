@echo off
setlocal
set "POS_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%POS_PYTHON%" (
  echo Python 3.12 was not found. See DEVELOPER.md for setup.
  pause
  exit /b 2
)
start "" /D "%~dp0" "%POS_PYTHON%" "%~dp0main.py"
