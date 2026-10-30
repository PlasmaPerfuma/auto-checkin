@echo off
rem Thin launcher: all logic lives in app\main.py (single entry point).
rem Pure ASCII only (cmd.exe parses with the OEM code page).
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PY=C:\Users\admin\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=py"
"%PY%" "%ROOT%\app\main.py" stop
pause
endlocal
exit /b 0
