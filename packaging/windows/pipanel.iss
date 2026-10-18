; Windows installer of PIPanel (Inno Setup 6). Built by .github/workflows/release.yml:
;   iscc /DAppVersion=0.2.0 /DSourceDir=dist\PIPanel /DOutputDir=dist packaging\windows\pipanel.iss
;
; The app goes to Program Files, where only administrators can change it: that keeps pipanel-helper.ps1 (the only
; script PIPanel runs as administrator) safe. Everything PIPanel downloads later (Nginx, PHP, databases) and every
; setting live in %LOCALAPPDATA%\PIPanel and are left alone on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\PIPanel"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{6F8A3C1E-5B2D-4E7A-9C41-2D8E7B5A9F10}
AppName=PIPanel
AppVersion={#AppVersion}
AppVerName=PIPanel {#AppVersion}
AppPublisher=Pahrul Irfan
AppPublisherURL=https://github.com/pahrulirfan/PHP-DevTools
AppSupportURL=https://pahrulirfan.github.io/PHP-DevTools/
DefaultDirName={autopf}\PIPanel
DefaultGroupName=PIPanel
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=PIPanel-{#AppVersion}-windows-x64-setup
SetupIconFile=pipanel.ico
UninstallDisplayIcon={app}\PIPanel.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PIPanel"; Filename: "{app}\PIPanel.exe"
Name: "{autodesktop}\PIPanel"; Filename: "{app}\PIPanel.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PIPanel.exe"; Description: "{cm:LaunchProgram,PIPanel}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; stop the services and the agent of the user who uninstalls
Filename: "{app}\pipanel-cli.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopServices"
Filename: "{sys}\taskkill.exe"; Parameters: "/IM pipanel-cli.exe /F /T"; Flags: runhidden; RunOnceId: "StopAgent"
Filename: "{sys}\taskkill.exe"; Parameters: "/IM PIPanel.exe /F"; Flags: runhidden; RunOnceId: "StopApp"

[Registry]
; login items that PIPanel may have added for this user
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "PIPanel"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "PIPanel services"; Flags: uninsdeletevalue
