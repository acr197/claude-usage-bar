@echo off
REM Claude Usage Bar launcher
REM Double-click to run. Uses pythonw so no console window appears.

cd /d "%~dp0"

REM Create venv on first run
if not exist ".venv\Scripts\pythonw.exe" (
    echo First run: creating virtual env and installing deps...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install --upgrade pip
    call .venv\Scripts\deactivate.bat
)

REM Always sync deps so new requirements (e.g. curl_cffi) are picked up
call .venv\Scripts\pip install -q -r requirements.txt

REM Launch silently
start "" ".venv\Scripts\pythonw.exe" "claude_usage_bar.py"
