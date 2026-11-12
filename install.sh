#!/bin/bash
# Torch Bench installer for macOS. Safe to run again: it updates the program and keeps your settings.
#
#   In Terminal, paste this one line:
#     curl -fsSL https://raw.githubusercontent.com/mathrulestheworld/torch-bench/main/install.sh | bash
#   (From a copy of the repo, `bash install.sh` does the same without downloading.)
#
# What it does (no sudo, no pip, nothing installed on Torch):
#   ~/torchbench/program/               the program (replaced on every install)
#   ~/torchbench/bin/torchbench         the `torchbench` command (added to your PATH)
#   /Applications/Torch Bench.app       a double-clickable launcher (~/Applications if /Applications is not writable)
#   ~/torchbench/config.json, logs/, cache/   your settings and records (never touched by the installer)
set -euo pipefail

REPO="${TORCHBENCH_REPO:-mathrulestheworld/torch-bench}"
BRANCH="${TORCHBENCH_BRANCH:-main}"
HOME_DIR="${TORCHBENCH_HOME:-$HOME/torchbench}"
PROGRAM="$HOME_DIR/program"
BIN="$HOME_DIR/bin"

say() { printf '%s\n' "$*"; }
fail() { printf 'Torch Bench was not installed: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || say "Note: Torch Bench is built for macOS. Continuing anyway."

# 1. Python 3 comes with Apple's Command Line Tools.
PYTHON=/usr/bin/python3
if [ "$(uname -s)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
  say "Torch Bench needs Apple's Command Line Tools (they include Python)."
  say "A window is opening to install them. When it finishes, run this installer again."
  xcode-select --install >/dev/null 2>&1 || true
  exit 1
fi
[ -x "$PYTHON" ] || PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || fail "Python 3 was not found."
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || fail "Python 3.9 or newer is needed; this Mac has $("$PYTHON" --version 2>&1)."

# 2. Find the program files: next to this script, or download them.
SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [ -d "$HERE/app/torchbench" ] && SRC="$HERE"
fi
TMP=""
if [ -z "$SRC" ]; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  say "Downloading Torch Bench from github.com/$REPO"
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" -o "$TMP/src.tar.gz" \
    || fail "could not download from github.com/$REPO (is the repository public and the branch '$BRANCH'?)"
  tar -xzf "$TMP/src.tar.gz" -C "$TMP"
  SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
  [ -d "$SRC/app/torchbench" ] || fail "the download does not look like Torch Bench."
fi

# 3. Stop a running copy, then put the new program in place.
if [ -x "$BIN/torchbench" ]; then "$BIN/torchbench" stop >/dev/null 2>&1 </dev/null || true; fi
mkdir -p "$HOME_DIR" "$BIN"
chmod 700 "$HOME_DIR"
NEW="$HOME_DIR/program.new"
rm -rf "$NEW"
mkdir -p "$NEW/tests/fixtures"
cp -R "$SRC/app" "$NEW/app"
cp "$SRC/tests/fake_ssh.py" "$NEW/tests/fake_ssh.py"
cp -R "$SRC/tests/fixtures/demo" "$NEW/tests/fixtures/demo"
find "$NEW" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$PROGRAM.old"
[ -d "$PROGRAM" ] && mv "$PROGRAM" "$PROGRAM.old"
mv "$NEW" "$PROGRAM"
rm -rf "$PROGRAM.old"
cp "$SRC/torchbench" "$BIN/torchbench"
chmod +x "$BIN/torchbench"

# 4. Make `torchbench` available in new Terminal windows.
case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    PROFILE="$HOME/.zprofile"
    LINE="export PATH=\"$BIN:\$PATH\"  # torchbench"
    if ! grep -qsF "# torchbench" "$PROFILE"; then
      printf '\n%s\n' "$LINE" >> "$PROFILE"
      say "Added the torchbench command to your PATH (in $PROFILE). Open a new Terminal window to use it."
    fi
    ;;
esac

# 5. A double-clickable launcher, built here on this Mac (so macOS does not treat it as a download).
if [ "$(uname -s)" = "Darwin" ]; then
  # /Applications when this account may write there (most Macs), otherwise the Applications folder in your home.
  if [ -w /Applications ]; then APPS=/Applications; else APPS="$HOME/Applications"; fi
  rm -rf "$HOME/Applications/Torch Bench.app"
  APP="$APPS/Torch Bench.app"
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS"
  cat > "$APP/Contents/MacOS/TorchBench" <<LAUNCH
#!/bin/bash
exec "$BIN/torchbench" start
LAUNCH
  chmod +x "$APP/Contents/MacOS/TorchBench"
  cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Torch Bench</string>
  <key>CFBundleDisplayName</key><string>Torch Bench</string>
  <key>CFBundleIdentifier</key><string>local.torchbench.launcher</string>
  <key>CFBundleExecutable</key><string>TorchBench</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.4.0</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST
  say "Installed. Torch Bench is in $APPS. Open it from there, or run: torchbench"
else
  say "Installed. Run: $BIN/torchbench"
fi

# 6. Open it.
if [ "${TORCHBENCH_NO_START:-0}" != "1" ]; then "$BIN/torchbench" start </dev/null || true; fi
