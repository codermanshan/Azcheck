@echo off
cd /d "%~dp0"
echo Starting azcheck web UI...
python webapp.py
if errorlevel 1 pause
