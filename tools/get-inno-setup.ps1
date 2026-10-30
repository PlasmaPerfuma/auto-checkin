# get-inno-setup.ps1 - make sure Inno Setup (ISCC.exe) is available.
#
# Strategy, cheapest first:
#   1) already installed -> done
#   2) winget, but FORCED to the "winget" source (the default also tries msstore,
#      which fails on many Chinese networks with WinHttpSendRequest 12029)
#   3) download the official installer from GitHub releases, verify it is a real
#      PE (MZ header + sane size), then install it silently for the current user
#
# Exit code 0 = ISCC.exe is usable, 1 = could not get it.
# Pure ASCII on purpose (no BOM games needed).

$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Find-ISCC {
    $cands = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles} 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles} 'Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
    )
    foreach ($c in $cands) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    return $null
}

$iscc = Find-ISCC
if ($iscc) { Write-Host "        already installed: $iscc"; exit 0 }

# ------------------------------------------------------------------ winget
# winget can stall for minutes (or sit on a hidden prompt) on some networks, so
# every attempt gets a hard timeout instead of hanging the whole build.
function Invoke-WithTimeout {
    param([string[]]$ArgLine, [int]$TimeoutSec)
    $p = Start-Process -FilePath $ArgLine[0] -ArgumentList ($ArgLine[1..($ArgLine.Length - 1)]) `
                       -NoNewWindow -PassThru -RedirectStandardOutput "$env:TEMP\_wg.out" `
                       -RedirectStandardError "$env:TEMP\_wg.err"
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
        try { $p.Kill() } catch {}
        Write-Host "            timed out after $TimeoutSec s - killed"
        return 124
    }
    Get-Content "$env:TEMP\_wg.out" -ErrorAction SilentlyContinue | ForEach-Object { "            $_" }
    Get-Content "$env:TEMP\_wg.err" -ErrorAction SilentlyContinue | ForEach-Object { "            $_" }
    return $p.ExitCode
}

$winget = Get-Command winget -ErrorAction SilentlyContinue
if ($winget) {
    Write-Host '        trying winget (source: winget)...'
    foreach ($id in @('JRSoftware.InnoSetup', 'JRSoftware.InnoSetup.7')) {
        Write-Host "          winget install $id (timeout 150s)"
        $code = Invoke-WithTimeout -TimeoutSec 150 -ArgLine @(
            $winget.Source, 'install', '--id', $id, '-e', '-s', 'winget',
            '--silent', '--disable-interactivity',
            '--accept-package-agreements', '--accept-source-agreements'
        )
        Write-Host "          exit code: $code"
        $iscc = Find-ISCC
        if ($iscc) { Write-Host "        installed via winget: $iscc"; exit 0 }
    }
} else {
    Write-Host '        winget not available.'
}

# ---------------------------------------------------------------- download
# Official releases live on GitHub (jrsoftware.org/files/... frequently 404s
# because the version numbers change; see https://jrsoftware.org/isdl.php).
$urls = @(
    'https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe',
    'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
)

# Last resort: read the download page and use whatever .exe link it advertises.
try {
    $page = (Invoke-WebRequest 'https://jrsoftware.org/isdl.php' -UseBasicParsing -TimeoutSec 30).Content
    $found = [regex]::Matches($page, 'https://[^"''\s>]+innosetup-[0-9.]+(-x64|-x86)?\.exe') |
             ForEach-Object { $_.Value } | Select-Object -Unique
    if ($found) { Write-Host "        page advertises $($found.Count) installer link(s)"; $urls += $found }
} catch {
    Write-Host "        could not read the download page: $($_.Exception.Message)"
}

$dst = Join-Path $env:TEMP 'inno-setup.exe'
foreach ($u in $urls) {
    try {
        Write-Host "        GET $u"
        if (Test-Path -LiteralPath $dst) { Remove-Item $dst -Force -ErrorAction SilentlyContinue }
        Invoke-WebRequest -Uri $u -OutFile $dst -UseBasicParsing -TimeoutSec 180
        $len = (Get-Item $dst).Length
        $b = [IO.File]::ReadAllBytes($dst)
        $mz = ($b.Length -gt 2 -and $b[0] -eq 0x4D -and $b[1] -eq 0x5A)   # 'MZ'
        Write-Host "        got $len bytes, MZ=$mz"
        if (-not ($len -gt 3000000 -and $mz)) {
            Write-Host '        rejected (not a usable installer)'
            Remove-Item $dst -Force -ErrorAction SilentlyContinue
            continue
        }
        Write-Host '        installing silently (current user)...'
        $args = '/VERYSILENT','/SUPPRESSMSGBOXES','/CURRENTUSER','/NORESTART','/SP-'
        Start-Process -FilePath $dst -ArgumentList $args -Wait
        Start-Sleep -Seconds 2
        Remove-Item $dst -Force -ErrorAction SilentlyContinue
        $iscc = Find-ISCC
        if ($iscc) { Write-Host "        installed: $iscc"; exit 0 }
        Write-Host '        installer ran but ISCC.exe is still missing.'
    } catch {
        Write-Host "        failed: $($_.Exception.Message)"
    }
}

Write-Host '        could not obtain Inno Setup.'
exit 1
