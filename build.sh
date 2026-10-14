#!/usr/bin/env bash
# Build dist/SnapAsk.app and dist/SnapAsk.dmg (to share) from scratch:
# clean venv -> dependencies -> icon -> PyInstaller -> signature -> disk image.
#
#   ./build.sh                     ad-hoc signed; friends click "Open Anyway" once
#
# With an Apple Developer ID (paid Apple Developer account), friends can just
# double-click it, and macOS permissions survive your updates:
#
#   SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#   NOTARY_PROFILE=snapask ./build.sh
#
# NOTARY_PROFILE is a notarytool keychain profile, created once with:
#   xcrun notarytool store-credentials snapask --apple-id you@example.com --team-id TEAMID
#
# Set PYTHON=/path/to/python3 to pick the interpreter (3.10+; python.org builds work best).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV=.build-venv
APP=dist/SnapAsk.app
DMG=dist/SnapAsk.dmg

if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "error: $PYTHON is $("$PYTHON" -V 2>&1); SnapAsk needs Python 3.10+." >&2
    echo "       Install one from python.org, then: PYTHON=python3.13 ./build.sh" >&2
    exit 1
fi

echo "==> Fresh build environment ($("$PYTHON" -V))"
rm -rf "$VENV" build dist
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt "pyinstaller==6.22.3"

if [ ! -f assets/SnapAsk.icns ]; then
    echo "==> Drawing the icon"
    "$VENV/bin/python" tools/make_icon.py
fi

echo "==> Building SnapAsk.app"
SIGN_IDENTITY="${SIGN_IDENTITY:-}" "$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN SnapAsk.spec
rm -rf dist/SnapAsk build        # intermediate folders; the .app is self-contained

if [ -n "${SIGN_IDENTITY:-}" ]; then
    echo "==> Signed with: $SIGN_IDENTITY"
    codesign --verify --deep --strict "$APP"
    if [ -n "${NOTARY_PROFILE:-}" ]; then
        echo "==> Notarizing (takes a few minutes)"
        ditto -c -k --keepParent "$APP" dist/notarize.zip
        xcrun notarytool submit dist/notarize.zip --keychain-profile "$NOTARY_PROFILE" --wait
        rm dist/notarize.zip
        xcrun stapler staple "$APP"
    fi
else
    echo "==> Ad-hoc signing"
    codesign --force --deep --sign - "$APP"
    codesign --verify --deep --strict "$APP"
fi

echo "==> Making SnapAsk.dmg to share"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "assets/How to open SnapAsk.txt" "$STAGE/"
hdiutil create -quiet -volname SnapAsk -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
if [ -n "${SIGN_IDENTITY:-}" ]; then
    codesign --sign "$SIGN_IDENTITY" --timestamp "$DMG"
fi

echo
echo "Built $(pwd)/$APP ($(du -sh "$APP" | cut -f1))"
echo "Share $(pwd)/$DMG ($(du -sh "$DMG" | cut -f1)) with friends; it includes install steps."
