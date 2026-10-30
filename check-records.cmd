@echo off
rem check-records.cmd - read-only: print the current sign-in record total.
rem Double-click this AFTER a class to see whether the count went up by one.
rem Pure ASCII only.

chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "PY=C:\Users\admin\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=py"

"%PY%" "%ROOT%\check-records.py" %*
echo.
pause
endlocal
exit /b 0
