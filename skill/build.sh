#!/usr/bin/env bash
# Assemble the distributable skill: skill/minecraft-bot + a copy of agent/bot, zipped
# with SKILL.md at the archive root.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/skill/minecraft-bot"
DIST="$ROOT/skill/dist"
OUT="$DIST/minecraft-bot"

rm -rf "$OUT" "$DIST/minecraft-bot.zip"
mkdir -p "$OUT"
cp -r "$SRC/." "$OUT/"
mkdir -p "$OUT/scripts/bot"
# Runtime sources only: no tests, installed modules, local logs or internal docs.
(cd "$ROOT/agent/bot" && find . -maxdepth 1 -type f \( -name '*.js' -o -name 'package.json' -o -name 'package-lock.json' \) \
    -exec cp {} "$OUT/scripts/bot/" \;)
# Domain knowledge is shared with the Python agent's prompts (agent/knowledge/README.md).
find "$ROOT/agent/knowledge" -maxdepth 1 -name '*.md' ! -name 'README.md' -exec cp {} "$OUT/references/" \;
python3 "$ROOT/skill/gen_commands.py" "$OUT/references/commands.md"

(cd "$OUT" && zip -qr "$DIST/minecraft-bot.zip" .)
echo "built $DIST/minecraft-bot.zip"
(cd "$OUT" && find . -type f | sort)
