@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Python environment could not be created.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -c "from importlib.metadata import version; assert version('flet').startswith('1.0.'); import icalendar, recurring_ical_events, requests; from PIL import Image" >nul 2>nul
if errorlevel 1 (
  echo Installing MAI Pulse dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Installation failed. Check the internet connection.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" main.py
if errorlevel 1 pause
