#define AppName "PromVector Agent"
#define AppVersion "0.1.0"

[Setup]
AppId={{E98CC624-8F5D-4953-9724-6D327DF2F1B0}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\PromVector Agent
DefaultGroupName=PromVector Agent
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=PromVector-Agent-Setup
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
UninstallDisplayIcon={app}\PromVector Agent.exe

[Tasks]
Name: "startup"; Description: "Start PromVector Agent when I sign in"; Flags: unchecked

[Files]
Source: "..\dist\PromVector Agent.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\PromVector Agent"; Filename: "{app}\PromVector Agent.exe"
Name: "{userstartup}\PromVector Agent"; Filename: "{app}\PromVector Agent.exe"; Tasks: startup

[Run]
Filename: "{app}\PromVector Agent.exe"; Description: "Open PromVector Agent"; Flags: nowait postinstall skipifsilent
