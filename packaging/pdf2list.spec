# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PDF2LIST (macOS one-dir .app and Windows one-dir)."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
APP_NAME = "PDF2LIST"
VERSION = "1.1.0"

datas = [(str(ROOT / "pdf2list" / "resources"), "pdf2list/resources")]
binaries = []
hiddenimports = []

for pkg in ("rapidocr_onnxruntime", "onnxruntime"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # pragma: no cover - build-time diagnostics
        print(f"warning: collect_all({pkg}) failed: {exc}")

hiddenimports += collect_submodules("keyring.backends")
hiddenimports += [
    "onnxruntime.capi._pybind_state",
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
]

excludes = [
    "tkinter",
    "matplotlib",
    "scipy",
    "pandas",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtMultimedia",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
]

icon = ROOT / "pdf2list" / "resources" / "icons" / (
    "icon.icns" if sys.platform == "darwin" else "icon.ico"
)

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon) if icon.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(icon) if icon.is_file() else None,
        bundle_identifier="it.pdf2list.app",
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": "it.pdf2list.app",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundlePackageType": "APPL",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "PDF2LIST",
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "PDF document",
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                    "LSItemContentTypes": ["com.adobe.pdf"],
                }
            ],
        },
    )
