#!/usr/bin/env bash
# install.sh — Watcher installation script
# Installs watcher and watcherd to /usr/local/bin and sets up the environment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PREFIX="${INSTALL_PREFIX:-/usr/local}"
BIN_DIR="$INSTALL_PREFIX/bin"
WATCH_DIR="$HOME/.claude-watch"

echo "Watcher — installation"
echo ""

# Ensure Python 3.9+
PYTHON=$(command -v python3 || command -v python || echo "")
if [ -z "$PYTHON" ]; then
    echo "Error: Python 3 not found. Install Python 3.9+ and try again."
    exit 1
fi

PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  ✓ Python $PY_VERSION"

# Install Python dependencies
echo "  Installing Python dependencies..."
"$PYTHON" -m pip install httpx --quiet --user
echo "  ✓ httpx installed"

# Create ~/.claude-watch structure
mkdir -p "$WATCH_DIR"/{sessions,conversations,archive,hooks,logs}
echo "  ✓ ~/.claude-watch/ created"

# Copy source files
cp -r "$SCRIPT_DIR/src/"* "$WATCH_DIR/"
echo "  ✓ Source files copied"

# Copy and install hook scripts
cp "$SCRIPT_DIR/hooks/"*.sh "$WATCH_DIR/hooks/"
chmod +x "$WATCH_DIR/hooks/"*.sh
echo "  ✓ Hook scripts installed"

# Copy slash command
mkdir -p "$HOME/.claude/commands"
cp "$SCRIPT_DIR/commands/watch.sh" "$HOME/.claude/commands/watch.md" 2>/dev/null || \
    cp "$SCRIPT_DIR/commands/watch.sh" "$HOME/.claude/commands/watch.sh"
chmod +x "$HOME/.claude/commands/"watch* 2>/dev/null || true
echo "  ✓ /watch slash command installed"

# Create send.py symlink/copy in watch dir
cp "$SCRIPT_DIR/src/send.py" "$WATCH_DIR/send.py"
chmod +x "$WATCH_DIR/send.py"
# Update shebang
sed -i.bak "1s|#!/usr/bin/env python3|#!$PYTHON|" "$WATCH_DIR/send.py" && rm -f "$WATCH_DIR/send.py.bak"
echo "  ✓ send.py installed"

# Install watcher CLI
if [ -w "$BIN_DIR" ] || sudo -n true 2>/dev/null; then
    SUDO=""
    [ ! -w "$BIN_DIR" ] && SUDO="sudo"
    $SUDO cp "$SCRIPT_DIR/watcher" "$BIN_DIR/watcher"
    $SUDO chmod +x "$BIN_DIR/watcher"
    # Create watcherd wrapper
    cat > /tmp/watcherd_wrapper << EOF
#!$PYTHON
import sys
sys.path.insert(0, "$WATCH_DIR")
from watcherd import main
import asyncio
asyncio.run(main())
EOF
    $SUDO cp /tmp/watcherd_wrapper "$BIN_DIR/watcherd"
    $SUDO chmod +x "$BIN_DIR/watcherd"
    echo "  ✓ watcher and watcherd installed to $BIN_DIR"
else
    echo "  ⚠ Could not write to $BIN_DIR. Add this to your PATH:"
    echo "    export PATH=\"$SCRIPT_DIR:\$PATH\""
fi

echo ""
echo "Installation complete."
echo ""
echo "Next steps:"
echo "  watcher install   — configure channels and projects"
echo "  watcher doctor    — verify setup"
echo "  watcher start     — go"
