@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

set "PYTHON_EXE="
if exist "%ROOT%.venv-3\Scripts\python.exe" set "PYTHON_EXE=%ROOT%.venv-3\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%ROOT%.venv\Scripts\python.exe" set "PYTHON_EXE=%ROOT%.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%ROOT%venv\Scripts\python.exe" set "PYTHON_EXE=%ROOT%venv\Scripts\python.exe"

if not defined PYTHON_EXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=python"
)

if not defined PYTHON_EXE goto :python_missing

set "PYTHONUNBUFFERED=1"
"%PYTHON_EXE%" "%ROOT%launcher.py"
exit /b 0

:python_missing
echo.
echo [ERR] Could not find a Python interpreter.
pause
exit /b 1
