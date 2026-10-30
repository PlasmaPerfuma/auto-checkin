@echo off
rem diag-console.cmd - figure out why the console window does not come up.
rem Pure ASCII. Prints the real error instead of a window that just vanishes.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "EXE=D:\ac-build\dist\auto-checkin\auto-checkin.exe"
set "LOG=%~dp0logs\cli.log"

echo ============================================================
echo   auto-checkin console diagnostic
echo ============================================================
echo.
echo [1] exe present?
if not exist "%EXE%" goto :noexe
echo     ok: %EXE%
echo.
echo [2] launching dashboard (a window should appear in ~25s)...
start "" "%EXE%" --home "%~dp0." dashboard
echo.
echo [3] waiting 25 seconds...
ping -n 26 127.0.0.1 >nul
echo.
echo [4] is port 8791 listening?
powershell -NoProfile -Command "try { $c=New-Object Net.Sockets.TcpClient('127.0.0.1',8791); Write-Host '     YES - backend is up'; $c.Close() } catch { Write-Host '     NO - backend did not start' }"
echo.
echo [5] last lines of logs\cli.log (the real error lives here):
echo ------------------------------------------------------------
if exist "%LOG%" powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 30 -Encoding UTF8"
if not exist "%LOG%" echo     (no cli.log at all)
echo ------------------------------------------------------------
goto :done

:noexe
echo     MISSING: %EXE%
echo     The build output was deleted. Run build-exe.cmd to rebuild it.

:done
echo.
echo Done. If something looks wrong, copy everything above.
pause
