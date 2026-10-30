@echo off
rem Build the portable onedir package with PyInstaller.
rem
rem Why a script: the full command is ~20 lines of flags. Typing it by hand every
rem time is slow and easy to get subtly wrong (one missing --add-data and the
rem packaged app cannot find its own scripts).
rem
rem Requirements:
rem   PyInstaller installed into %DEPS% (not into the DSH runtime):
rem     "%PY%" -m pip install --target "D:\ac-build\pydeps" pyinstaller
rem
rem Output: D:\ac-build\dist\auto-checkin\auto-checkin.exe
rem Pure ASCII only (cmd.exe parses with the OEM code page).
chcp 65001 >nul
rem Delayed expansion OFF on purpose: callers (build-setup.cmd) may have turned it
rem on, and then a plain "echo [!]" would lose everything after the '!'.
setlocal DisableDelayedExpansion
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "PY=C:\Users\admin\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=py"

set "DEPS=D:\ac-build\pydeps"
set "OUT=D:\ac-build\dist"
set "WORK=D:\ac-build\build"
set "SPEC=D:\ac-build"

set "TEMP=%ROOT%\tmp"
set "TMP=%ROOT%\tmp"
set "PYTHONPATH=%DEPS%"
set "PYTHONIOENCODING=utf-8"
if not exist "%ROOT%\tmp" mkdir "%ROOT%\tmp"

echo ============================================================
echo   build auto-checkin (onedir)
echo   source : %ROOT%
echo   output : %OUT%
echo ============================================================
echo.

if not exist "%DEPS%\PyInstaller" (
  echo [X] PyInstaller not found in %DEPS%
  echo     install it first:
  echo       "%PY%" -m pip install --target "%DEPS%" pyinstaller
  echo.
  if not defined NOPAUSE pause
  exit /b 1
)

rem A previous exe may be running (the console opens THIS exe) or held by
rem antivirus. PyInstaller would then die near the end while copying into dist,
rem which looks like "nothing happened". Diagnose it here instead.
rem tasklist can be unavailable in restricted contexts, so only trust it when it
rem returns an actual PID (second CSV field); otherwise stay quiet.
rem NOTE: do not hard-fail here. Closing the console leaves the exe tearing down
rem (WebView2 + Playwright) for several seconds, and a freshly-closed console
rem looks exactly like "still running". Just warn and let the retry loop below
rem wait it out.
set "RUNNING_PID="
set "TL=%TEMP%\_tasklist.txt"
tasklist /fo csv /nh /fi "imagename eq auto-checkin.exe" > "%TL%" 2>nul
if exist "%TL%" for /f "tokens=2 delims=," %%a in (%TL%) do set "RUNNING_PID=%%~a"
del /f /q "%TL%" >nul 2>&1
if not defined RUNNING_PID goto skip_stop_running
echo     trying to stop it gracefully: auto-checkin.exe stop-dashboard
if exist "%OUT%\auto-checkin\auto-checkin.exe" start /wait "" "%OUT%\auto-checkin\auto-checkin.exe" stop-dashboard
echo     waiting for it to exit...
:skip_stop_running

rem Labels must NOT live inside an "if (...)" block, so the retry loop below is
rem written with plain gotos.
set "TARGET=%OUT%\auto-checkin\auto-checkin.exe"
if not exist "%TARGET%" goto del_exe_done
set "ATTEMPTS=0"
:try_del_exe
attrib -r -s -h "%TARGET%" >nul 2>&1
del /f /q "%TARGET%" >nul 2>&1
if not exist "%TARGET%" goto del_exe_done
set /a ATTEMPTS+=1
if %ATTEMPTS% GEQ 10 (
  echo [!] cannot delete the previous exe after 10 tries ^(~30s^):
  echo     %TARGET%
  echo     it is held by another process or by antivirus.
  echo     Tip: delete it by hand in Explorer - Windows then tells you
  echo          which program is holding it.
  echo     Or add %OUT% to antivirus exclusions.
  echo.
  if not defined NOPAUSE pause
  exit /b 1
)
echo     previous exe is busy, retry %ATTEMPTS%/10 in 3s...
ping -n 4 127.0.0.1 >nul
goto try_del_exe
:del_exe_done

rem --noconsole: the exe must never show a console window (a normal user should
rem not see a black box flash). Downside: sys.stdout becomes None, so main.py
rem redirects its output to logs\cli.log when there is no console.
"%PY%" -m PyInstaller --noconfirm --clean --onedir --noconsole --name auto-checkin ^
  --distpath "%OUT%" --workpath "%WORK%" --specpath "%SPEC%" ^
  --add-data "%ROOT%\app;app" ^
  --add-data "%ROOT%\VERSION;." --icon "%ROOT%\app.ico" ^
  --paths "%ROOT%\libs" ^
  --collect-all playwright ^
  --collect-all zxingcpp ^
  --hidden-import mss ^
  --hidden-import PIL.Image ^
  --hidden-import numpy ^
  --hidden-import capture_checkin ^
  --hidden-import commands ^
  --hidden-import config_store ^
  --hidden-import settings ^
  --hidden-import scheduler ^
  --hidden-import sjtu_login ^
  --hidden-import discover_courses ^
  --hidden-import dashboard ^
  "%ROOT%\app\main.py"

if errorlevel 1 (
  echo.
  echo [X] build failed
  echo.
  if not defined NOPAUSE pause
  exit /b 1
)

echo.
echo   done: %OUT%\auto-checkin\auto-checkin.exe
echo.
echo   quick check:
echo     "%OUT%\auto-checkin\auto-checkin.exe" help
echo     "%OUT%\auto-checkin\auto-checkin.exe" capture --check-env
echo.
if not defined NOPAUSE pause
endlocal
exit /b 0
