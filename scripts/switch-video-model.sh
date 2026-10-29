#!/usr/bin/env bash
# Switch the active video-use agent profile to a different model variant.
# Usage: switch-video-model.sh <gemini|gpt|claude|qwen|grok|kimi|deepseek|default>
set -euo pipefail

ALIAS="${1:-default}"
case "$ALIAS" in
  gemini|default)  FILE="video-use.md" ;;
  gpt)             FILE="video-use-gpt.md" ;;
  claude)          FILE="video-use-claude.md" ;;
  qwen)            FILE="video-use-qwen.md" ;;
  grok)            FILE="video-use-grok.md" ;;
  kimi)            FILE="video-use-kimi.md" ;;
  deepseek)        FILE="video-use-deepseek.md" ;;
  *)
    echo "Unknown alias: $ALIAS" >&2
    echo "Usage: $0 <gemini|gpt|claude|qwen|grok|kimi|deepseek|default>" >&2
    exit 1
    ;;
esac

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_DIR/agents/opencode/$FILE"
DST="$HOME/.config/opencode/agents/video-use.md"

if [ ! -f "$SRC" ]; then
  echo "Profile not found: $SRC" >&2
  exit 1
fi

mkdir -p "$(dirname "$DST")"
ln -sfn "$SRC" "$DST"
echo "video-use agent now uses: $FILE ($(grep '^model:' "$SRC" | head -1))"