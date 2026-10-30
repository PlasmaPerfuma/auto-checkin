@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "REPO=https://github.com/PlasmaPerfuma/auto-checkin.git"
set "NEWURL=https://github.com/new?name=auto-checkin&visibility=public"

echo ============================================================
echo  Push auto-checkin to GitHub
echo ============================================================
echo.

echo [1/4] pointing git at the local proxy (http://127.0.0.1:7890)
git config http.proxy "http://127.0.0.1:7890"
git config https.proxy "http://127.0.0.1:7890"
rem schannel cannot do TLS through this proxy; openssl backend works
git config http.sslBackend openssl
echo.

echo [2/4] remote:
git remote set-url origin "%REPO%" 2>nul
git remote -v
echo.

echo [3/4] checking whether the repo exists on GitHub ...
set "PROBE="
for /f "delims=" %%i in ('git ls-remote --heads origin 2^>^&1') do set "PROBE=%%i"
if not "!PROBE!"=="" (
  echo !PROBE! | findstr /i "not found" >nul && (
    echo.
    echo [X] the repository does not exist yet.
    echo.
    echo     Create it first:
    echo       1^) the create page will open
    echo       2^) Repository name: auto-checkin
    echo       3^) choose Public
    echo       4^) do NOT tick README / .gitignore / license
    echo       5^) click "Create repository", then run this script again
    echo.
    start "" "%NEWURL%"
    goto done
  )
)

echo      repo looks reachable, pushing ...
echo.
echo [4/4] git push -u origin main
echo (a browser window may pop up for GitHub login - finish it there)
echo.
git push -u origin main
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo [X] push failed, exit code %RC%
  echo.
  echo If it says "not found": create the repo on GitHub first.
  echo If it asks for a password: paste a Personal Access Token
  echo   ^(GitHub - Settings - Developer settings - Personal access tokens^)
  echo If it says proxy / connect errors: is Clash running on port 7890?
) else (
  echo [OK] pushed!
  echo      open https://github.com/PlasmaPerfuma/auto-checkin
)

:done
echo.
pause
