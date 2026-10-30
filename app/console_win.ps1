# console_win.ps1 -- 控制台窗口（标准 Windows 窗口 + WebView2）
#
# 设计定案（2026-10-23）：**用标准窗口，不自绘标题条**。
#   理由：早期为了"去掉系统标题栏"折腾了四轮（Edge --app 自绘标题栏去不掉 →
#   WinForms 无边框 + 盖 Panel 当拖拽条（挡住页面按钮）→ 原生标题条独立一行
#   （GDI 与 Chromium 两层渲染，接缝天然存在）→ 内容铺满后边缘收不到鼠标事件）。
#   MaaEnd 本身用的就是**系统标题栏**，只是 Windows 11 下它表现为浅色、无边框感。
#   换成标准窗口后：缩放 / 贴靠 / 圆角 / 投影 / 最小化 全部由系统免费提供，
#   内容铺满客户区、零接缝、零 hack。标题栏底色用 DWM 改成纯白，与页面连成一片。
param(
    [string]$Url,
    [int]$Width = 1180,
    [int]$Height = 820,
    [string]$Title = '',
    [string]$Tag = '',
    [string]$Root = '',
    [string]$AppDir = '',
    [int]$BackendPid = 0
)

if (-not $Title) {
    $Title = if ($Tag -eq 'preview') { '自动签到控制台 · 预览' } else { '自动签到控制台' }
}

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class WinFx {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
    [DllImport("gdi32.dll")] public static extern IntPtr CreateRoundRectRgn(int l, int t, int r, int b, int w, int h);
    // 任务栏图标：Windows 用 WM_SETICON 的大图标（ICON_BIG）
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern IntPtr LoadImage(IntPtr hinst, string name, uint type, int cx, int cy, uint load);
    // 不设 AppUserModelID 的话，任务栏会把窗口归到 pythonw.exe 名下、用它的图标
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)] public static extern int SetCurrentProcessExplicitAppUserModelID(string appId);
}
'@ -Language CSharp

[void][WinFx]::SetProcessDPIAware()

# 让任务栏把本窗口当成独立应用（而不是 pythonw.exe 的一个实例）
try { [void][WinFx]::SetCurrentProcessExplicitAppUserModelID('AutoCheckin.Console') } catch {}

# ---------------- 路径 ----------------
# 启动器为了补 UTF-8 BOM 会把脚本复制成 tmp\_console_win.bom.ps1 再跑，
# 所以不能靠 $MyInvocation 推算项目根 —— 优先用显式传入的 -Root。
if ($Root -and (Test-Path $Root)) {
    $root = $Root
} elseif ($env:AUTOCHECKIN_HOME -and (Test-Path $env:AUTOCHECKIN_HOME)) {
    $root = $env:AUTOCHECKIN_HOME
} else {
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $root = Split-Path -Parent $here
    if (-not (Test-Path (Join-Path $root 'app\webview2'))) {
        $root = $here
        while ($root -and -not (Test-Path (Join-Path $root 'app\webview2'))) {
            $parent = Split-Path -Parent $root
            if ($parent -eq $root -or -not $parent) { break }
            $root = $parent
        }
    }
}
$here   = if ($AppDir -and (Test-Path $AppDir)) { $AppDir } else { Join-Path $root 'app' }
$dllDir = Join-Path $here 'webview2'
$env:PATH = "$dllDir;$env:PATH"
if (-not (Test-Path $dllDir)) { throw "找不到 WebView2 库目录：$dllDir（root=$root, app=$here）" }

$pidName = if ($Tag) { "console-win-$Tag.pid" } else { 'console-win.pid' }
$pidFile = Join-Path (Join-Path $root 'logs') $pidName
try { [System.IO.File]::WriteAllText($pidFile, "$PID") } catch {}

$onResolve = [System.ResolveEventHandler]{
    param($s, $e)
    $simple = ($e.Name -split ',')[0]
    $p = Join-Path $dllDir "$simple.dll"
    if (Test-Path $p) {
        return [System.Reflection.Assembly]::Load([System.IO.File]::ReadAllBytes($p))
    }
    return $null
}
[System.AppDomain]::CurrentDomain.add_AssemblyResolve($onResolve)

$asmWin = [System.Reflection.Assembly]::Load([System.IO.File]::ReadAllBytes((Join-Path $dllDir 'Microsoft.Web.WebView2.WinForms.dll')))
[void][System.Reflection.Assembly]::Load([System.IO.File]::ReadAllBytes((Join-Path $dllDir 'Microsoft.Web.WebView2.Core.dll')))
$tWebView = $asmWin.GetType('Microsoft.Web.WebView2.WinForms.WebView2')
if ($null -eq $tWebView) { throw '找不到 WebView2 类型' }

[System.Windows.Forms.Application]::EnableVisualStyles()
[System.Windows.Forms.Application]::SetCompatibleTextRenderingDefault($false)

# ---------------- 尺寸（按 DPI 换算成物理像素） ----------------
$g = [System.Drawing.Graphics]::FromHwnd([IntPtr]::Zero)
$scale = $g.DpiX / 96.0
$g.Dispose()
if ($scale -lt 1) { $scale = 1 }
function S([double]$v) { return [int][Math]::Round($v * $scale) }

# ---------------- 窗体 ----------------
# 版本号：读项目根的 VERSION 文件（唯一真源），拼进窗口标题 —— 系统标题栏会原样显示。
$ver = ''
try {
    $vf = Join-Path (Split-Path -Parent $here) 'VERSION'
    if (Test-Path $vf) { $ver = (Get-Content $vf -Raw -ErrorAction Stop).Trim() }
} catch {}
$winTitle = if ($ver) { "$Title  v$ver" } else { $Title }

$form = New-Object System.Windows.Forms.Form
$form.Text = $winTitle
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::Sizable
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::None
$form.ShowIcon = $true
$form.ShowInTaskbar = $true
$form.KeyPreview = $true

# ---------------- 窗口图标（绿底白勾 app.ico） ----------------
# 标题栏 + 任务栏 + 托盘都用它。找不到就退回系统默认，绝不让窗口起不来。
$appIcon = $null
$appIconPath = $null
foreach ($base in @($AppDir, $Root)) {
    if (-not $base) { continue }
    foreach ($rel in @('app.ico', '..\app.ico')) {
        $cand = [System.IO.Path]::GetFullPath((Join-Path $base $rel))
        if (Test-Path $cand) {
            try {
                $appIcon = New-Object System.Drawing.Icon($cand)
                $form.Icon = $appIcon
                $appIconPath = $cand
                break
            } catch { }
        }
    }
    if ($appIcon) { break }
}

# ⚠️ 只设 $form.Icon 只影响标题栏的小图标；**任务栏用的是 WM_SETICON 的 ICON_BIG**，
#    不显式发一次，任务栏仍然显示 pythonw.exe 的图标（2026-10-30 用户实测）。
#    所以窗口句柄一建好就再补一发大图标（取 256px 那帧最清晰）。
$script:bigIcon = $null
$form.add_Load({
    if (-not $appIconPath) { return }
    try {
        $script:bigIcon = New-Object System.Drawing.Icon($appIconPath, 256, 256)
        [void][WinFx]::SendMessage($this.Handle, 0x0080, [IntPtr]1, $script:bigIcon.Handle)  # WM_SETICON / ICON_BIG
        [void][WinFx]::SendMessage($this.Handle, 0x0080, [IntPtr]0, $appIcon.Handle)           # WM_SETICON / ICON_SMALL
    } catch { }
})
$form.MinimumSize = New-Object System.Drawing.Size((S 880), (S 600))
$form.Size = New-Object System.Drawing.Size((S $Width), (S $Height))
$form.BackColor = [System.Drawing.Color]::White

# ---------------- WebView2 铺满客户区 ----------------
$web = [Activator]::CreateInstance($tWebView)
$web.Dock = [System.Windows.Forms.DockStyle]::Fill
$form.Controls.Add($web)

# ---------------- Windows 11：把系统标题栏刷成白色，与页面连成一片 ----------------
# DWMWA_BORDER_COLOR(34) / DWMWA_CAPTION_COLOR(35) / DWMWA_TEXT_COLOR(36)
# 颜色是 COLORREF = 0x00BBGGRR
function Set-CaptionLook {
    try {
        if ([Environment]::OSVersion.Version.Build -lt 22000) { return }   # 仅 Win11+
        $h = $form.Handle
        $border = 0x00ECE8E4          # RGB(228,232,236)
        $caps   = 0x00FFFFFF          # 纯白
        $text   = 0x00211D1A          # RGB(26,29,33)
        [void][WinFx]::DwmSetWindowAttribute($h, 34, [ref]$border, 4)
        [void][WinFx]::DwmSetWindowAttribute($h, 35, [ref]$caps, 4)
        [void][WinFx]::DwmSetWindowAttribute($h, 36, [ref]$text, 4)
    } catch {}
}
$form.add_Load({ Set-CaptionLook })
$form.add_Shown({ Set-CaptionLook })

# ---------------- 最小化 / 关闭 => 进托盘 ----------------
# 必须配合 $Application::Run() 使用：若用 $form.ShowDialog()，Hide() 会结束模态循环
# 让整个窗口退出——那就是早期「点—就退出」的真根因。
$script:exiting = $false

function Show-Window {
    $form.Show()
    $form.WindowState = [System.Windows.Forms.FormWindowState]::Normal
    $form.Activate()
}

# 自己画的小提示卡（替代系统的托盘气泡）
#
# 为什么不用 $ni.ShowBalloonTip：Win10/11 会把气泡转成系统通知，而通知顶部显示的
# 「应用名」来自**进程身份**（我们是 powershell.exe），于是永远显示 "Windows PowerShell"，
# 那是 ShowBalloonTip 的参数改不了的。自己画一个就没有这个问题，标题完全可控，
# 还能和界面配色统一。
function Show-Toast([string]$text) {
    try {
        # 上一张还没消失就先收掉，避免连点最小化时卡片叠在一起
        try {
            if ($script:toastForm -and -not $script:toastForm.IsDisposed) { $script:toastForm.Close() }
        } catch {}
        $tw = S 340
        $th = S 92
        $t = New-Object System.Windows.Forms.Form
        $t.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
        $t.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
        $t.ShowInTaskbar = $false
        $t.TopMost = $true
        $t.BackColor = [System.Drawing.Color]::FromArgb(26, 29, 33)
        $t.Size = New-Object System.Drawing.Size($tw, $th)

        $l1 = New-Object System.Windows.Forms.Label
        $l1.Text = $Title
        $l1.AutoSize = $false
        $l1.Bounds = New-Object System.Drawing.Rectangle((S 17), (S 15), ($tw - (S 34)), (S 20))
        $l1.ForeColor = [System.Drawing.Color]::FromArgb(154, 164, 174)
        $l1.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)
        $l1.BackColor = [System.Drawing.Color]::Transparent

        $l2 = New-Object System.Windows.Forms.Label
        $l2.Text = $text
        $l2.AutoSize = $false
        $l2.Bounds = New-Object System.Drawing.Rectangle((S 17), (S 39), ($tw - (S 34)), ($th - (S 55)))
        $l2.ForeColor = [System.Drawing.Color]::White
        $l2.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 10.5, [System.Drawing.FontStyle]::Bold)
        $l2.BackColor = [System.Drawing.Color]::Transparent

        $t.Controls.Add($l1)
        $t.Controls.Add($l2)

        try {
            $hr = [WinFx]::CreateRoundRectRgn(0, 0, $tw + 1, $th + 1, (S 10), (S 10))
            $t.Region = [System.Drawing.Region]::FromHrgn($hr)
        } catch {}

        $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
        $t.Location = New-Object System.Drawing.Point(
            ($wa.Right - $tw - (S 18)),
            ($wa.Bottom - $th - (S 18)))
        $t.Show()

        # 用脚本作用域持有引用：脚本块在函数返回后仍要能访问到它们
        $script:toastForm = $t
        $script:toastTimer = New-Object System.Windows.Forms.Timer
        $script:toastTimer.Interval = 1900
        $script:toastTimer.add_Tick({
            try { $script:toastTimer.Stop(); $script:toastForm.Close() } catch {}
        })
        $script:toastTimer.Start()
    } catch {}
}

function Hide-ToTray([string]$tip) {
    $form.Hide()
    $ni.Visible = $true
    if ($tip) { Show-Toast $tip }
}

# 最小化 => 收进托盘（不占任务栏）
$form.add_Resize({
    if ($form.WindowState -eq [System.Windows.Forms.FormWindowState]::Minimized) {
        Hide-ToTray '已最小化到托盘，双击图标恢复'
    }
})

# 点 ✕ => 也收进托盘；真正退出走托盘菜单的「退出」
$form.add_FormClosing({
    param($s, $e)
    if (-not $script:exiting) {
        $e.Cancel = $true
        Hide-ToTray '已收进托盘，双击图标可恢复；要彻底退出请右键托盘图标 → 退出'
    } else {
        try { $ni.Visible = $false; $ni.Dispose() } catch {}
    }
})

# ---------------- 托盘 ----------------
$ni = New-Object System.Windows.Forms.NotifyIcon
$ni.Icon = if ($appIcon) { $appIcon } else { [System.Drawing.SystemIcons]::Application }
$ni.Text = $Title
$ni.Visible = $true
$ni.add_DoubleClick({ Show-Window })
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miShow = $menu.Items.Add('显示窗口')
$miShow.add_Click({ Show-Window })
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
$miExit = $menu.Items.Add('退出')
$miExit.add_Click({
    $script:exiting = $true
    try { $ni.Visible = $false } catch {}
    $form.Close()
})
$ni.ContextMenuStrip = $menu

# ---------------- 快捷键 ----------------
$form.add_KeyDown({
    # Esc 只收进托盘，不再直接退出（误按代价太大）
    if ($_.KeyCode -eq [System.Windows.Forms.Keys]::Escape) { Hide-ToTray '已收进托盘' }
    if ($_.KeyCode -eq [System.Windows.Forms.Keys]::F5 -and $web.CoreWebView2) { $web.CoreWebView2.Reload() }
})

# ---------------- WebView2 ----------------
$web.add_CoreWebView2InitializationCompleted({
    param($s, $e)
    if (-not $e.IsSuccess) {
        $script:exiting = $true
        [System.Windows.Forms.MessageBox]::Show("WebView2 初始化失败：$($e.InitializationException)", '错误') | Out-Null
        $form.Close(); return
    }
    $st = $web.CoreWebView2.Settings
    $st.AreDefaultContextMenusEnabled = $false
    $st.IsZoomControlEnabled = $false
    $st.IsStatusBarEnabled = $false
    $st.AreDevToolsEnabled = $true

    # 页面里显式的窗口按钮（若页面提供了）也能用；这不是必需路径。
    $web.CoreWebView2.add_WebMessageReceived({
        param($s2, $e2)
        $msg = ''
        try { $msg = $e2.TryGetWebMessageAsString() } catch { return }
        switch ($msg) {
            'win:minimize' { Hide-ToTray '已最小化到托盘' }
            'win:maximize' {
                if ($form.WindowState -eq [System.Windows.Forms.FormWindowState]::Maximized) {
                    $form.WindowState = [System.Windows.Forms.FormWindowState]::Normal
                } else {
                    $form.WindowState = [System.Windows.Forms.FormWindowState]::Maximized
                }
            }
            'win:close' { $script:exiting = $true; try { $ni.Visible = $false } catch {}; $form.Close() }
        }
    })

    $web.CoreWebView2.Navigate($Url)
})
$form.add_Shown({
    $udf = Join-Path $env:LOCALAPPDATA 'auto-checkin\webview2'
    New-Item -ItemType Directory -Force -Path $udf | Out-Null
    $env:WEBVIEW2_USER_DATA_FOLDER = $udf
    $web.EnsureCoreWebView2Async($null) | Out-Null
})

try {
    # 用 Application::Run 而不是 $form.ShowDialog()：
    # ShowDialog 是模态循环，里面调 Hide() 会把循环结束掉 → 整个窗口退出
    # （这就是早期「点—就退出」的根因）。Run 模式下 Hide() 只隐藏，消息循环继续，
    # 托盘图标和 Resize 事件都照常工作。
    [System.Windows.Forms.Application]::Run($form)
} finally {
    $script:exiting = $true
    try { $ni.Visible = $false; $ni.Dispose() } catch {}
    try { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue } catch {}

    # 关掉窗口就顺手停掉控制台后端。
    # 为什么：后端是 detached 进程，窗口退出它照样活着，会一直占着 8791 端口
    # 和 auto-checkin.exe 这个文件——用户以为"关了"，实际没关；重新打包时
    # 还会因为文件被占用而失败（2026-10-27 卡了两小时就是这个）。
    # ⚠️ 只杀这一个 pid，并且必须验明是 python/pythonw/auto-checkin，
    #    绝不能波及常驻签到的 autowatch（那是另一个独立进程）。
    if ($BackendPid -gt 0) {
        try {
            $bp = Get-Process -Id $BackendPid -ErrorAction SilentlyContinue
            if ($bp -and ($bp.ProcessName -match '^(python|pythonw|auto-checkin)$')) {
                Stop-Process -Id $BackendPid -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }
}
