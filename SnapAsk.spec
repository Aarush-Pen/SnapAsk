# PyInstaller recipe for dist/SnapAsk.app. Run through build.sh, not directly.
# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

sys.path.insert(0, SPECPATH)
from snapask import APP_NAME, BUNDLE_ID, __version__  # noqa: E402

SIGN_IDENTITY = os.environ.get("SIGN_IDENTITY") or None

# Imports PyInstaller can't see by reading the code:
#  - keyring finds backends through entry points; SnapAsk imports the macOS one
#    directly, but its ctypes helper module is pulled in lazily.
#  - google-genai imports parts of itself on first use.
hiddenimports = (
    ["keyring.backends.macOS", "keyring.backends.macOS.api"]
    + collect_submodules("google.genai", filter=lambda name: ".tests" not in name)
)
# Some SDKs read their own version from package metadata at import time.
datas = []
for dist in ("anthropic", "openai", "google-genai", "keyring", "httpx", "httpx2"):
    datas += copy_metadata(dist)

a = Analysis(
    ["main.py"],
    pathex=[SPECPATH],
    hiddenimports=hiddenimports,
    datas=datas,
    excludes=["tkinter", "PyObjCTest", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    target_arch="arm64",            # Apple Silicon; see README for universal2
    # With a Developer ID (build.sh's SIGN_IDENTITY), PyInstaller signs every
    # binary with it and the hardened runtime, as notarization requires.
    # Without one, build.sh ad-hoc signs the finished bundle instead.
    codesign_identity=SIGN_IDENTITY,
    entitlements_file="assets/entitlements.plist" if SIGN_IDENTITY else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon="assets/SnapAsk.icns",
    bundle_identifier=BUNDLE_ID,
    version=__version__,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,                     # menu bar app: no Dock icon
        "NSHighResolutionCapable": True,
        "NSScreenCaptureUsageDescription":
            "SnapAsk captures only the area you drag over and sends it to the AI provider you chose.",
        "NSAccessibilityUsageDescription":
            "SnapAsk listens for its keyboard shortcuts (like ⌃A) while you work in other apps.",
    },
)
