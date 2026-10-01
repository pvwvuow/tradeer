; Inno Setup script. Per-user install (no admin rights) so the app runs as the same
; Windows user and privilege level as the MetaTrader 5 terminal (spec I2).
#define MyAppName "MT5 Trading Workstation"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppExeName "MT5TradingWorkstation.exe"

[Setup]
AppId={{8F6E2C1A-4B7D-4E8F-9A3C-2D5B6E7F8A90}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=pvwvuow
DefaultDirName={localappdata}\Programs\MT5TradingWorkstation
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=MT5TradingWorkstation-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
UninstallDisplayIcon={app}\{#MyAppExeName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MT5TradingWorkstation\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
