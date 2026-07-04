#!/usr/bin/env bash
#
# Copy the app to a badge over USB as a separate "Race Condition (dev)" app.
#
# Builds the published payload from the current working tree (uncommitted AND
# untracked files included, .gitignore/export-ignore respected) via a throwaway
# git index, then installs it to /apps/racecondition_dev/ so it appears in the
# launcher as "Race Condition (dev)" alongside any store-installed copy.
#
# The launcher names apps from metadata.json (tildagon.toml is only used by the
# app store), so this injects one with the dev name and the current git
# describe as the version.
#
# Usage:
#   scripts/deploy-dev.sh [--port /dev/ttyACM0] [--dest racecondition_dev]
#                         [--name "Race Condition (dev)"] [--no-reset]
#
# Requires mpremote (used natively if it runs, otherwise via pipx run).

set -euo pipefail

APP_NAME="Race Condition (dev)"
DEST="racecondition_dev"
PORT=""
DO_RESET=1

if [ -t 1 ]; then GRN=$'\e[32m'; RED=$'\e[31m'; RST=$'\e[0m'; else GRN=""; RED=""; RST=""; fi
info() { printf '%s==>%s %s\n' "$GRN" "$RST" "$*"; }
die()  { printf '%sERROR:%s %s\n' "$RED" "$RST" "$*" >&2; exit 1; }

usage() { awk 'NR>1 && /^#/{sub(/^# ?/,"");print;next} NR>1{exit}' "$0"; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --port)     PORT="${2:?--port needs a value}"; shift ;;
    --dest)     DEST="${2:?--dest needs a value}"; shift ;;
    --name)     APP_NAME="${2:?--name needs a value}"; shift ;;
    --no-reset) DO_RESET=0 ;;
    -h|--help)  usage 0 ;;
    *)          die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

case "$DEST" in
  *[!a-zA-Z0-9_]*|[0-9]*) die "--dest must be a valid Python identifier (imported as apps.$DEST.app)" ;;
esac

REPO_ROOT="$(git rev-parse --show-toplevel)" || die "not inside the git repo"
cd "$REPO_ROOT"

# Prefer a native mpremote, but check it actually runs — a pipx shim can
# survive on PATH with a dead venv behind it (e.g. after a python upgrade).
if command -v mpremote >/dev/null 2>&1 && mpremote version >/dev/null 2>&1; then
  MPREMOTE=(mpremote)
elif command -v pipx >/dev/null 2>&1; then
  MPREMOTE=(pipx run mpremote)
else
  die "no working mpremote and no pipx on PATH"
fi
[ -n "$PORT" ] && MPREMOTE+=(connect "$PORT")

# ── Build the payload ─────────────────────────────────────────────────────────
PAYLOAD="$(mktemp -d)"
trap 'rm -rf "$PAYLOAD"' EXIT

info "Building payload from working tree ($(git describe --tags --always --dirty))"
# A throwaway index picks up unstaged and untracked files without touching
# git status. hexpansion-firmwares is export-ignored anyway; excluding it from
# the add just silences the embedded-repo warning.
GIT_INDEX_FILE="$(mktemp -u)" bash -c '
  git add -A -- ":!hexpansion-firmwares" &&
  git archive --worktree-attributes --format=tar "$(git write-tree)"
' | tar -x -C "$PAYLOAD"

# ── Dev-ify it ────────────────────────────────────────────────────────────────
VERSION="$(git describe --tags --always --dirty)"
python3 - "$PAYLOAD/metadata.json" "$APP_NAME" "$VERSION" <<'EOF'
import json, sys
path, name, version = sys.argv[1:]
with open(path, "w") as f:
    json.dump({"name": name, "hidden": False, "version": version}, f)
EOF

# Best-effort: also mark the in-app title screen. Harmless no-op if the line
# in badge/app.py changes shape.
sed -i 's/text("Race Condition")/text("Race Condition (dev)")/' "$PAYLOAD/badge/app.py"

# ── Install ───────────────────────────────────────────────────────────────────
info "Preparing /apps/$DEST on the badge (removes any previous dev copy)"
"${MPREMOTE[@]}" exec "
import os
def rmr(p):
    try:
        st = os.stat(p)
    except OSError:
        return
    if st[0] & 0x4000:
        for e in os.listdir(p):
            rmr(p + '/' + e)
        os.rmdir(p)
    else:
        os.remove(p)
rmr('/apps/$DEST')
try:
    os.mkdir('/apps')
except OSError:
    pass
os.mkdir('/apps/$DEST')
" || die "badge not responding — is it plugged in and not in a REPL/app that holds the port?"

info "Copying payload"
"${MPREMOTE[@]}" cp -r "$PAYLOAD"/* ":/apps/$DEST/"

if [ "$DO_RESET" = 1 ]; then
  info "Resetting badge"
  "${MPREMOTE[@]}" reset
fi

info "Done. Look for \"$APP_NAME\" in the launcher (version $VERSION)."
