#!/usr/bin/env bash
# Build PDF2LIST.app for the current Mac architecture (Intel or Apple Silicon).
set -euo pipefail

cd "$(dirname "$0")/.."
ARCH="$(uname -m)"
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.11 python3.12 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "Python 3.10-3.12 non trovato. Installa Python da python.org o usa PYTHON=..."
  exit 1
fi
VENV=".venv-build"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3.11/3.12 non trovato. Installa Python da python.org o usa PYTHON=..."
  exit 1
fi

if [ ! -d "$VENV" ]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r requirements.txt pyinstaller

# Finder may recreate .DS_Store while the directory is being removed.
for path in build dist release; do
  for _ in 1 2 3; do
    rm -rf "$path" 2>/dev/null || true
    [ -e "$path" ] || break
    sleep 0.2
  done
  rm -rf "$path"
done
"$VENV/bin/python" -m PyInstaller --clean --noconfirm packaging/pdf2list.spec

# Ad-hoc signature: avoids some Gatekeeper issues (the app is not notarized).
if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "dist/PDF2LIST.app" || true
fi

mkdir -p release
ditto -c -k --keepParent "dist/PDF2LIST.app" "release/PDF2LIST-macOS-${ARCH}.zip"

if command -v hdiutil >/dev/null 2>&1; then
  STAGE="$(mktemp -d)"
  cp -R "dist/PDF2LIST.app" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "PDF2LIST" -srcfolder "$STAGE" -ov -format UDZO \
    "release/PDF2LIST-macOS-${ARCH}.dmg" >/dev/null
  rm -rf "$STAGE"
fi

echo
echo "Creato:"
ls -lh release
echo
echo "Nota: la build è per ${ARCH}. Per l'altra architettura usa un Mac corrispondente"
echo "oppure il workflow GitHub Actions (.github/workflows/build.yml)."
echo "Al primo avvio: clic destro sull'app → Apri (app non notarizzata)."
