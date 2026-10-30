@echo off
rem build-setup.cmd - rebuild the portable exe, then try to build an installer.
rem
rem   1) call build-exe.cmd  ->  D:\ac-build\dist\auto-checkin
rem   2) make sure Inno Setup (ISCC.exe) exists
rem        a) already installed -> use it
rem        b) tools\get-inno-setup.ps1 -> winget, then GitHub download
rem        c) still nothing     -> make D:\ac-build\dist\auto-checkin-portable.zip
rem   3) compile installer\auto-checkin.iss into a one-file setup exe
rem
rem NOTE: output is NOT redirected anywhere. An earlier version wrapped this
rem script in a "re-run myself, tee to a log, print at the end" shell; the
rem window then looked frozen for minutes while winget/downloads ran, and the
rem user could not tell whether it was alive. Live output wins: every step
rem prints as it happens, and failures pause at the end so nothing is lost.
rem
rem Pure ASCII only (cmd.exe parses with the OEM code page).

chcp 65001 >nul
setlocal enabledelayedexpansion
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "SRC=D:\ac-build\dist\auto-checkin"
set "OUTDIR=D:\ac-build\dist\setup"
set "ZIP=D:\ac-build\dist\auto-checkin-portable.zip"
set "ISS=%ROOT%\installer\auto-checkin.iss"
set "TEMP=%ROOT%\tmp"
set "TMP=%ROOT%\tmp"
if not exist "%ROOT%\tmp" mkdir "%ROOT%\tmp"

echo ============================================================
echo   build auto-checkin + installer
echo   started: %DATE% %TIME%
echo ============================================================
echo.

rem --------------------------------------------------------------- 1) exe
echo [1/3] building the portable exe (about a minute)...
echo.
set "NOPAUSE=1"
call "%ROOT%\build-exe.cmd"
set "BUILD_RC=%ERRORLEVEL%"
set "NOPAUSE="
echo.
echo       build-exe.cmd exit code: %BUILD_RC%
if not "%BUILD_RC%"=="0" (
  echo.
  echo [X] exe build failed - see the messages above.
  echo     hint: the usual cause is that D:\ac-build\dist\auto-checkin\auto-checkin.exe
  echo     is still running - close the console window ^(the one opened from
  echo     "open console.lnk"^) and wait a few seconds, then run this again.
  echo.
  pause
  endlocal & exit /b 1
)
if not exist "%SRC%\auto-checkin.exe" (
  echo [X] %SRC%\auto-checkin.exe missing after build.
  echo.
  pause
  endlocal & exit /b 1
)
echo       ok: %SRC%\auto-checkin.exe
echo.

rem --------------------------------------------------------------- 2) Inno
call :find_iscc
if defined ISCC goto have_inno

echo [2/3] Inno Setup not found.
echo       Will try winget, then a GitHub download. This can take a few
echo       minutes and prints progress below - please leave the window open.
echo       If it fails, a portable zip is produced instead (exe is already built).
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\tools\get-inno-setup.ps1"
call :find_iscc
if defined ISCC goto have_inno

echo.
echo [2/3] could not get Inno Setup. Falling back to a portable zip.
powershell -NoProfile -ExecutionPolicy Bypass -Command "if(Test-Path '%ZIP%'){Remove-Item '%ZIP%' -Force}; Compress-Archive -Path '%SRC%\*' -DestinationPath '%ZIP%' -CompressionLevel Optimal; Write-Host ('  ' + '%ZIP%')"
echo.
echo ============================================================
echo   partial success
echo   portable folder: %SRC%
echo   portable zip   : %ZIP%
echo   no installer was built because Inno Setup is missing.
echo   You can install it yourself from https://jrsoftware.org/isdl.php
echo   and then run this script again (exe is already built, so it is quick).
echo ============================================================
echo.
pause
endlocal & exit /b 0

:have_inno
echo [2/3] Inno Setup: %ISCC%
echo.

rem ------------------------------------------------------------ 3) installer
echo [3/3] compiling the installer...
if not exist "%ISS%" (
  echo [X] %ISS% not found.
  echo.
  pause
  endlocal & exit /b 1
)

rem Inno expects UTF-8 WITH BOM; editors usually strip it, so compile a copy.
set "ISSCBOM=%TEMP%\_auto-checkin.bom.iss"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText('%ISS%',[Text.Encoding]::UTF8); [IO.File]::WriteAllText('%ISSCBOM%',$t,(New-Object System.Text.UTF8Encoding -ArgumentList $true))"
if not exist "%ISSCBOM%" (
  echo [X] failed to prepare the script copy.
  echo.
  pause
  endlocal & exit /b 1
)

"%ISCC%" /Qp /DSrcDir="%SRC%" /DIconFile="%ROOT%\app.ico" /DReadmeFile="%ROOT%\README.md" /DChecklistFile="%ROOT%\CHECKLIST.md" "%ISSCBOM%"
if errorlevel 1 (
  echo.
  echo [X] ISCC failed - see the messages above.
  echo.
  pause
  endlocal & exit /b 1
)

echo.
echo ============================================================
echo   done
echo   portable : %SRC%\auto-checkin.exe
echo   installer: %OUTDIR%\auto-checkin-setup-*.exe
echo ============================================================
dir /b "%OUTDIR%\auto-checkin-setup-*.exe" 2>nul
echo.
echo   next: double-click the setup exe to install.
echo.
pause
endlocal & exit /b 0

rem ------------------------------------------------------------------ helpers
rem Inno Setup 7 and 6, per-user and machine-wide. Plain sequential "if" lines:
rem "%%ProgramFiles(x86)%%" inside a for/if block would confuse the parser.
:find_iscc
set "ISCC="
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
goto :eof
