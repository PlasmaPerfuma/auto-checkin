; auto-checkin 安装包脚本（Inno Setup 6）
;
; 由 build-setup.cmd 调 ISCC 编译：它会把本文件重编码成"带 BOM 的副本"再编译，
; 因为 Inno 6 期望 UTF-8 + BOM，而编辑工具一般会剥掉 BOM（和 .ps1 一个毛病）。
;
; 安装是"按用户"的（PrivilegesRequired=lowest），不需要管理员，不写系统目录。
; 数据与登录态在 %APPDATA%\auto-checkin（程序自己建的），卸载不会删。

#define MyAppName "自动签到"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "auto-checkin"
#define MyAppExeName "auto-checkin.exe"
#ifndef SrcDir
  #define SrcDir "D:\ac-build\dist\auto-checkin"
#endif
; 图标用绝对路径传进来（这个脚本会被复制到 tmp\ 里再编译，相对路径会失效）
#ifndef IconFile
  #define IconFile "..\app.ico"
#endif

; README 的位置：.iss 会被复制到 tmp\ 再编译，所以相对路径不可靠。
; build-setup.cmd 会用 /DReadmeFile 传绝对路径进来；直接编译时才退回同级目录。
#ifndef ReadmeFile
  #define ReadmeFile "{#SourcePath}\README.md"
#endif

; 安装后验收清单（同样由 build-setup.cmd 用 /DChecklistFile 传绝对路径）
#ifndef ChecklistFile
  #define ChecklistFile "{#SourcePath}\CHECKLIST.md"
#endif

[Setup]
AppId={{8E4C2A71-3B5D-4E9A-9C1F-6A7B2D5E8F03}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\AutoCheckin
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=D:\ac-build\dist\setup
OutputBaseFilename=auto-checkin-setup-{#MyAppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0

[Languages]
; 官方只随包英文；中文界面需要额外下载 ChineseSimplified.isl，这里不折腾
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional tasks:"
Name: "autostart"; Description: "Start automatically at logon (daemon + watchdog)"; GroupDescription: "Additional tasks:"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#ReadmeFile}"; DestDir: "{app}"; DestName: "README.md"; Flags: ignoreversion
Source: "{#ChecklistFile}"; DestDir: "{app}"; DestName: "安装后验收清单.md"; Flags: ignoreversion
; 图标文件本身也放进程序目录：快捷方式直接引用它，换图标不用重新编译 exe
Source: "{#IconFile}"; DestDir: "{app}"; DestName: "app.ico"; Flags: ignoreversion

[Icons]
; 零黑框入口：exe 是 GUI 子系统，带 dashboard 参数才开窗口
Name: "{group}\{#MyAppName} 控制台"; Filename: "{app}\{#MyAppExeName}"; Parameters: "dashboard"; IconFilename: "{app}\app.ico"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{group}\使用说明"; Filename: "{app}\README.md"
Name: "{group}\安装后验收清单"; Filename: "{app}\安装后验收清单.md"
Name: "{autodesktop}\{#MyAppName} 控制台"; Filename: "{app}\{#MyAppExeName}"; Parameters: "dashboard"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Registry]
; 开机自启：HKCU Run，不需要管理员，也没有控制台窗口
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "auto-checkin-autowatch"; ValueData: """{app}\{#MyAppExeName}"" run"; Flags: uninsdeletevalue; Tasks: autostart
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "auto-checkin-watchdog"; ValueData: """{app}\{#MyAppExeName}"" --internal-run watchdog.py"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "dashboard"; Description: "Launch the console now"; Flags: postinstall nowait skipifsilent
