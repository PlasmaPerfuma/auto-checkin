@echo off
rem uninstall-autostart.cmd - removes BOTH startup shortcuts.
rem Pure ASCII only.

chcp 65001 >nul
setlocal
set "DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
for %%f in (auto-checkin-autowatch.lnk auto-checkin-watchdog.lnk) do (
  if exist "%DIR%\%%f" (
    del /f /q "%DIR%\%%f"
    echo Removed: %DIR%\%%f
  ) else (
    echo Not installed: %DIR%\%%f
  )
)
echo.
pause
