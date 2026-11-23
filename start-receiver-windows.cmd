@echo off
cd /d "%~dp0"
py -3 receiver.py --gui --host 127.0.0.1 --output "work\received"
if errorlevel 1 pause
