@echo off
rem install-autostart.cmd
rem Creates TWO shortcuts in the user's Startup folder:
rem   1) auto-checkin-autowatch.lnk -> starts the signing daemon at logon
rem   2) auto-checkin-watchdog.lnk  -> starts the watchdog at logon
rem      (the watchdog restarts the daemon if it silently dies mid-day)
rem Shortcut (not a copy) on purpose: copying the .cmd there would make %~dp0
rem point at the Startup folder and break every relative path.
rem Pure ASCII only.

chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PYW=C:\Users\admin\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
if not exist "%PYW%" (
  echo [!] pythonw.exe not found: %PYW%
  pause
  exit /b 1
)
if not exist "%ROOT%app\autowatch.py" (
  echo [!] app\autowatch.py not found. Wrong folder?
  pause
  exit /b 1
)
if not exist "%ROOT%app\watchdog.py" (
  echo [!] app\watchdog.py not found. Wrong folder?
  pause
  exit /b 1
)

echo Creating startup shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; $dir=$env:APPDATA+'\Microsoft\Windows\Start Menu\Programs\Startup'; $a=@(@('auto-checkin-autowatch.lnk','%ROOT%app\autowatch.py --run','auto-checkin: watch class schedule and check in automatically'),@('auto-checkin-watchdog.lnk','%ROOT%app\watchdog.py','auto-checkin: restart the signing daemon if it dies')); foreach($x in $a){ $p=Join-Path $dir $x[0]; $s=$w.CreateShortcut($p); $s.TargetPath='%PYW%'; $s.Arguments=$x[1]; $s.WorkingDirectory='%ROOT%'; $s.WindowStyle=7; $s.Description=$x[2]; $s.Save(); Unblock-File -Path $p -ErrorAction SilentlyContinue; Write-Host ('  ' + $p) }"
if errorlevel 1 (
  echo [!] failed to create shortcuts
  pause
  exit /b 1
)

rem PowerShell's Save() failure is a non-terminating error, so errorlevel may
rem stay 0 - verify the files really exist afterwards.
set "SF=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "OK=1"
if not exist "%SF%\auto-checkin-autowatch.lnk" set "OK=0"
if not exist "%SF%\auto-checkin-watchdog.lnk" set "OK=0"
if "%OK%"=="0" (
  echo [!] one or more shortcuts were NOT created - see the error above.
  pause
  exit /b 1
)

echo.
echo Done. At next logon Windows will start:
echo   - the signing daemon (autowatch)
echo   - the watchdog (restarts the daemon if it died)
echo   - check now : status-autowatch.cmd
echo   - start now : start-watchdog.cmd  (or run-autowatch.cmd)
echo   - remove    : uninstall-autostart.cmd
echo.
pause
