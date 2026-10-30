@echo off
rem Stop the dashboard backend (the console window can also stop it by closing).
rem Pure ASCII. Reads dashboard.pid and stops only that process.
setlocal
cd /d "%~dp0"
set "PY=C:\Users\admin\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=py"
"%PY%" "%~dp0app\main.py" stop-dashboard
pause
