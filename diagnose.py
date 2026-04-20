# diagnose.py
# Standalone diagnostic for Claude Desktop cookie reading.
# Run directly with python, no build step needed:
#   python diagnose.py
# Copy-paste the whole output back to Claude.

import os
import sys
import json
import base64
import sqlite3
import tempfile
import shutil
import traceback
from pathlib import Path

APPDATA = os.environ.get("APPDATA", "")
CLAUDE_DIR = Path(APPDATA) / "Claude"

print("=" * 60)
print("Claude Usage Bar - Diagnostic")
print("=" * 60)
print(f"APPDATA: {APPDATA}")
print(f"Looking under: {CLAUDE_DIR}")
print()

# Step 1: List Claude Desktop's folder structure
print("--- Step 1: Folder contents ---")
if not CLAUDE_DIR.exists():
    print(f"ERROR: {CLAUDE_DIR} does not exist")
    sys.exit(1)

for root, dirs, files in os.walk(CLAUDE_DIR):
    depth = root.replace(str(CLAUDE_DIR), "").count(os.sep)
    if depth > 3:
        continue
    indent = "  " * depth
    rel = os.path.relpath(root, CLAUDE_DIR)
    print(f"{indent}[{rel}]")
    for f in files:
        try:
            size = os.path.getsize(os.path.join(root, f))
            print(f"{indent}  {f}  ({size} bytes)")
        except Exception as e:
            print(f"{indent}  {f}  (size error: {e})")
print()

# Step 2: Try to find any file named Cookies
print("--- Step 2: Search for Cookies files ---")
for root, dirs, files in os.walk(CLAUDE_DIR):
    for f in files:
        if "cookie" in f.lower() or f.lower().startswith("cookies"):
            full = os.path.join(root, f)
            try:
                size = os.path.getsize(full)
            except Exception:
                size = "?"
            print(f"  {full}  ({size} bytes)")
print()

# Step 3: Check Local State
print("--- Step 3: Local State ---")
ls = CLAUDE_DIR / "Local State"
print(f"Path: {ls}")
print(f"Exists: {ls.exists()}")
if ls.exists():
    try:
        with open(ls, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"Top-level keys: {list(data.keys())}")
        if "os_crypt" in data:
            oc = data["os_crypt"]
            print(f"os_crypt keys: {list(oc.keys())}")
            ek = oc.get("encrypted_key", "")
            if ek:
                raw = base64.b64decode(ek)
                print(f"encrypted_key length: {len(ek)} b64 chars, {len(raw)} raw bytes")
                print(f"encrypted_key prefix: {raw[:5]!r}")
    except Exception as e:
        print(f"Local State read error: {e}")
print()

# Step 4: Try to copy a Cookies file three different ways
print("--- Step 4: File copy test ---")
cookie_path = None
for candidate in (
    CLAUDE_DIR / "Network" / "Cookies",
    CLAUDE_DIR / "Default" / "Network" / "Cookies",
    CLAUDE_DIR / "Default" / "Cookies",
    CLAUDE_DIR / "Cookies",
):
    if candidate.exists():
        cookie_path = candidate
        break
if not cookie_path:
    print("No Cookies file found")
    sys.exit(0)

size_on_disk = os.path.getsize(cookie_path)
print(f"Source: {cookie_path}")
print(f"Source size: {size_on_disk} bytes")

# Method A: shutil.copy2
try:
    tmp_a = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp_a.close()
    shutil.copy2(cookie_path, tmp_a.name)
    print(f"Method A (shutil.copy2): {os.path.getsize(tmp_a.name)} bytes copied")
    os.unlink(tmp_a.name)
except Exception as e:
    print(f"Method A (shutil.copy2) FAILED: {e}")

# Method B: raw open + read
try:
    with open(cookie_path, "rb") as f:
        data = f.read()
    print(f"Method B (raw open): read {len(data)} bytes")
except Exception as e:
    print(f"Method B (raw open) FAILED: {e}")

# Method C: win32file with share flags
try:
    import win32file
    import win32con
    h = win32file.CreateFile(
        str(cookie_path),
        win32con.GENERIC_READ,
        win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
        None,
        win32con.OPEN_EXISTING,
        win32con.FILE_ATTRIBUTE_NORMAL,
        None,
    )
    try:
        hr, data = win32file.ReadFile(h, size_on_disk)
        print(f"Method C (win32file share flags): read {len(data)} bytes, hr={hr}")
        if len(data) > 0:
            tmp_c = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
            tmp_c.write(bytes(data))
            tmp_c.close()
            try:
                conn = sqlite3.connect(tmp_c.name)
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cur.fetchall()]
                print(f"Method C tables: {tables}")
                conn.close()
            except Exception as e:
                print(f"Method C sqlite error: {e}")
            os.unlink(tmp_c.name)
    finally:
        win32file.CloseHandle(h)
except Exception as e:
    print(f"Method C FAILED: {e}")
    traceback.print_exc()

# Method D: VSS shadow copy via shadowcopy lib
try:
    from shadowcopy import shadow_copy
    tmp_d = tempfile.mktemp(suffix=".db")
    shadow_copy(str(cookie_path), tmp_d)
    size_d = os.path.getsize(tmp_d) if os.path.exists(tmp_d) else 0
    print(f"Method D (VSS shadow copy): {size_d} bytes")
    if size_d > 0:
        conn = sqlite3.connect(tmp_d)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print(f"Method D tables: {tables}")
        conn.close()
    if os.path.exists(tmp_d):
        os.unlink(tmp_d)
except Exception as e:
    print(f"Method D FAILED: {e}")

print()
print("=" * 60)
print("Done. Copy all output above and paste it back.")
print("=" * 60)
