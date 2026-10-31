#!/usr/bin/env bash
# Theodore setup: configures the DaVinci Resolve Studio scripting environment
# variables for this shell and persists them to your shell profile.
#
# Resolve's external scripting API is Studio-only (the free edition does not
# expose it), and Resolve must be running with a project open before
# Theodore can connect.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}==>${NC} $*"; }
warn()  { echo -e "${YELLOW}!!${NC} $*"; }
fail()  { echo -e "${RED}xx${NC} $*"; exit 1; }

OS="$(uname -s 2>/dev/null || echo unknown)"

case "$OS" in
  Darwin)
    RESOLVE_SCRIPT_API_VAL="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    RESOLVE_SCRIPT_LIB_VAL="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
    ;;
  Linux)
    RESOLVE_SCRIPT_API_VAL="/opt/resolve/Developer/Scripting"
    RESOLVE_SCRIPT_LIB_VAL="/opt/resolve/libs/Fusion/fusionscript.so"
    if [ -d "/home/resolve/Developer/Scripting" ] && [ ! -d "$RESOLVE_SCRIPT_API_VAL" ]; then
      RESOLVE_SCRIPT_API_VAL="/home/resolve/Developer/Scripting"
    fi
    ;;
  MINGW*|MSYS*|CYGWIN*)
    warn "Detected a Windows shell (Git Bash/MSYS/Cygwin)."
    echo
    echo "DaVinci Resolve's scripting bridge on Windows needs these set as"
    echo "*persistent Windows environment variables* (System Properties ->"
    echo "Environment Variables), not just shell-local ones -- and Resolve"
    echo "fails to load the module if the values are wrapped in quotes."
    echo
    echo "Set these (UNQUOTED, exactly as shown):"
    echo
    echo '  RESOLVE_SCRIPT_API  = %PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting'
    echo '  RESOLVE_SCRIPT_LIB  = C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll'
    echo '  PYTHONPATH          = %PYTHONPATH%;%RESOLVE_SCRIPT_API%\Modules\'
    echo
    echo "Or from PowerShell (run once, then open a new terminal):"
    echo
    cat <<'PS1'
  [Environment]::SetEnvironmentVariable('RESOLVE_SCRIPT_API', '%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting', 'User')
  [Environment]::SetEnvironmentVariable('RESOLVE_SCRIPT_LIB', 'C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll', 'User')
  [Environment]::SetEnvironmentVariable('PYTHONPATH', $env:PYTHONPATH + ';' + $env:RESOLVE_SCRIPT_API + '\Modules\', 'User')
PS1
    echo
    exit 0
    ;;
  *)
    fail "Unrecognized OS '$OS'. Set RESOLVE_SCRIPT_API, RESOLVE_SCRIPT_LIB, and PYTHONPATH manually -- see README.md."
    ;;
esac

info "Detected $OS"

MISSING=0
if [ ! -d "$RESOLVE_SCRIPT_API_VAL" ]; then
  warn "RESOLVE_SCRIPT_API path not found: $RESOLVE_SCRIPT_API_VAL"
  warn "Is DaVinci Resolve STUDIO installed? The free edition does not ship the scripting API."
  MISSING=1
fi
if [ ! -f "$RESOLVE_SCRIPT_LIB_VAL" ]; then
  warn "RESOLVE_SCRIPT_LIB path not found: $RESOLVE_SCRIPT_LIB_VAL"
  MISSING=1
fi
if [ "$MISSING" -eq 1 ]; then
  fail "One or more Resolve scripting paths are missing. Install/repair DaVinci Resolve Studio, then re-run setup.sh."
fi

info "Found Resolve scripting API at: $RESOLVE_SCRIPT_API_VAL"
info "Found Resolve scripting lib at: $RESOLVE_SCRIPT_LIB_VAL"

PROFILE=""
if [ -n "${ZSH_VERSION:-}" ] || [ "${SHELL##*/}" = "zsh" ]; then
  PROFILE="$HOME/.zshrc"
elif [ -n "${BASH_VERSION:-}" ] || [ "${SHELL##*/}" = "bash" ]; then
  PROFILE="$HOME/.bash_profile"
  if [ -f "$HOME/.bashrc" ] && [ ! -f "$PROFILE" ]; then
    PROFILE="$HOME/.bashrc"
  fi
else
  PROFILE="$HOME/.profile"
fi

MARKER="# >>> theodore / DaVinci Resolve scripting env >>>"
END_MARKER="# <<< theodore / DaVinci Resolve scripting env <<<"

if [ -f "$PROFILE" ] && grep -qF "$MARKER" "$PROFILE"; then
  info "$PROFILE already has a Theodore block -- leaving it as-is. Delete the block"
  info "between '$MARKER' and '$END_MARKER' first if you want setup.sh to regenerate it."
else
  {
    echo ""
    echo "$MARKER"
    echo "export RESOLVE_SCRIPT_API=\"$RESOLVE_SCRIPT_API_VAL\""
    echo "export RESOLVE_SCRIPT_LIB=\"$RESOLVE_SCRIPT_LIB_VAL\""
    echo "export PYTHONPATH=\"\${PYTHONPATH:-}:\$RESOLVE_SCRIPT_API/Modules/\""
    echo "$END_MARKER"
  } >> "$PROFILE"
  info "Appended Resolve scripting env vars to $PROFILE"
fi

export RESOLVE_SCRIPT_API="$RESOLVE_SCRIPT_API_VAL"
export RESOLVE_SCRIPT_LIB="$RESOLVE_SCRIPT_LIB_VAL"
export PYTHONPATH="${PYTHONPATH:-}:$RESOLVE_SCRIPT_API/Modules/"

info "Set for this shell session too. Open a NEW terminal (or run 'source $PROFILE')"
info "for it to apply everywhere. Make sure DaVinci Resolve Studio is running with a"
info "project open before using 'theodore markers'."
echo
info "If the connection still fails, check Resolve -> Preferences -> System ->"
info "General -> 'External scripting using', and set it to 'Local'."
