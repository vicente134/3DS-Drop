[Setup]
AppId={{D3D5D20F-8B4A-4F3A-9C1E-3D5D20F00001}
AppName=3DS-Drop
AppVersion=1.0.0
AppPublisher=ElVichoPo (@vicente134)
AppPublisherURL=https://github.com/vicente134/3DS-Drop
DefaultDirName={autopf}\3DS-Drop
DefaultGroupName=3DS-Drop
PrivilegesRequired=lowest
OutputDir=..
OutputBaseFilename=3DS-Drop-Windows-Setup
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\3DS-Drop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\3DS-Drop"; Filename: "{app}\3DS-Drop.exe"
Name: "{autodesktop}\3DS-Drop"; Filename: "{app}\3DS-Drop.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\3DS-Drop.exe"; Description: "{cm:LaunchProgram,3DS-Drop}"; Flags: nowait postinstall skipifsilent
