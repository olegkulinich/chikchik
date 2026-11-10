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

[Tasks]
Name: "contextmenu"; Description: "Add ""Trim / Crop"" and ""Trim / Crop → GIF"" to the context menu"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Registry]
; Explorer right-click items (classic menu; on Windows 11 under "Show more options").
; One task for both items (task contextmenu): either both or none; unchecked on update →
; the keys are removed again. Key names QuickTrimCrop / QuickTrimCropGIF keep them adjacent.
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\QuickTrimCrop"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\QuickTrimCrop"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\QuickTrimCrop\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\QuickTrimCrop"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\QuickTrimCropGIF"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop → GIF"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\QuickTrimCropGIF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\QuickTrimCropGIF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --gif ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\QuickTrimCropGIF"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\QuickTrimCrop"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\QuickTrimCrop"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\QuickTrimCrop\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\QuickTrimCrop"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\QuickTrimCropGIF"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop → GIF"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\QuickTrimCropGIF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\QuickTrimCropGIF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --gif ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\QuickTrimCropGIF"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\QuickTrimCrop"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\QuickTrimCrop"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\QuickTrimCrop\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\QuickTrimCrop"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\QuickTrimCropGIF"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop → GIF"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\QuickTrimCropGIF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\QuickTrimCropGIF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --gif ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\QuickTrimCropGIF"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\QuickTrimCrop"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\QuickTrimCrop"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\QuickTrimCrop\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\QuickTrimCrop"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\QuickTrimCropGIF"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop → GIF"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\QuickTrimCropGIF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\QuickTrimCropGIF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --gif ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\QuickTrimCropGIF"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\QuickTrimCrop"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\QuickTrimCrop"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\QuickTrimCrop\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\QuickTrimCrop"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\QuickTrimCropGIF"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop → GIF"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\QuickTrimCropGIF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\QuickTrimCropGIF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --gif ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\QuickTrimCropGIF"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\QuickTrimCrop"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\QuickTrimCrop"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\QuickTrimCrop\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\QuickTrimCrop"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\QuickTrimCropGIF"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop → GIF"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\QuickTrimCropGIF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\QuickTrimCropGIF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --gif ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\QuickTrimCropGIF"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mts\shell\QuickTrimCrop"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mts\shell\QuickTrimCrop"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mts\shell\QuickTrimCrop\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mts\shell\QuickTrimCrop"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mts\shell\QuickTrimCropGIF"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop → GIF"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mts\shell\QuickTrimCropGIF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mts\shell\QuickTrimCropGIF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --gif ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mts\shell\QuickTrimCropGIF"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ts\shell\QuickTrimCrop"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ts\shell\QuickTrimCrop"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ts\shell\QuickTrimCrop\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ts\shell\QuickTrimCrop"; ValueType: none; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ts\shell\QuickTrimCropGIF"; ValueType: string; ValueName: ""; ValueData: "Trim / Crop → GIF"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ts\shell\QuickTrimCropGIF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe},0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ts\shell\QuickTrimCropGIF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --gif ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ts\shell\QuickTrimCropGIF"; ValueType: none; Flags: deletekey; Tasks: not contextmenu

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
