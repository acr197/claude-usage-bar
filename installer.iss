; Claude Usage Bar - Inno Setup Installer Script
; Version 0.1.2
;
; Prerequisites:
;   1. Run PyInstaller first to produce dist\ClaudeUsageBar.exe
;      Command: pyinstaller claude_usage_bar.spec
;   2. Install Inno Setup from https://jrsoftware.org/isinfo.php
;   3. Open this file in Inno Setup Compiler and click Build > Compile
;
; Output: installer\ClaudeUsageBarSetup.exe

[Setup]
AppName=Claude Usage Bar
AppVersion=0.1.2
AppPublisher=Claude Usage Bar
AppPublisherURL=https://github.com/acr197/claude-usage-bar
AppSupportURL=https://github.com/acr197/claude-usage-bar/issues
AppUpdatesURL=https://github.com/acr197/claude-usage-bar/releases

; Where the app gets installed
DefaultDirName={autopf}\ClaudeUsageBar
DefaultGroupName=Claude Usage Bar

; Installer output location and filename
OutputDir=installer
OutputBaseFilename=ClaudeUsageBarSetup

; Require admin rights to install to Program Files
PrivilegesRequired=admin

; Compression
Compression=lzma2
SolidCompression=yes

; Installer UI appearance
WizardStyle=modern
SetupIconFile=claude_usage_bar.ico

; Allow running at the end of install
UninstallDisplayIcon={app}\ClaudeUsageBar.exe

; Minimum Windows 10 required (Electron app needs it anyway)
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Offer a startup shortcut as an opt-in checkbox during install
Name: "startup"; Description: "Start Claude Usage Bar when Windows starts"; \
    GroupDescription: "Additional options:"; Flags: unchecked

[Files]
; The PyInstaller single-file exe
Source: "dist\ClaudeUsageBar.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu shortcut
Name: "{group}\Claude Usage Bar"; Filename: "{app}\ClaudeUsageBar.exe"
Name: "{group}\Uninstall Claude Usage Bar"; Filename: "{uninstallexe}"

; Desktop shortcut
Name: "{autodesktop}\Claude Usage Bar"; Filename: "{app}\ClaudeUsageBar.exe"

; Startup shortcut - only created if the user checked the task box above
Name: "{autostartup}\Claude Usage Bar"; Filename: "{app}\ClaudeUsageBar.exe"; \
    Tasks: startup

[Run]
; Offer to launch the app immediately after install finishes
Filename: "{app}\ClaudeUsageBar.exe"; \
    Description: "Launch Claude Usage Bar"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up the config folder on uninstall
Type: filesandordirs; Name: "{userappdata}\ClaudeUsageBar"

[Messages]
; Customize the finish page message
FinishedLabel=Claude Usage Bar is installed.%n%nThe bar will appear at the bottom of your screen. Right-click it to set up your cookie source.
