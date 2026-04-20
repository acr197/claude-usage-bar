# Claude Usage Bar

Version 0.1.0

A thin always-on-top Windows widget that shows your Claude.ai Pro/Max usage (five-hour session + weekly cap) as two progress bars pinned to the bottom of your screen.

## Why this is not a DXT

You originally asked for a DXT. DXT (now .mcpb) extensions package a local MCP server, which only exposes *tools* Claude the model can call during a chat. They cannot render persistent UI in the Claude Desktop app. There is no overlay/chrome hook in the spec. This project is a separate desktop widget that sits above Claude Desktop (or any window) instead.

## What it does

- Frameless always-on-top bar, 580x34 px, pinned to bottom-center of your primary monitor
- Two progress bars: five-hour session and weekly cap
- Polls every 2 minutes
- Drag to move, right-click for Refresh / Setup / Reset position / Quit
- Tooltip shows reset times
- Demo mode on first run so you can verify the UI before wiring real data

## Install

Requires Python 3.10 or newer on Windows.

1. Unzip this folder somewhere permanent, like `C:\Tools\claude-usage-bar\`
2. Double-click `run.bat`
3. First run creates a `.venv` and installs dependencies
4. The bar appears at the bottom of your screen in demo mode

## Wiring up real data

Anthropic does not publish a usage API for consumer plans. The `claude.ai/settings/usage` page fetches data from an internal endpoint that is not documented and may change. You have to find it once, paste it into Setup, and the widget handles the rest.

**Steps:**

1. Open Chrome, log into claude.ai
2. Go to `https://claude.ai/settings/usage`
3. Open DevTools (F12) and switch to the Network tab
4. Refresh the page, filter by `Fetch/XHR`
5. Look for a request returning JSON with your usage numbers. Likely named something like `usage`, `bootstrap`, `limits`, or contains `organization` in the path
6. Right-click the request, "Copy as URL"
7. Click the response preview and note the JSON path to:
   - Session percent used (e.g. `usage.session.percent_used`)
   - Weekly percent used
   - Session reset time (optional)
   - Weekly reset time (optional)
8. Right-click the bar, choose `Setup…`, paste the URL and paths, uncheck **Demo mode**, click Save

The widget reads your Chrome cookies automatically (via `browser_cookie3`), so you stay authenticated as long as you're logged into claude.ai in your chosen browser.

## Supported browsers

`chrome` (default), `edge`, `firefox`, `brave`. Set in Setup.

> **Note on Chrome v127+:** Chrome introduced app-bound cookie encryption that sometimes breaks cookie extraction. If `browser_cookie3` fails on Chrome, try pointing it at Edge or Firefox instead (whichever you also use for claude.ai).

## Config location

`%APPDATA%\ClaudeUsageBar\config.json`

Delete this file to reset to defaults.

## Startup on boot (optional)

1. Press `Win+R`, type `shell:startup`, hit Enter
2. Drop a shortcut to `run.bat` in there

## Known limitations

- Internal endpoint may change without notice. If data stops updating, re-check the Network tab and update the URL in Setup.
- Uses an undocumented endpoint, so treat this as personal-use only and don't hammer it (2-minute poll default is deliberately gentle).
- Multi-monitor: always pins to primary display. Drag it wherever you want and it'll stay there until restart; use "Reset position" to recenter.

## Files

- `claude_usage_bar.py` - main widget
- `requirements.txt` - Python deps
- `run.bat` - launcher, creates venv on first run
- `README.md` - this file
