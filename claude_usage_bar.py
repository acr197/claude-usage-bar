# ============================================================
# Claude Usage Bar - always-on-top Windows widget
# Version 0.3.1
# Shows Claude.ai Pro/Max usage limits as a thin bar pinned to
# the bottom of your primary monitor.
#
# Primary path: reads Claude Desktop session cookies, GETs
# claude.ai/settings/usage, and scrapes the percentages from HTML.
# User sees nothing, configures nothing.
#
# Fallback 1: if session cookies fail, prompt user to sign in.
# Fallback 2: if HTML parse fails, allow manual entry and
# save the unparsed HTML to disk for debugging.
# ============================================================

#------------
# Imports
#------------
import sys
import os
import re
import json
import sqlite3
import shutil
import base64
import tempfile
import subprocess
import ctypes
from pathlib import Path
from datetime import datetime

import requests
import win32crypt
from Cryptodome.Cipher import AES
import browser_cookie3

# curl_cffi for Cloudflare-resistant HTTP requests. It impersonates a
# real Chrome TLS/JA3 fingerprint so Cloudflare doesn't flag us as a bot.
# If it's not installed, we fall back to requests and Cloudflare will
# probably block.
try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CFFI = False
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QHBoxLayout, QVBoxLayout,
    QProgressBar, QMenu, QDialog, QLineEdit, QPushButton,
    QFormLayout, QCheckBox, QComboBox, QMessageBox, QSpinBox,
    QDialogButtonBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QGuiApplication, QDesktopServices, QIcon, QPixmap, QPainter, QColor, QBrush
)
from PySide6.QtCore import QUrl

#------------
# Config and constants
#------------
APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "ClaudeUsageBar"
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APP_DIR / "config.json"
DIAG_HTML_PATH = APP_DIR / "last_unparsed.html"

# Claude Desktop Electron profile location on Windows
CLAUDE_DESKTOP_DIR = Path(os.environ.get("APPDATA", "")) / "Claude"

# The page we scrape for usage data
USAGE_URL = "https://claude.ai/settings/usage"

# Poll interval in seconds
POLL_SECONDS = 120

# Diagnostic log path - rewritten every refresh so we always have fresh info
DIAG_LOG_PATH = APP_DIR / "diagnostic.log"

#------------
# Append a diagnostic line to the debug log, and mirror to stdout
#------------
def diag(msg):
    try:
        with open(DIAG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {msg}\n")
    except Exception:
        pass
    try:
        print(msg)
    except Exception:
        pass

#------------
# Wipe the log at the start of each refresh so old noise does not accumulate
#------------
def reset_diag():
    try:
        DIAG_LOG_PATH.write_text("", encoding="utf-8")
    except Exception:
        pass

#------------
# Return True if the Claude Desktop process is currently running
#------------
def is_claude_desktop_running():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Claude.exe", "/NH"],
            capture_output=True, text=True, timeout=3
        ).stdout
        return "Claude.exe" in out
    except Exception:
        return False

#------------
# Generate a 32x32 icon showing two progress bars on a dark background
#------------
def make_app_icon():
    px = QPixmap(32, 32)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(28, 28, 32)))
    p.drawRoundedRect(0, 0, 32, 32, 5, 5)
    track = QColor(70, 70, 78)
    fill = QColor(217, 119, 87)
    p.setBrush(QBrush(track))
    p.drawRoundedRect(4, 9, 24, 5, 2, 2)
    p.setBrush(QBrush(fill))
    p.drawRoundedRect(4, 9, 14, 5, 2, 2)
    p.setBrush(QBrush(track))
    p.drawRoundedRect(4, 18, 24, 5, 2, 2)
    p.setBrush(QBrush(fill))
    p.drawRoundedRect(4, 18, 9, 5, 2, 2)
    p.end()
    return QIcon(px)

# Default config - cookie_string is the most reliable auth method since
# it captures every cookie (including CF bot-defeat cookies) in one paste
DEFAULT_CONFIG = {
    "demo_mode": False,
    "browser": "chrome",
    "session_key": "",
    "cookie_string": "",
    "manual_override": False,
    "manual_session_pct": 0,
    "manual_weekly_pct": 0,
    "manual_session_reset": "",
    "manual_weekly_reset": "",
    "follow_claude_desktop": False
}

#------------
# Section: Config helpers
#------------

#------------
# Load config from disk, seed defaults if missing
#------------
def load_config():
    if not CONFIG_PATH.exists():
        with open(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg

print("load_config ready")

#------------
# Write config back to disk
#------------
def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

#------------
# Section: Claude Desktop cookie reader
# Claude Desktop is Electron, which embeds Chromium. Its cookie
# store lives under %APPDATA%\Claude\ in the same SQLite + AES-GCM
# format Chrome uses.
#------------

#------------
# Find the Cookies SQLite file in Claude Desktop's profile folder
#------------
def find_claude_desktop_cookie_db():
    candidates = [
        CLAUDE_DESKTOP_DIR / "Default" / "Cookies",
        CLAUDE_DESKTOP_DIR / "Default" / "Network" / "Cookies",
        CLAUDE_DESKTOP_DIR / "Cookies",
        CLAUDE_DESKTOP_DIR / "Network" / "Cookies",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None

#------------
# Decrypt the app-bound AES key from Claude Desktop's Local State
#------------
def get_claude_desktop_key():
    local_state_path = CLAUDE_DESKTOP_DIR / "Local State"
    if not local_state_path.exists():
        return None
    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            local_state = json.load(f)
        b64_key = local_state.get("os_crypt", {}).get("encrypted_key", "")
        if not b64_key:
            return None
        # First 5 bytes after base64-decoding are the literal prefix "DPAPI"
        raw = base64.b64decode(b64_key)[5:]
        return win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
    except Exception as e:
        print(f"get_claude_desktop_key error: {e}")
        return None

#------------
# Decrypt a single cookie value using AES-GCM or legacy DPAPI
#------------
def decrypt_value(encrypted_value, key):
    if not encrypted_value:
        return ""
    prefix = encrypted_value[:3]
    if prefix in (b"v10", b"v11") and key:
        try:
            iv = encrypted_value[3:15]
            tag = encrypted_value[-16:]
            payload = encrypted_value[15:-16]
            cipher = AES.new(key, AES.MODE_GCM, iv)
            return cipher.decrypt_and_verify(payload, tag).decode("utf-8")
        except Exception:
            return ""
    try:
        return win32crypt.CryptUnprotectData(
            encrypted_value, None, None, None, 0
        )[1].decode("utf-8")
    except Exception:
        return ""

#------------
# Copy a file locked by another process using Windows share-aware API.
# Uses pywin32 which already ships with the app, so types are handled
# for us. FILE_SHARE flags let us read even while another process holds it.
#------------
def copy_locked_file(src, dst):
    import win32file
    import win32con
    # Open the source with sharing flags
    handle = win32file.CreateFile(
        str(src),
        win32con.GENERIC_READ,
        win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
        None,
        win32con.OPEN_EXISTING,
        win32con.FILE_ATTRIBUTE_NORMAL,
        None
    )
    try:
        src_size = os.path.getsize(src)
        chunks = []
        total_read = 0
        # Read in 64KB chunks until we've consumed the whole file
        while total_read < src_size:
            remaining = src_size - total_read
            chunk_size = min(65536, remaining)
            hr, data = win32file.ReadFile(handle, chunk_size)
            if not data:
                break
            chunks.append(bytes(data))
            total_read += len(data)
        diag(f"  copy_locked_file: read {total_read}/{src_size} bytes from {src}")
        with open(dst, "wb") as f:
            for c in chunks:
                f.write(c)
    finally:
        win32file.CloseHandle(handle)

print("copy_locked_file ready")

#------------
# Read claude.ai cookies straight from Claude Desktop's store.
# Tries SQLite immutable mode first (works while the app is running),
# then falls back to copying the file.
#------------
def get_claude_desktop_cookies():
    db_path = find_claude_desktop_cookie_db()
    if not db_path:
        raise FileNotFoundError(
            f"Claude Desktop cookie store not found under {CLAUDE_DESKTOP_DIR}"
        )
    diag(f"cookie db: {db_path}")
    try:
        diag(f"db size on disk: {os.path.getsize(db_path)} bytes")
    except Exception:
        pass
    key = get_claude_desktop_key()
    diag(f"aes key: {'found' if key else 'MISSING'}")

    def read_from(conn_str):
        # Read and decrypt all claude.ai cookies from the given SQLite URI.
        cookies = {}
        encrypted_fail = encrypted_ok = plain_ok = 0
        conn = sqlite3.connect(conn_str, uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            diag(f"tables: {tables}")
            cookie_table = next(
                (t for t in ("cookies", "moz_cookies") if t in tables), None
            )
            if not cookie_table:
                raise RuntimeError(f"No cookies table. Tables: {tables}")
            cur.execute(f"PRAGMA table_info({cookie_table})")
            cols = [r[1] for r in cur.fetchall()]
            enc_col = "encrypted_value" if "encrypted_value" in cols else None
            host_col = next(
                (c for c in ("host_key", "host", "domain") if c in cols), None
            )
            if not host_col:
                raise RuntimeError(f"No host column: {cols}")
            select_cols = "name, value" + (f", {enc_col}" if enc_col else "")
            sql = (
                f"SELECT {select_cols} FROM {cookie_table} "
                f"WHERE {host_col} LIKE '%claude.ai%'"
            )
            cur.execute(sql)
            for row in cur.fetchall():
                name, value = row[0], row[1]
                enc = row[2] if enc_col else None
                if value:
                    cookies[name] = value
                    plain_ok += 1
                else:
                    decoded = decrypt_value(enc, key) if enc else ""
                    if decoded:
                        cookies[name] = decoded
                        encrypted_ok += 1
                    else:
                        encrypted_fail += 1
        finally:
            conn.close()
        diag(
            f"cookies: plain={plain_ok} ok_enc={encrypted_ok} "
            f"fail_enc={encrypted_fail} names={sorted(cookies.keys())}"
        )
        return cookies

    # SQLite URI paths must use forward slashes on Windows
    def to_uri(p):
        return str(p).replace("\\", "/")

    # Attempt 1: immutable=1 tells SQLite to skip all locking, so this
    # succeeds even when Claude Desktop has the file exclusively locked.
    try:
        cookies = read_from(f"file:{to_uri(db_path)}?mode=ro&immutable=1")
        if cookies.get("sessionKey"):
            diag("immutable direct read succeeded")
            return cookies
        diag("immutable read: no sessionKey — trying copy fallback")
    except Exception as e:
        diag(f"immutable read failed: {e} — trying copy fallback")

    # Attempt 2: copy the file first (handles WAL mode edge cases).
    tmp_dir = Path(tempfile.mkdtemp(prefix="cub_"))
    main_dst = tmp_dir / "Cookies"
    try:
        copy_locked_file(db_path, main_dst)
        diag(f"copied main db: {os.path.getsize(main_dst)} bytes")
        for suffix in ("-wal", "-shm", "-journal"):
            companion = Path(str(db_path) + suffix)
            if companion.exists():
                dst = tmp_dir / ("Cookies" + suffix)
                try:
                    copy_locked_file(companion, dst)
                    diag(f"copied {suffix}: {os.path.getsize(dst)} bytes")
                except Exception as e:
                    diag(f"failed to copy {suffix}: {e}")
        return read_from(f"file:{to_uri(main_dst)}?mode=ro")
    except Exception as e:
        diag(f"copy approach also failed: {e}")
        raise
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

print("get_claude_desktop_cookies ready")

#------------
# Parse a raw Cookie header string like "name1=val1; name2=val2; ..."
# into a dict. Handles values with = signs inside them (sessionKey often has),
# and is tolerant of newlines, trailing commas, and curly braces in values.
#------------
def parse_cookie_string(s):
    out = {}
    if not s:
        return out
    # Normalize: strip whitespace, replace any newlines with spaces
    s = s.strip().replace("\r", " ").replace("\n", " ")
    # Some users paste with the leading `cookie:` header name, trim that off
    if s.lower().startswith("cookie:"):
        s = s[7:].strip()
    for part in s.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if name:
            out[name] = value
    return out

#------------
# Return cookies as a plain dict from the chosen source.
# Order of preference:
#   1. Full cookie string pasted into Setup (Cloudflare-friendly)
#   2. sessionKey-only (minimal, may be blocked by Cloudflare)
#   3. "auto" walks through installed browsers
#   4. explicit browser name
#   5. claude_desktop (usually fails due to file lock)
#------------
def get_claude_cookies(cfg):
    if cfg.get("cookie_string"):
        cookies = parse_cookie_string(cfg["cookie_string"])
        diag(f"using pasted cookie string: {len(cookies)} cookies, "
             f"names={sorted(cookies.keys())}")
        if not cookies:
            raise RuntimeError(
                "Pasted cookie string was empty or unparseable. "
                "Open Setup and re-paste."
            )
        if "sessionKey" not in cookies:
            raise RuntimeError(
                "Pasted cookie is missing sessionKey. Make sure you copied "
                "the value from the Network tab's Request Headers (not "
                "document.cookie from the Console)."
            )
        return cookies
    if cfg.get("session_key"):
        diag("using pasted sessionKey only (may be blocked by Cloudflare)")
        return {"sessionKey": cfg["session_key"].strip()}
    browser_name = (cfg.get("browser") or "auto").lower()
    if browser_name == "auto":
        order = ["chrome", "edge", "firefox", "brave"]
        last_err = None
        for b in order:
            try:
                diag(f"auto: trying {b}")
                jar = {
                    "chrome": browser_cookie3.chrome,
                    "edge": browser_cookie3.edge,
                    "firefox": browser_cookie3.firefox,
                    "brave": browser_cookie3.brave,
                }[b](domain_name="claude.ai")
                cookies = {c.name: c.value for c in jar}
                if cookies:
                    diag(f"auto: {b} returned {len(cookies)} cookies")
                    return cookies
                diag(f"auto: {b} returned no cookies")
            except Exception as e:
                last_err = e
                diag(f"auto: {b} failed: {e}")
        raise RuntimeError(
            f"No browser had claude.ai cookies. Last error: {last_err}"
        )
    if browser_name == "claude_desktop":
        return get_claude_desktop_cookies()
    loader = {
        "chrome": browser_cookie3.chrome,
        "edge": browser_cookie3.edge,
        "firefox": browser_cookie3.firefox,
        "brave": browser_cookie3.brave,
    }.get(browser_name, browser_cookie3.chrome)
    jar = loader(domain_name="claude.ai")
    return {c.name: c.value for c in jar}

#------------
# Section: HTML fetch and parse
#------------

class ParseError(Exception):
    pass

class AuthError(Exception):
    pass

#------------
# Fetch the settings page HTML using the given cookies.
# Uses curl_cffi with Chrome impersonation so Cloudflare's TLS
# fingerprinting sees us as a real browser. Falls back to requests
# if curl_cffi isn't available (Cloudflare will likely block).
#------------
def fetch_usage_page(cookies):
    diag(f"fetching {USAGE_URL} with {len(cookies)} cookies (curl_cffi={HAS_CFFI})")
    try:
        if HAS_CFFI:
            r = cffi_requests.get(
                USAGE_URL, cookies=cookies, timeout=20,
                impersonate="chrome", allow_redirects=False
            )
        else:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml"
            }
            r = requests.get(
                USAGE_URL, cookies=cookies, headers=headers,
                timeout=20, allow_redirects=False
            )
    except Exception as e:
        diag(f"request error: {e}")
        raise ConnectionError(str(e))
    diag(f"HTTP {r.status_code}, body length {len(r.text)}")
    try:
        with open(DIAG_HTML_PATH, "w", encoding="utf-8") as f:
            f.write(r.text)
        diag(f"response saved to {DIAG_HTML_PATH}")
    except Exception as e:
        diag(f"could not save response: {e}")
    if r.status_code in (301, 302, 303, 307, 308):
        location = r.headers.get("Location", "")
        diag(f"redirect target: {location}")
        if "login" in location.lower():
            raise AuthError("Redirected to login")
    if r.status_code in (401, 403):
        if "cf-browser-verification" in r.text.lower() or "just a moment" in r.text.lower():
            raise AuthError(f"Cloudflare challenge (HTTP {r.status_code})")
        raise AuthError(f"HTTP {r.status_code}")
    if r.status_code != 200:
        raise ConnectionError(f"HTTP {r.status_code}")
    text_lower = r.text.lower()
    login_markers = ["sign in with google", "enter your email", "continue with email"]
    if any(m in text_lower for m in login_markers):
        raise AuthError("Response contains login markers")
    return r.text

#------------
# Walk a nested JSON structure looking for usage-shaped data
#------------
def walk_for_usage(obj, depth=0):
    # Cap recursion so we don't blow the stack on big payloads
    if depth > 12:
        return None
    if isinstance(obj, dict):
        keys = set(obj.keys())
        # Heuristic: a dict with percent_used/percent and a reset field probably is usage
        if any(k in keys for k in ("percent_used", "percentage_used", "percent")):
            return obj
        for v in obj.values():
            found = walk_for_usage(v, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = walk_for_usage(v, depth + 1)
            if found:
                return found
    return None

#------------
# Extract session and weekly usage from the settings page HTML
#------------
def parse_usage_html(html):
    # Strategy 1 (most reliable): parse the progressbar ARIA attributes.
    # The settings page renders each bar as
    #   <div role="progressbar" aria-valuenow="9" aria-valuemax="100" aria-label="Usage">
    # The order in the DOM is: Current session, All models, Sonnet only,
    # Claude Design, Daily routine runs, Extra usage.
    # We want the first two.
    bars = re.findall(
        r'role="progressbar"[^>]*aria-valuenow="(\d{1,3})"',
        html
    )
    diag(f"progressbar values found: {bars}")
    if len(bars) >= 2:
        result = {
            "session_pct": int(bars[0]),
            "weekly_pct": int(bars[1]),
            "session_reset": "",
            "weekly_reset": ""
        }
        # Try to pull the reset strings too
        m = re.search(r"Resets\s+in\s+([^<\n]+?)\s*<", html, re.IGNORECASE)
        if m:
            result["session_reset"] = m.group(1).strip()
        m = re.search(
            r"All models.{0,500}?Resets\s+([^<\n]+?)\s*<",
            html, re.DOTALL | re.IGNORECASE
        )
        if m:
            result["weekly_reset"] = m.group(1).strip()
        return result

    # Strategy 2: text-landmark regex for "Current session ... X% used"
    result = {}
    m = re.search(
        r"Current session.{0,3000}?(\d{1,3})\s*%\s*used",
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        result["session_pct"] = int(m.group(1))

    m = re.search(
        r"All models.{0,3000}?(\d{1,3})\s*%\s*used",
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        result["weekly_pct"] = int(m.group(1))

    m = re.search(
        r"Current session.{0,3000}?Resets\s+in\s+([^<\n]+?)[<\n]",
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        result["session_reset"] = m.group(1).strip()

    m = re.search(
        r"All models.{0,3000}?Resets\s+([^<\n]+?)[<\n]",
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        result["weekly_reset"] = m.group(1).strip()

    if "session_pct" in result and "weekly_pct" in result:
        result.setdefault("session_reset", "")
        result.setdefault("weekly_reset", "")
        return result

    # Strategy 3: Next.js __NEXT_DATA__ blob fallback
    m = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.+?)</script>',
        html, re.DOTALL
    )
    if m:
        try:
            data = json.loads(m.group(1))
            found = walk_for_usage(data)
            if found:
                pct = found.get("percent_used") or found.get("percentage_used") or found.get("percent") or 0
                return {
                    "session_pct": pct,
                    "weekly_pct": pct,
                    "session_reset": "",
                    "weekly_reset": ""
                }
        except (json.JSONDecodeError, TypeError):
            pass

    # Save the HTML to disk for inspection
    try:
        with open(DIAG_HTML_PATH, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass
    raise ParseError(f"Could not locate usage values. HTML saved to {DIAG_HTML_PATH}")

print("parse_usage_html ready")

#------------
# Hit the known /usage endpoint and parse its full response.
# Keeps every utilization bucket plus its reset time so the details
# dialog can render them all.
# Uses curl_cffi with chrome impersonation to beat Cloudflare TLS checks.
#------------
def try_usage_apis(cookies):
    def _get(url):
        if HAS_CFFI:
            return cffi_requests.get(
                url, cookies=cookies, timeout=15,
                impersonate="chrome"
            )
        return requests.get(
            url, cookies=cookies, timeout=15,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )

    org_id = None
    try:
        r = _get("https://claude.ai/api/organizations")
        diag(f"/api/organizations: HTTP {r.status_code}")
        if r.status_code == 200:
            orgs = r.json()
            if isinstance(orgs, list) and orgs:
                org_id = orgs[0].get("uuid") or orgs[0].get("id")
                diag(f"org_id: {org_id}")
    except Exception as e:
        diag(f"org lookup error: {e}")
    if not org_id:
        return None

    url = f"https://claude.ai/api/organizations/{org_id}/usage"
    try:
        r = _get(url)
        diag(f"{url}: HTTP {r.status_code}, body len {len(r.text)}")
        if r.status_code != 200:
            return None
        body = r.json()

        # Friendly labels for each bucket. The names come straight from
        # the API response so we preserve the exact set the UI shows.
        LABELS = {
            "five_hour": "Current session",
            "seven_day": "All models (weekly)",
            "seven_day_sonnet": "Sonnet only (weekly)",
            "seven_day_opus": "Opus only (weekly)",
            "seven_day_omelette": "Claude Design (weekly)",
            "seven_day_cowork": "Cowork (weekly)",
            "seven_day_oauth_apps": "OAuth apps (weekly)",
            "iguana_necktie": "Iguana necktie",
            "omelette_promotional": "Omelette promo"
        }

        bars = []
        for key, label in LABELS.items():
            bucket = body.get(key)
            if not bucket or not isinstance(bucket, dict):
                continue
            pct = bucket.get("utilization")
            if pct is None:
                continue
            bars.append({
                "key": key,
                "label": label,
                "pct": int(float(pct)),
                "reset": format_reset_time(bucket.get("resets_at")),
                "reset_raw": bucket.get("resets_at") or ""
            })

        # Extra usage is structured differently
        extra = body.get("extra_usage") or {}
        if extra.get("is_enabled"):
            pct = extra.get("utilization")
            if pct is not None:
                bars.append({
                    "key": "extra_usage",
                    "label": "Extra usage",
                    "pct": int(float(pct)),
                    "reset": "",
                    "reset_raw": ""
                })

        # The two primary bars the widget surfaces. The UI's "All models"
        # view uses seven_day.
        five = body.get("five_hour") or {}
        seven = body.get("seven_day") or {}
        result = {
            "session_pct": int(float(five.get("utilization") or 0)),
            "weekly_pct": int(float(seven.get("utilization") or 0)),
            "session_reset": format_reset_time(five.get("resets_at")),
            "weekly_reset": format_reset_time(seven.get("resets_at")),
            "all_bars": bars,
            "rate_limit_tier": body.get("rate_limit_tier", "")
        }
        diag(
            f"parsed: session={result['session_pct']}%, "
            f"weekly={result['weekly_pct']}%, all_bars={len(bars)}"
        )
        return result
    except Exception as e:
        diag(f"usage fetch error: {e}")
        return None

#------------
# Turn a UTC ISO timestamp like "2026-04-20T04:59:59.621770+00:00" into
# a short "in Xh Ym" style string relative to now
#------------
def format_reset_time(iso_str):
    if not iso_str:
        return ""
    try:
        # Python datetime.fromisoformat handles offsets in 3.11+
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        from datetime import timezone
        now = datetime.now(timezone.utc)
        delta = ts - now
        total_minutes = int(delta.total_seconds() // 60)
        if total_minutes <= 0:
            return "soon"
        if total_minutes < 60:
            return f"in {total_minutes}m"
        hours = total_minutes // 60
        mins = total_minutes % 60
        if hours < 24:
            return f"in {hours}h {mins}m" if mins else f"in {hours}h"
        days = hours // 24
        leftover_h = hours % 24
        return f"in {days}d {leftover_h}h" if leftover_h else f"in {days}d"
    except Exception:
        return ""

print("try_usage_apis ready")

#------------
# Orchestrator: demo -> manual override -> cookies -> fetch -> parse
# Returns (data_dict, status_string)
#------------
def get_usage(cfg):
    if cfg.get("manual_override"):
        return {
            "session_pct": cfg.get("manual_session_pct", 0),
            "weekly_pct": cfg.get("manual_weekly_pct", 0),
            "session_reset": cfg.get("manual_session_reset", ""),
            "weekly_reset": cfg.get("manual_weekly_reset", "")
        }, "MANUAL"
    reset_diag()
    diag(f"=== refresh at {datetime.now().isoformat(timespec='seconds')} ===")
    try:
        cookies = get_claude_cookies(cfg)
    except Exception as e:
        diag(f"cookie read failed: {e}")
        return {"error": str(e)[:80]}, "AUTH"
    if not cookies:
        diag("no cookies returned")
        return {"error": "No claude.ai cookies found"}, "AUTH"
    # Strategy A: Try the JSON API endpoints first, they're more reliable
    api_result = try_usage_apis(cookies)
    if api_result is not None:
        return api_result, "OK"
    # Strategy B: Fall back to scraping the settings page HTML
    try:
        html = fetch_usage_page(cookies)
    except AuthError as e:
        return {"error": str(e)[:80]}, "AUTH"
    except ConnectionError as e:
        return {"error": str(e)[:80]}, "NETWORK"
    try:
        data = parse_usage_html(html)
        return data, "OK"
    except ParseError as e:
        return {"error": str(e)[:200]}, "PARSE"

#------------
# Section: Auth-needed dialog (Fallback 1)
#------------
class AuthNeededDialog(QDialog):
    #------------
    # Ask the user to sign in, offer a Ready button to retry
    #------------
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Claude Usage Bar - Sign in needed")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)

        msg = QLabel(
            "We couldn't read your Claude session.\n\n"
            "Please open Claude Desktop and sign in.\n"
            "If you prefer to use a browser session, pick a different "
            "cookie source in Setup."
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("Open claude.ai")
        open_btn.clicked.connect(self._open_claude)
        ready_btn = QPushButton("Ready - try again")
        ready_btn.setDefault(True)
        ready_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        btn_row.addWidget(open_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ready_btn)
        layout.addLayout(btn_row)

    #------------
    # Open the settings page in the default browser as a convenience
    #------------
    def _open_claude(self):
        QDesktopServices.openUrl(QUrl(USAGE_URL))

#------------
# Section: Manual entry dialog (Fallback 2)
#------------
class ManualEntryDialog(QDialog):
    #------------
    # Build the form for pasting percentages when auto-scrape fails
    #------------
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Claude Usage Bar - Enter values")
        self.setMinimumWidth(460)
        self.cfg = cfg

        layout = QVBoxLayout(self)

        msg = QLabel(
            "Auto-scraping failed. Open Claude Desktop or claude.ai "
            "and look at Settings > Usage, then enter the values you "
            "see below. They will stay static until you update them."
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        form = QFormLayout()

        self.s_pct = QSpinBox()
        self.s_pct.setRange(0, 100)
        self.s_pct.setSuffix(" %")
        self.s_pct.setValue(int(cfg.get("manual_session_pct", 0)))

        self.w_pct = QSpinBox()
        self.w_pct.setRange(0, 100)
        self.w_pct.setSuffix(" %")
        self.w_pct.setValue(int(cfg.get("manual_weekly_pct", 0)))

        self.s_reset = QLineEdit(cfg.get("manual_session_reset", ""))
        self.s_reset.setPlaceholderText("e.g. 4 hr 16 min")

        self.w_reset = QLineEdit(cfg.get("manual_weekly_reset", ""))
        self.w_reset.setPlaceholderText("e.g. Thu 3:00 PM")

        form.addRow("Current session used:", self.s_pct)
        form.addRow("Session resets in:", self.s_reset)
        form.addRow("Weekly (all models) used:", self.w_pct)
        form.addRow("Weekly resets:", self.w_reset)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    #------------
    # Return a config patch with the user's manual values
    #------------
    def get_values(self):
        return {
            "manual_override": True,
            "manual_session_pct": self.s_pct.value(),
            "manual_weekly_pct": self.w_pct.value(),
            "manual_session_reset": self.s_reset.text().strip(),
            "manual_weekly_reset": self.w_reset.text().strip()
        }

#------------
# Section: Setup dialog (simplified)
#------------
#------------
# Section: Details dialog
# Shows all usage bars the API returned, matching the claude.ai UI
#------------
class DetailsDialog(QDialog):
    #------------
    # Build the details view from the widget's most recent data.
    # When there's no data (not connected yet), show a friendly empty state
    # with a button that jumps to Setup.
    #------------
    def __init__(self, data, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Claude Usage Bar - Details")
        self.setMinimumWidth(460)
        self.setStyleSheet("""
            QLabel { color: #e6e6e6; }
            QLabel#section { color: #ffffff; font-weight: bold; font-size: 13px; }
            QLabel#dim { color: #aaaaaa; }
            QProgressBar {
                background-color: rgba(70, 70, 78, 220);
                border: none; border-radius: 4px;
                min-height: 10px; max-height: 10px;
            }
            QProgressBar::chunk {
                background-color: #d97757; border-radius: 4px;
            }
        """)

        root = QVBoxLayout(self)
        self.parent_bar = parent

        # Empty-state path: no data to show
        has_data = bool(data and (data.get("all_bars") or data.get("session_pct") is not None))
        if not has_data:
            empty = QLabel(
                "<b>Not connected yet.</b><br><br>"
                "Set up your claude.ai session first, then come back to "
                "see your full usage breakdown including session, weekly, "
                "Sonnet-only, Opus-only, Claude Design, and extra usage."
            )
            empty.setWordWrap(True)
            empty.setTextFormat(Qt.RichText)
            empty.setStyleSheet(
                "padding: 12px; background: rgba(255,255,255,0.03); "
                "border-radius: 6px;"
            )
            root.addWidget(empty)

            setup_btn = QPushButton("Open Setup")
            setup_btn.clicked.connect(self._go_to_setup)
            root.addWidget(setup_btn)

            close_btn = QPushButton("Close")
            close_btn.clicked.connect(self.accept)
            root.addWidget(close_btn)
            return

        tier = (data or {}).get("rate_limit_tier", "")
        tier_readable = tier.replace("_", " ").replace("default", "").strip().title()
        if tier_readable:
            header = QLabel(f"Plan: {tier_readable}")
            header.setObjectName("section")
            root.addWidget(header)

        all_bars = (data or {}).get("all_bars") or []
        if not all_bars:
            all_bars = [
                {
                    "label": "Current session",
                    "pct": (data or {}).get("session_pct", 0),
                    "reset": (data or {}).get("session_reset", "")
                },
                {
                    "label": "All models (weekly)",
                    "pct": (data or {}).get("weekly_pct", 0),
                    "reset": (data or {}).get("weekly_reset", "")
                }
            ]

        for bar in all_bars:
            row = QVBoxLayout()
            top = QHBoxLayout()
            label_lbl = QLabel(f"<b>{bar['label']}</b>  —  {bar['pct']}% used")
            label_lbl.setTextFormat(Qt.RichText)
            top.addWidget(label_lbl)
            top.addStretch()
            row.addLayout(top)
            pb = QProgressBar()
            pb.setMaximum(100)
            pb.setValue(int(bar.get("pct", 0)))
            pb.setTextVisible(False)
            row.addWidget(pb)
            if bar.get("reset"):
                sub = QLabel(f"Resets {bar['reset']}")
                sub.setObjectName("dim")
                row.addWidget(sub)
            wrap = QWidget()
            wrap.setLayout(row)
            root.addWidget(wrap)

        # Source note so users know where the numbers came from
        if cfg.get("manual_override"):
            source = "Manual values (set by you)"
        else:
            source = f"Live from claude.ai, updated {datetime.now().strftime('%H:%M:%S')}"
        foot = QLabel(source)
        foot.setObjectName("dim")
        root.addSpacing(8)
        root.addWidget(foot)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._on_refresh)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self.parent_bar = parent

    #------------
    # Trigger a live refresh on the parent bar, then rebuild this dialog
    #------------
    def _on_refresh(self):
        if self.parent_bar:
            self.parent_bar.refresh()
            self.accept()
            QTimer.singleShot(
                500,
                lambda: DetailsDialog(
                    self.parent_bar.latest_data,
                    self.parent_bar.cfg,
                    self.parent_bar
                ).exec()
            )

    #------------
    # Close this dialog and open Setup on the parent bar
    #------------
    def _go_to_setup(self):
        self.accept()
        if self.parent_bar:
            self.parent_bar._open_setup_dialog()


#------------
# Section: Setup dialog
#------------
class SetupDialog(QDialog):
    #------------
    # Setup flow: pick browser at top, show only that browser's steps,
    # then paste cookie + test. Details behind "?" info button.
    #------------

    # Per-browser short steps. Kept under 3 lines each. The "?" button shows a
    # longer explanation for users who want it.
    BROWSER_STEPS = {
        "chrome": (
            "F12 (or right-click and Inspect) → <b>Network</b> tab → Ctrl+R to reload → find any line named 'Usage' "
            "→ scroll to <b>Request Headers</b> → copy the <b>cookie:</b> value."
        ),
        "edge": (
            "F12 (or right-click and Inspect) → <b>Network</b> tab → Ctrl+R to reload → find any line named 'Usage' "
            "→ scroll to <b>Request Headers</b> → copy the <b>cookie:</b> value."
        ),
        "brave": (
            "F12 (or right-click and Inspect) → <b>Network</b> tab → Ctrl+R to reload → find any line named 'Usage' "
            "→ scroll to <b>Request Headers</b> → copy the <b>cookie:</b> value."
        ),
        "firefox": (
            "F12 (or right-click and Inspect) → <b>Network</b> tab → Ctrl+R to reload → find any line named 'Usage' "
            "→ in the right panel click <b>Headers</b> → scroll to "
            "<b>Request Headers</b> → copy the <b>Cookie</b> value."
        ),
        "safari": (
            "Enable Develop menu first (Safari → Settings → Advanced → "
            "Show Develop menu). Then Option+Cmd+I → <b>Network</b> tab "
            "→ Cmd+R to reload → find any line named 'Usage' → copy the <b>Cookie</b> "
            "request header."
        ),
    }

    BROWSER_DETAIL = (
        "<b>Why we need this:</b> Claude's session is stored in an "
        "<code>HttpOnly</code> cookie named <code>sessionKey</code>. Browsers "
        "deliberately hide HttpOnly cookies from JavaScript, so we can't auto-"
        "grab them. The Network tab is the one place they're visible.<br><br>"
        "<b>What to look for:</b> after you reload, every request to claude.ai "
        "shows a <code>cookie:</code> header. Its value is a long string like "
        "<code>cf_clearance=...; sessionKey=sk-ant-sid02-...; _cfuvid=...</code>. "
        "Copy the whole thing.<br><br>"
        "<b>Security note:</b> this cookie grants access to your Claude account. "
        "The widget stores it locally in <code>%APPDATA%\\ClaudeUsageBar</code> "
        "and sends it only to <code>claude.ai</code>. Treat it like a password."
    )

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Claude Usage Bar - Setup")
        self.setMinimumWidth(480)
        self.cfg = dict(cfg)
        self.parent_widget = parent

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # --- Browser picker at top ---
        picker_row = QHBoxLayout()
        picker_row.addWidget(QLabel("Your browser:"))
        self.setup_browser_combo = QComboBox()
        self.setup_browser_combo.addItems([
            "Chrome", "Edge", "Firefox", "Brave", "Safari"
        ])
        # Restore previous pick if any
        saved = cfg.get("setup_browser", "Chrome")
        idx = self.setup_browser_combo.findText(saved)
        if idx >= 0:
            self.setup_browser_combo.setCurrentIndex(idx)
        self.setup_browser_combo.currentTextChanged.connect(self._update_steps)
        picker_row.addWidget(self.setup_browser_combo, 1)
        layout.addLayout(picker_row)

        # --- Steps label with inline info button ---
        steps_row = QHBoxLayout()
        self.steps_label = QLabel("")
        self.steps_label.setWordWrap(True)
        self.steps_label.setTextFormat(Qt.RichText)
        self.steps_label.setStyleSheet(
            "padding: 10px; background: rgba(255,255,255,0.03); "
            "border-radius: 6px; font-size: 11px;"
        )
        steps_row.addWidget(self.steps_label, 1)

        info_btn = QPushButton("?")
        info_btn.setFixedWidth(28)
        info_btn.setToolTip("More details")
        info_btn.clicked.connect(self._show_detail)
        steps_row.addWidget(info_btn)
        layout.addLayout(steps_row)

        # --- Shortcut button ---
        open_btn = QPushButton("Open Claude usage page")
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(USAGE_URL))
        )
        layout.addWidget(open_btn)

        # --- Paste field ---
        form = QFormLayout()
        self.cookie_string_field = QLineEdit(cfg.get("cookie_string", ""))
        self.cookie_string_field.setPlaceholderText(
            "Paste the long cookie value here (must contain sessionKey=)"
        )
        self.cookie_string_field.setEchoMode(QLineEdit.Password)
        form.addRow("Cookie:", self.cookie_string_field)
        self.show_cookie = QCheckBox("Show")
        self.show_cookie.toggled.connect(self._toggle_show_cookie)
        form.addRow("", self.show_cookie)
        layout.addLayout(form)

        # --- Test + result ---
        self.test_btn = QPushButton("Test connection")
        self.test_btn.clicked.connect(self._test)
        layout.addWidget(self.test_btn)

        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        layout.addWidget(self.test_result)

        # --- Collapsed advanced options ---
        adv_toggle = QPushButton("▸ Advanced options")
        adv_toggle.setFlat(True)
        adv_toggle.setStyleSheet(
            "QPushButton { text-align: left; color: #aaa; font-size: 10px; }"
        )
        self.adv_panel = QWidget()
        self.adv_panel.setVisible(False)
        adv_toggle.clicked.connect(
            lambda: (
                self.adv_panel.setVisible(not self.adv_panel.isVisible()),
                adv_toggle.setText(
                    "▾ Advanced options" if self.adv_panel.isVisible()
                    else "▸ Advanced options"
                )
            )
        )
        layout.addWidget(adv_toggle)

        adv_layout = QFormLayout(self.adv_panel)
        self.browser_combo = QComboBox()
        self.browser_combo.addItems([
            "auto", "chrome", "edge", "firefox", "brave", "claude_desktop"
        ])
        current = cfg.get("browser", "chrome")
        idx = self.browser_combo.findText(current)
        if idx >= 0:
            self.browser_combo.setCurrentIndex(idx)

        self.manual_check = QCheckBox("Use manual values instead")
        self.manual_check.setChecked(bool(cfg.get("manual_override")))

        self.follow_check = QCheckBox(
            "Show only when Claude Desktop is running"
        )
        self.follow_check.setChecked(bool(cfg.get("follow_claude_desktop")))

        adv_layout.addRow("Auto-sniff browser:", self.browser_combo)
        adv_layout.addRow(self.follow_check)
        adv_layout.addRow(self.manual_check)
        manual_btn = QPushButton("Edit manual values…")
        manual_btn.clicked.connect(self._edit_manual)
        adv_layout.addRow(manual_btn)
        layout.addWidget(self.adv_panel)

        # --- Save/Cancel ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_steps(self.setup_browser_combo.currentText())

    #------------
    # Swap in the right step text for the chosen browser
    #------------
    def _update_steps(self, browser_name):
        key = (browser_name or "").lower()
        steps = self.BROWSER_STEPS.get(key, self.BROWSER_STEPS["chrome"])
        self.steps_label.setText(steps)

    #------------
    # Show the longer "why + what + security" explainer
    #------------
    def _show_detail(self):
        box = QMessageBox(self)
        box.setWindowTitle("What is the cookie and why is this needed?")
        box.setTextFormat(Qt.RichText)
        box.setText(self.BROWSER_DETAIL)
        box.setIcon(QMessageBox.Information)
        box.exec()

    #------------
    # Toggle masked display of the cookie field
    #------------
    def _toggle_show_cookie(self, checked):
        self.cookie_string_field.setEchoMode(
            QLineEdit.Normal if checked else QLineEdit.Password
        )

    #------------
    # Run a one-shot fetch so the user can see if setup works
    #------------
    def _test(self):
        self.test_result.setText("Testing…")
        QApplication.processEvents()
        test_cfg = dict(self.cfg)
        test_cfg["demo_mode"] = False
        test_cfg["browser"] = self.browser_combo.currentText()
        test_cfg["cookie_string"] = self.cookie_string_field.text().strip()
        test_cfg["manual_override"] = self.manual_check.isChecked()
        data, status = get_usage(test_cfg)
        if status in ("OK", "MANUAL"):
            self.test_result.setText(
                f"✓ Connected: Session {data.get('session_pct', '?')}%, "
                f"Week {data.get('weekly_pct', '?')}%"
            )
            self.test_result.setStyleSheet("color: #8bd17c;")
        else:
            err = data.get("error", "unknown")
            self.test_result.setText(f"✗ {status}: {err}")
            self.test_result.setStyleSheet("color: #ff7676;")

    #------------
    # Open the manual values dialog
    #------------
    def _edit_manual(self):
        dlg = ManualEntryDialog(self.cfg, self)
        if dlg.exec():
            self.cfg.update(dlg.get_values())
            self.manual_check.setChecked(True)

    #------------
    # Return the final config shaped from form + any manual edits
    #------------
    def get_config(self):
        out = dict(self.cfg)
        out["browser"] = self.browser_combo.currentText()
        out["cookie_string"] = self.cookie_string_field.text().strip()
        out["manual_override"] = self.manual_check.isChecked()
        out["follow_claude_desktop"] = self.follow_check.isChecked()
        out["setup_browser"] = self.setup_browser_combo.currentText()
        return out

#------------
# Section: Main bar widget
#------------
class UsageBar(QWidget):
    #------------
    # Build the bar, position it, start the poll timer
    #------------
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.drag_pos = None
        self.current_status = "starting"
        self.latest_data = None
        self._build_ui()
        self._position_at_bottom()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_SECONDS * 1000)
        QTimer.singleShot(400, self.refresh)

        # Follow Claude Desktop: poll every 3 s and show/hide accordingly
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._check_claude_follow)
        self._follow_timer.start(3000)

        # First-run: if there's no config at all, push the user into Setup
        # immediately with a welcome explanation
        if self._is_first_run():
            QTimer.singleShot(800, self._first_run_welcome)

    #------------
    # Returns True if this looks like a fresh install (no creds set yet)
    #------------
    def _is_first_run(self):
        return (
            not self.cfg.get("cookie_string")
            and not self.cfg.get("session_key")
            and not self.cfg.get("manual_override")
            and not self.cfg.get("demo_mode")
        )

    #------------
    # First-run welcome: one friendly popup, then Setup
    #------------
    def _first_run_welcome(self):
        box = QMessageBox(self)
        box.setWindowTitle("Welcome to Claude Usage Bar")
        box.setIcon(QMessageBox.Information)
        box.setText(
            "<b>Almost there!</b><br><br>"
            "To show your real usage, the bar needs your claude.ai "
            "session cookie. You only paste it once.<br><br>"
            "Click <b>Setup</b> on the next screen for a quick 30-second "
            "walkthrough, or skip with <b>Manual values</b> to enter "
            "numbers by hand."
        )
        setup_btn = box.addButton("Setup", QMessageBox.AcceptRole)
        manual_btn = box.addButton("Manual values", QMessageBox.ActionRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == setup_btn:
            self._open_setup_dialog()
        elif clicked == manual_btn:
            self._open_manual_dialog()

    #------------
    # Show/hide the bar based on whether Claude Desktop is running.
    # Only active when follow_claude_desktop is enabled in config.
    #------------
    def _check_claude_follow(self):
        if not self.cfg.get("follow_claude_desktop"):
            return
        running = is_claude_desktop_running()
        if running and not self.isVisible():
            self.show()
            self.refresh()
        elif not running and self.isVisible():
            self.hide()

    #------------
    # Construct the frameless always-on-top layout
    #------------
    def _build_ui(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.root = QWidget(self)
        self.root.setObjectName("root")
        self.root.setStyleSheet("""
            QWidget#root {
                background-color: rgba(28, 28, 32, 225);
                border-radius: 10px;
            }
            QLabel {
                color: #e6e6e6;
                font-family: 'Segoe UI';
                font-size: 11px;
                padding: 0 4px;
            }
            QLabel#status_warn {
                color: #ffb86c;
                font-size: 11px;
            }
            QLabel#status_ok {
                color: #8bd17c;
                font-size: 10px;
            }
            QProgressBar {
                background-color: rgba(70, 70, 78, 220);
                border: none;
                border-radius: 4px;
                text-align: center;
                min-height: 10px;
                max-height: 10px;
            }
            QProgressBar::chunk {
                background-color: #d97757;
                border-radius: 4px;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.root)

        row = QHBoxLayout(self.root)
        row.setContentsMargins(14, 6, 14, 6)
        row.setSpacing(10)

        self.session_label = QLabel("Session …")
        self.session_bar = QProgressBar()
        self.session_bar.setMaximum(100)
        self.session_bar.setTextVisible(False)
        self.session_bar.setFixedWidth(130)

        self.weekly_label = QLabel("Week …")
        self.weekly_bar = QProgressBar()
        self.weekly_bar.setMaximum(100)
        self.weekly_bar.setTextVisible(False)
        self.weekly_bar.setFixedWidth(130)

        self.status_label = QLabel("")
        self.status_label.setObjectName("status_ok")

        row.addWidget(self.session_label)
        row.addWidget(self.session_bar)
        row.addSpacing(6)
        row.addWidget(self.weekly_label)
        row.addWidget(self.weekly_bar)
        row.addSpacing(6)
        row.addWidget(self.status_label)

        self.resize(580, 34)

    #------------
    # Pin the bar to bottom-center of the primary display
    #------------
    def _position_at_bottom(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + screen.height() - self.height() - 6
        self.move(x, y)

    #------------
    # Run a fetch and route to the right UI state
    #------------
    def refresh(self):
        data, status = get_usage(self.cfg)
        self.current_status = status
        if status in ("OK", "MANUAL"):
            self._display(data, status)
        elif status == "AUTH":
            self._display_error(data.get("error", ""), "Click to set up")
        elif status == "PARSE":
            self._display_error(data.get("error", ""), "Parse failed - click")
        else:
            self._display_error(data.get("error", ""), "Offline")

    #------------
    # Render good data onto the bars
    #------------
    def _display(self, data, status):
        self.latest_data = data
        try:
            s = max(0, min(100, int(float(data.get("session_pct", 0)))))
            w = max(0, min(100, int(float(data.get("weekly_pct", 0)))))
        except (ValueError, TypeError):
            s, w = 0, 0
        self.session_bar.setValue(s)
        self.weekly_bar.setValue(w)
        self.session_label.setText(f"Session {s}%")
        self.weekly_label.setText(f"Week {w}%")
        s_reset = data.get("session_reset", "") or ""
        w_reset = data.get("weekly_reset", "") or ""
        for w in (self.session_label, self.session_bar):
            w.setToolTip(s_reset)
        for w in (self.weekly_label, self.weekly_bar):
            w.setToolTip(w_reset)
        self.status_label.setObjectName("status_ok")
        self.status_label.setStyleSheet("")
        if status == "MANUAL":
            self.status_label.setText("manual")
            tip_prefix = "MANUAL VALUES\n"
        else:
            self.status_label.setText(datetime.now().strftime("%H:%M"))
            tip_prefix = ""
        self.setToolTip(
            f"{tip_prefix}Session resets: {data.get('session_reset', '')}\n"
            f"Weekly resets: {data.get('weekly_reset', '')}\n\n"
            f"Click the time for details. Right-click for menu."
        )

    #------------
    # Render an error state into the status label
    #------------
    def _display_error(self, err, label):
        self.session_label.setText("Session —")
        self.weekly_label.setText("Week —")
        self.session_bar.setValue(0)
        self.weekly_bar.setValue(0)
        for w in (self.session_label, self.session_bar,
                  self.weekly_label, self.weekly_bar):
            w.setToolTip("")
        self.status_label.setObjectName("status_warn")
        self.status_label.setStyleSheet("color: #ffb86c;")
        self.status_label.setText(label)
        self.setToolTip(f"{label}\n{err}\n\nRight-click for options.")

    #------------
    # Left-click routing:
    #   session label/bar  → pop reset-time bubble
    #   week label/bar     → pop reset-time bubble
    #   status label       → Setup / Manual / Details depending on state
    #   anywhere else      → drag
    #------------
    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        gpos = event.globalPosition().toPoint()

        def hit(w):
            return w.rect().contains(w.mapFromGlobal(gpos))

        # Session/week LABEL only: swap text with reset time while held.
        # The progress bar is drag-only — no text swap there.
        if hit(self.session_label):
            reset = self.session_label.toolTip()
            if reset:
                self._held_label = self.session_label
                self._held_original = self.session_label.text()
                self.session_label.setText(reset)
        elif hit(self.weekly_label):
            reset = self.weekly_label.toolTip()
            if reset:
                self._held_label = self.weekly_label
                self._held_original = self.weekly_label.text()
                self.weekly_label.setText(reset)
        else:
            on_status = self.status_label.rect().contains(
                self.status_label.mapFromGlobal(gpos)
            )
            if on_status and self.current_status == "AUTH":
                self._open_setup_dialog()
                return
            if on_status and self.current_status == "PARSE":
                self._open_manual_dialog()
                return
            if on_status and self.current_status in ("OK", "MANUAL"):
                self._open_details_dialog()
                return

        self.drag_pos = gpos - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_pos and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if getattr(self, "_held_label", None):
            self._held_label.setText(self._held_original)
            self._held_label = None
            self._held_original = None
        self.drag_pos = None

    #------------
    # Right-click menu
    #------------
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_details = menu.addAction("Show details…")
        act_refresh = menu.addAction("Refresh now")
        menu.addSeparator()
        act_setup = menu.addAction("Setup…")
        act_manual = menu.addAction("Enter values manually…")
        act_reset_pos = menu.addAction("Reset position")
        menu.addSeparator()
        act_view_diag = menu.addAction("View diagnostic log")
        act_open_diag = menu.addAction("Open config folder")
        menu.addSeparator()
        act_quit = menu.addAction("Quit")
        chosen = menu.exec(event.globalPos())
        if chosen == act_details:
            self._open_details_dialog()
        elif chosen == act_refresh:
            self.refresh()
        elif chosen == act_setup:
            self._open_setup_dialog()
        elif chosen == act_manual:
            self._open_manual_dialog()
        elif chosen == act_reset_pos:
            self._position_at_bottom()
        elif chosen == act_view_diag:
            if DIAG_LOG_PATH.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(DIAG_LOG_PATH)))
        elif chosen == act_open_diag:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(APP_DIR)))
        elif chosen == act_quit:
            QApplication.quit()

    #------------
    # Launch the details dialog positioned just above the bar
    #------------
    def _open_details_dialog(self):
        dlg = DetailsDialog(self.latest_data or {}, self.cfg, self)
        dlg.adjustSize()
        bar_geo = self.frameGeometry()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        dlg_w = max(dlg.sizeHint().width(), 460)
        dlg_h = dlg.sizeHint().height()
        x = bar_geo.x() + (bar_geo.width() - dlg_w) // 2
        y = bar_geo.y() - dlg_h - 8
        # Clamp horizontally; if it won't fit above, put below instead
        x = max(screen.x(), min(x, screen.right() - dlg_w))
        if y < screen.y():
            y = bar_geo.bottom() + 8
        dlg.move(x, y)
        dlg.exec()

    #------------
    # Launch the auth-needed dialog, retry on accept
    #------------
    def _open_auth_dialog(self):
        dlg = AuthNeededDialog(self)
        if dlg.exec():
            self.refresh()

    #------------
    # Launch the manual entry dialog, save values on accept
    #------------
    def _open_manual_dialog(self):
        dlg = ManualEntryDialog(self.cfg, self)
        if dlg.exec():
            self.cfg.update(dlg.get_values())
            save_config(self.cfg)
            self.refresh()

    #------------
    # Launch the main setup dialog
    #------------
    def _open_setup_dialog(self):
        dlg = SetupDialog(self.cfg, self)
        if dlg.exec():
            self.cfg = dlg.get_config()
            save_config(self.cfg)
            self.refresh()

#------------
# Section: Entry point
#------------

#------------
# App bootstrap
#------------
def main():
    # Prevent multiple instances via a named Windows mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "ClaudeUsageBar_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)

    # --startup is passed from the Windows startup shortcut.
    # In that mode (or when follow_claude_desktop is on), start hidden if
    # Claude Desktop isn't already running; the follow-mode timer will
    # auto-show the bar once Claude Desktop launches.
    is_startup = "--startup" in sys.argv

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setWindowIcon(make_app_icon())
    bar = UsageBar()
    bar.show()
    if not is_claude_desktop_running() and (is_startup or bar.cfg.get("follow_claude_desktop")):
        bar.hide()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
