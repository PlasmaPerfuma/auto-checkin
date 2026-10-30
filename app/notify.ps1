# notify.ps1 - a small flat toast card for sign-in results.
#
# Called by autowatch.py after each session:
#   powershell -NoProfile -ExecutionPolicy Bypass -File notify.ps1 -Title .. -Text .. -Kind ok|bad
#
# Design rules (same as the console):
#   * flat: no border ring, no colour bands; boundary = rounded corners + soft shadow
#   * accent colour only on the small dot (green = signed, red = missed)
#   * auto-closes; click closes immediately
#
# ASCII only on purpose: Chinese text arrives as parameters, so this file never
# needs a BOM.

param(
    [string]$Title = 'auto-checkin',
    [string]$Text = '',
    [string]$Kind = 'ok',
    [int]$Seconds = 7
)

try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Nfx {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("gdi32.dll")] public static extern IntPtr CreateRoundRectRgn(int a, int b, int c, int d, int e, int f);
    [DllImport("user32.dll")] public static extern int SetClassLong(IntPtr hWnd, int nIndex, int dwNewLong);
}
'@

    [Nfx]::SetProcessDPIAware() | Out-Null

    $scale = 1.0
    try {
        $g = [System.Drawing.Graphics]::FromHwnd([IntPtr]::Zero)
        $scale = $g.DpiX / 96.0
        $g.Dispose()
    } catch { $scale = 1.0 }
    function S([double]$v) { [int]($v * $scale) }

    $dotColor = if ($Kind -eq 'bad') { '#C5282D' } else { '#008B45' }
    $w = S 360
    $h = S 92

    $form = New-Object System.Windows.Forms.Form
    $form.FormBorderStyle = 'None'
    $form.StartPosition = 'Manual'
    $form.ShowInTaskbar = $false
    $form.TopMost = $true
    $form.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
    $form.Width = $w
    $form.Height = $h
    $form.Padding = New-Object System.Windows.Forms.Padding(0)

    $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $form.Location = New-Object System.Drawing.Point(($wa.Right - $w - (S 20)), ($wa.Bottom - $h - (S 20)))

    # rounded corners + system drop shadow (no visible edge band)
    $form.add_Load({
        $r = [Nfx]::CreateRoundRectRgn(0, 0, $form.Width + 1, $form.Height + 1, (S 12), (S 12))
        $form.Region = [System.Drawing.Region]::FromHrgn($r)
        [Nfx]::SetClassLong($form.Handle, -26, 0x20000) | Out-Null   # CS_DROPSHADOW
    })

    $dot = New-Object System.Windows.Forms.Panel
    $dot.Size = New-Object System.Drawing.Size((S 8), (S 8))
    $dot.Location = New-Object System.Drawing.Point((S 18), (S 22))
    $dot.BackColor = [System.Drawing.ColorTranslator]::FromHtml($dotColor)
    $form.Controls.Add($dot)

    $l1 = New-Object System.Windows.Forms.Label
    $l1.Text = $Title
    $l1.AutoSize = $false
    $l1.Location = New-Object System.Drawing.Point((S 34), (S 16))
    $l1.Size = New-Object System.Drawing.Size(($w - (S 56)), (S 20))
    $l1.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', [float](9.5 * $scale), [System.Drawing.FontStyle]::Bold)
    $l1.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#1A1D21')
    $l1.BackColor = [System.Drawing.Color]::Transparent
    $form.Controls.Add($l1)

    $l2 = New-Object System.Windows.Forms.Label
    $l2.Text = $Text
    $l2.AutoSize = $false
    $l2.Location = New-Object System.Drawing.Point((S 34), (S 40))
    $l2.Size = New-Object System.Drawing.Size(($w - (S 54)), (S 40))
    $l2.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', [float](8.8 * $scale))
    $l2.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#5F6B76')
    $l2.BackColor = [System.Drawing.Color]::Transparent
    $form.Controls.Add($l2)

    foreach ($c in @($form, $dot, $l1, $l2)) {
        $c.Cursor = [System.Windows.Forms.Cursors]::Arrow
    }
    $form.add_Click({ $script:closeNow = $true; $form.Close() })
    $l1.add_Click({ $script:closeNow = $true; $form.Close() })
    $l2.add_Click({ $script:closeNow = $true; $form.Close() })

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = [Math]::Max(1500, $Seconds * 1000)
    $timer.add_Tick({ $timer.Stop(); $form.Close() })
    $timer.Start()

    [System.Windows.Forms.Application]::Run($form)
} catch {
    # a failed notification must never break the check-in flow
    exit 0
}
