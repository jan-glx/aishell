#!/usr/bin/env bash
set -euo pipefail

TMUX_BIN="${TMUX_BIN:-/usr/bin/tmux}"
SESSION_REGEX="${TMUX_SESSION_REGEX:-^default-}"

if ! "$TMUX_BIN" list-sessions >/dev/null 2>&1; then
    exit 0
fi

"$TMUX_BIN" list-sessions -F '#{session_name} #{session_attached}' \
| /usr/bin/awk -v re="$SESSION_REGEX" '$1 ~ re && $2 == 0 { print $1 }' \
| while IFS= read -r session_name; do
    [ -n "$session_name" ] || continue
    "$TMUX_BIN" kill-session -t "$session_name"
done
