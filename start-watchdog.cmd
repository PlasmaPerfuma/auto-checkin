@echo off
rem start-watchdog.cmd
rem
rem Starts app\watchdog.py with pythonw (no window). The watchdog checks every
rem 5 minutes whether the auto-checkin daemon is alive and restarts it if not.
rem Double-clicking it twice is harmless - a second watchdog only repeats the
rem same idempotent "start if dead" check.
rem
rem IMPORTANT: double-click from Explorer. Do NOT run this from inside the DSH
rem session - the DSH sandbox kills child processes when the command ends.
rem Pure ASCII only.

chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "PYW=C:\Users\admin\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
if not exist "%PYW%" set "PYW=C:\Users\admin\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PYW%" (
  echo [X] python not found
  pause
  exit /b 1
)
if not exist "%ROOT%\app\watchdog.py" (
  echo [X] app\watchdog.py not found
  pause
  exit /b 1
)

start "" "%PYW%" "%ROOT%\app\watchdog.py"

echo ============================================================
echo   watchdog started (hidden in the background)
echo.
echo   it checks every 5 minutes and restarts the signing daemon
echo   if that daemon silently died.
echo.
echo   logs:
echo     %ROOT%\logs\watchdog.log    (only when it acts)
echo     %ROOT%\logs\autowatch.log   (the daemon itself)
echo.
echo   you can close this window now.
echo ============================================================
echo.
pause
endlocal
exit /b 0
