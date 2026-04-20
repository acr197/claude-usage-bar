# Building the installer

This produces a single `ClaudeUsageBarSetup.exe` that anyone can double-click
to install. No Python required on the user's machine.

## Prerequisites (one-time setup on your dev machine)

1. **Python 3.10+** installed
2. **Dependencies** installed:
   ```
   pip install PySide6 requests browser-cookie3 pyinstaller
   ```
3. **Inno Setup** installed:
   https://jrsoftware.org/isinfo.php (free, ~5 MB)

## Step 1 — Build the exe with PyInstaller

From the project folder:

```
pyinstaller claude_usage_bar.spec
```

This creates `dist\ClaudeUsageBar.exe`. It takes 1-3 minutes the first time.
The exe is self-contained — no Python needed on the user's machine.

**If you get a missing module error:**
Add the module name to `hiddenimports` in `claude_usage_bar.spec` and rebuild.

**Optional icon:**
Drop a `claude_usage_bar.ico` file in the project folder before building.
If you skip this, remove the `icon=` line from the spec and the
`SetupIconFile=` line from `installer.iss`.

## Step 2 — Build the installer with Inno Setup

1. Open **Inno Setup Compiler** (installed above)
2. File → Open → select `installer.iss`
3. Build → Compile (or press F9)

This produces `installer\ClaudeUsageBarSetup.exe`.

**That file is what you distribute.** Users double-click it, click through
a standard Windows wizard, and the app is installed with:
- Start Menu shortcut
- Desktop shortcut
- Optional startup entry (checkbox during install)
- Standard uninstaller in Add/Remove Programs

## What users see when they install

1. Welcome screen
2. License screen (if you add a license file)
3. Destination folder (defaults to Program Files)
4. Additional options: "Start on Windows startup" checkbox
5. Ready to install
6. Installing…
7. Finish — with "Launch Claude Usage Bar" checkbox

On first launch, the bar appears in demo mode at the bottom of the screen.
Right-click → Setup to configure the cookie source and test the connection.

## Updating for a new version

1. Bump the version string in `claude_usage_bar.py` (top comment)
2. Bump `AppVersion=` in `installer.iss`
3. Rerun PyInstaller, then Inno Setup
4. Ship the new `ClaudeUsageBarSetup.exe`

## File structure after PyInstaller runs

```
claude-usage-bar/
├── claude_usage_bar.py       # source
├── claude_usage_bar.spec     # PyInstaller config
├── installer.iss             # Inno Setup config
├── requirements.txt
├── run.bat                   # dev launcher (no installer needed)
├── BUILD.md                  # this file
├── dist/
│   └── ClaudeUsageBar.exe    # standalone exe (input for Inno Setup)
├── build/                    # PyInstaller temp files, safe to delete
└── installer/
    └── ClaudeUsageBarSetup.exe   # final distributable
```
