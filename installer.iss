; ChikChik! installer (Inno Setup 6). Built by build.bat:
;   ISCC /DAppVersion=x.y installer.iss
; Per-user install, no admin rights: %LOCALAPPDATA%\Programs\ChikChik
; Formerly QuickTrimCrop: same AppId, so it updates the old install in place and
; removes the old folder, Start menu shortcut and "Open with" entry.

#ifndef AppVersion
  #define AppVersion "0.0"
#endif
#define AppName "ChikChik!"
; File/folder name: no "!" (special character in cmd/bat).
#define AppTech "ChikChik"
#define AppExe "ChikChik.exe"
#define OldName "QuickTrimCrop"

[Setup]
; Never change AppId: it ties updates and the uninstaller to the same installation.
AppId={{04204E1A-5B89-4671-B709-80414D770E97}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppTech}
; The old install lived in ...\QuickTrimCrop — don't reuse it.
UsePreviousAppDir=no
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=dist
OutputBaseFilename={#AppTech}-{#AppVersion}-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
CloseApplications=yes
; Tells Explorer to refresh the right-click menu right away.
ChangesAssociations=yes

[InstallDelete]
; Updates: drop the old PyInstaller runtime so no stale files stay behind.
Type: filesandordirs; Name: "{app}\_internal"
; Rename QuickTrimCrop → ChikChik!: old program folder and Start menu shortcut.
Type: filesandordirs; Name: "{autopf}\{#OldName}"
Type: files; Name: "{autoprograms}\{#OldName}.lnk"

[Files]
Source: "dist\{#AppTech}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "THIRD-PARTY.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "LICENSE.LGPL"; DestDir: "{app}\licenses"; DestName: "mpv-LICENSE.LGPL"; Flags: ignoreversion
Source: "ffmpeg\LICENSE"; DestDir: "{app}\licenses"; DestName: "FFmpeg-LICENSE.txt"; Flags: ignoreversion
Source: "fonts\LICENSE-Inter.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "icons\LICENSE-FluentEmoji.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Registry]
; Rename: remove the old "Open with" entry (QuickTrimCrop.exe).
Root: HKCU; Subkey: "Software\Classes\Applications\{#OldName}.exe"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\.mp4\OpenWithList\{#OldName}.exe"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\.mkv\OpenWithList\{#OldName}.exe"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\.mov\OpenWithList\{#OldName}.exe"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\.m4v\OpenWithList\{#OldName}.exe"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\.avi\OpenWithList\{#OldName}.exe"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\.webm\OpenWithList\{#OldName}.exe"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\.mts\OpenWithList\{#OldName}.exe"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\.ts\OpenWithList\{#OldName}.exe"; ValueType: none; Flags: deletekey

; "Open with" list
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".mp4"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".mkv"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".mov"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".m4v"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".avi"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".webm"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".mts"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".ts"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\.mp4\OpenWithList\{#AppExe}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.mkv\OpenWithList\{#AppExe}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.mov\OpenWithList\{#AppExe}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.m4v\OpenWithList\{#AppExe}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.avi\OpenWithList\{#AppExe}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.webm\OpenWithList\{#AppExe}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.mts\OpenWithList\{#AppExe}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.ts\OpenWithList\{#AppExe}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
{ T30: Explorer right-click items "ChikChik!" / "ChikChik! → GIF" (HKCU).
  First install: both are added. Update: the user's choice (Settings → Explorer Menu)
  stays — items that exist only get the new title, icon and command. Uninstall: removed.
  Key names QuickTrimCrop / QuickTrimCropGIF stay (Explorer sorts verbs by key name). }

const
  ShellRoot = 'Software\Classes\SystemFileAssociations\';

var
  FirstInstall: Boolean;

function Extensions(I: Integer): String;
begin
  case I of
    0: Result := '.mp4';  1: Result := '.mkv';  2: Result := '.mov';  3: Result := '.m4v';
    4: Result := '.avi';  5: Result := '.webm'; 6: Result := '.mts';  7: Result := '.ts';
  end;
end;

function InitializeSetup(): Boolean;
begin
  // No uninstall entry for our AppId yet → a fresh install (not an update).
  FirstInstall := not RegKeyExists(HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{04204E1A-5B89-4671-B709-80414D770E97}_is1');
  Result := True;
end;

procedure WriteItem(Ext, KeyName, Title, Icon, Command: String);
var
  Key: String;
begin
  Key := ShellRoot + Ext + '\shell\' + KeyName;
  RegWriteStringValue(HKCU, Key, '', Title);
  RegWriteStringValue(HKCU, Key, 'Icon', Icon);
  RegWriteStringValue(HKCU, Key + '\command', '', Command);
end;

procedure UpdateItem(KeyName, Title, Icon, Args: String);
var
  I: Integer;
  Ext, Exe: String;
begin
  Exe := ExpandConstant('{app}\{#AppExe}');
  for I := 0 to 7 do
  begin
    Ext := Extensions(I);
    if FirstInstall or RegKeyExists(HKCU, ShellRoot + Ext + '\shell\' + KeyName) then
      WriteItem(Ext, KeyName, Title, Icon, '"' + Exe + '"' + Args + ' "%1"');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    UpdateItem('QuickTrimCrop', '{#AppName}', ExpandConstant('{app}\{#AppExe},0'), '');
    UpdateItem('QuickTrimCropGIF', '{#AppName} → GIF', ExpandConstant('{app}\gif.ico'), ' --gif');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  I: Integer;
begin
  if CurUninstallStep = usPostUninstall then
    for I := 0 to 7 do
    begin
      RegDeleteKeyIncludingSubkeys(HKCU, ShellRoot + Extensions(I) + '\shell\QuickTrimCrop');
      RegDeleteKeyIncludingSubkeys(HKCU, ShellRoot + Extensions(I) + '\shell\QuickTrimCropGIF');
    end;
end;
