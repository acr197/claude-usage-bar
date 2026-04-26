# claude_usage_bar.spec
# PyInstaller build spec for Claude Usage Bar
# Run with: pyinstaller claude_usage_bar.spec

from PyInstaller.building.build_main import Analysis, PYZ, EXE

a = Analysis(
    ['claude_usage_bar.py'],
    pathex=[],
    binaries=[],
    datas=[],
    # These hidden imports are required because PyInstaller can't see
    # them through dynamic loading in browser_cookie3 and pycryptodomex
    hiddenimports=[
        'browser_cookie3',
        'Cryptodome',
        'Cryptodome.Cipher',
        'Cryptodome.Cipher.AES',
        'Cryptodome.Util',
        'Cryptodome.Util.Padding',
        'win32crypt',
        'win32api',
        'win32con',
        'win32file',
        'pywintypes',
        'lz4.block',
        'lz4.frame',
        'curl_cffi',
        'curl_cffi.requests',
        'curl_cffi.const',
        'curl_cffi.curl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim heavy unused PySide6 modules to keep the exe smaller
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DExtras',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DRender',
        'PySide6.QtBluetooth',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtDesigner',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtOpenGL',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtPositioning',
        'PySide6.QtPrintSupport',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuickControls2',
        'PySide6.QtQuickWidgets',
        'PySide6.QtRemoteObjects',
        'PySide6.QtSensors',
        'PySide6.QtSerialBus',
        'PySide6.QtSerialPort',
        'PySide6.QtSpatialAudio',
        'PySide6.QtSql',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'PySide6.QtTest',
        'PySide6.QtTextToSpeech',
        'PySide6.QtWebChannel',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebSockets',
        'PySide6.QtXml',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ClaudeUsageBar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # console=False hides the terminal window when launched
    console=False,
    disable_windowed_traceback=False,
    # Run make_icon.py once before building to generate this file
    icon='claude_usage_bar.ico',
    version=None,
)
