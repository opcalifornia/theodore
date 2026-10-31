#!/usr/bin/env bash
# Theodore, one command: ./install.sh
#
# Sets up everything on a Mac so Claude can edit inside DaVinci Resolve Studio:
#   1. checks Resolve Studio, Python 3.11+, ffmpeg, Claude Code
#   2. installs Theodore into ./.venv
#   3. installs the Resolve panel (Workspace -> Workflow Integrations -> Theodore)
#   4. installs the bridge script (Workspace -> Scripts -> Theodore -> Theodore Bridge)
#   5. connects Theodore's Resolve tools to Claude Code and the Claude desktop app
# Safe to run again: every step checks before it changes anything.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
VENV="$REPO/.venv"
PLUGINS="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins"
PANEL="$PLUGINS/com.theodore.editor.panel"
SDK_NODE="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Workflow Integrations/Examples/SamplePlugin/WorkflowIntegration.node"

say()  { printf "\n\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "  ✓ %s\n" "$*"; }
warn() { printf "  ! %s\n" "$*"; }
die()  { printf "\n  ✗ %s\n" "$*"; exit 1; }

[[ "$(uname)" == "Darwin" ]] || die "This installer is for macOS."

say "1/5  Checking this Mac"
[[ -d "/Applications/DaVinci Resolve/DaVinci Resolve.app" ]] || die "DaVinci Resolve isn't in /Applications. Install DaVinci Resolve STUDIO first."
ok "DaVinci Resolve found"
PY="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3 || true)"
[[ -n "$PY" ]] || die "Python 3.11+ is needed: https://www.python.org/downloads/macos/"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "Python 3.11+ is needed (found $("$PY" --version))."
ok "$("$PY" --version)"
if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg; else die "ffmpeg is needed: install Homebrew (https://brew.sh), then run: brew install ffmpeg"; fi
fi
ok "ffmpeg $(ffmpeg -version | head -1 | awk '{print $3}')"
CLAUDE="$(command -v claude || ls "$HOME/.local/bin/claude" 2>/dev/null || true)"
if [[ -z "$CLAUDE" ]]; then
  warn "Claude Code isn't installed -- installing it (it runs Theodore on your Claude plan)."
  curl -fsSL https://claude.ai/install.sh | bash
  CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
fi
ok "Claude Code: $("$CLAUDE" --version 2>/dev/null | head -1)"

say "2/5  Installing Theodore"
[[ -d "$VENV" ]] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$REPO"
ok "theodore $("$VENV/bin/theodore" --help >/dev/null && echo installed)"
mkdir -p "$REPO/data"
[[ -f "$REPO/.env" ]] || printf "# Optional. Theodore runs on your Claude plan via Claude Code; an API key is only a fallback.\nANTHROPIC_API_KEY=\n" > "$REPO/.env"

say "3/5  Installing the Resolve panel"
if [[ -d "$PANEL" && ! -w "$PANEL" ]] || [[ ! -d "$PANEL" && ! -w "$PLUGINS" ]]; then
  warn "Need admin rights (once) to create Theodore's folder in Resolve's plugin folder."
  sudo mkdir -p "$PANEL" && sudo chown -R "$USER" "$PANEL"
fi
mkdir -p "$PANEL"
"$VENV/bin/theodore" install-panel --dest "$PANEL" >/dev/null
if [[ ! -d "$PANEL/node_modules" && -d "$REPO/resolve_panel/node_modules" ]]; then
  cp -R "$REPO/resolve_panel/node_modules" "$PANEL/"
fi
cat > "$PANEL/theodore-panel-config.json" <<EOF
{
  "theodoreExecutable": "$VENV/bin/theodore",
  "theodoreDataDir": "$REPO/data"
}
EOF
[[ -f "$SDK_NODE" && ! -f "$PANEL/WorkflowIntegration.node" ]] && cp "$SDK_NODE" "$PANEL/" && ok "Resolve's WorkflowIntegration module linked"
ok "Panel installed at: $PANEL"

say "4/5  Installing the bridge script"
"$VENV/bin/theodore" install-bridge >/dev/null && ok "Workspace -> Scripts -> Theodore -> Theodore Bridge"

say "5/5  Connecting Theodore's tools to Claude"
"$CLAUDE" mcp remove --scope user theodore >/dev/null 2>&1 || true
"$CLAUDE" mcp add --scope user theodore -- "$VENV/bin/theodore" mcp-serve >/dev/null && ok "Claude Code: 'theodore' tools available in every session"
DESKTOP_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
if [[ -d "$(dirname "$DESKTOP_CFG")" ]]; then
  "$VENV/bin/python" - "$DESKTOP_CFG" "$VENV/bin/theodore" <<'PY'
import json, sys, pathlib
path, exe = pathlib.Path(sys.argv[1]), sys.argv[2]
cfg = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
cfg.setdefault("mcpServers", {})["theodore"] = {"command": exe, "args": ["mcp-serve"]}
path.write_text(json.dumps(cfg, indent=2))
PY
  ok "Claude desktop app: restart it to pick up the 'theodore' tools"
else
  warn "Claude desktop app not found -- skipped (Claude Code is enough)."
fi

say "Done. Two clicks in Resolve, once:"
cat <<'EOT'
  1. DaVinci Resolve -> Preferences -> System -> General ->
     "External scripting using": Local.  (Then restart Resolve.)
  2. Workspace -> Workflow Integrations -> Theodore   (the chat panel)
     Optional, faster: Workspace -> Scripts -> Theodore -> Theodore Bridge each session.

Then just tell Claude what you want in the Theodore panel -- or in Claude Code:
  "cut the interview in the Interviews bin into a 60 and a 30 with lower thirds and captions"
EOT
