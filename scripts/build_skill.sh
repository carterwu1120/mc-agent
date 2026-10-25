#!/usr/bin/env bash
# Assemble the distributable agent skill (dist/minecraft-bot.zip) from project sources:
#   agent/skill/SKILL.md      -> SKILL.md
#   agent/skill/mcctl.js      -> scripts/mcctl.js
#   agent/bot (runtime only)  -> scripts/bot/
#   agent/skill/setup.md, agent/knowledge/*.md, generated commands.md -> references/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist/minecraft-bot"

rm -rf "$OUT" "$ROOT/dist/minecraft-bot.zip"
mkdir -p "$OUT/scripts/bot" "$OUT/references"
cp "$ROOT/agent/skill/SKILL.md" "$OUT/"
cp "$ROOT/agent/skill/mcctl.js" "$OUT/scripts/"
cp "$ROOT/agent/skill/setup.md" "$OUT/references/"
# Runtime sources only: no tests, installed modules, local logs or internal docs.
find "$ROOT/agent/bot" -maxdepth 1 -type f \( -name '*.js' -o -name 'package.json' -o -name 'package-lock.json' \) \
    -exec cp {} "$OUT/scripts/bot/" \;
# Domain knowledge is shared with the Python agent's prompts (agent/knowledge/README.md).
find "$ROOT/agent/knowledge" -maxdepth 1 -name '*.md' ! -name 'README.md' -exec cp {} "$OUT/references/" \;
python3 "$ROOT/scripts/gen_skill_commands.py" "$OUT/references/commands.md"

(cd "$OUT" && zip -qr "$ROOT/dist/minecraft-bot.zip" .)
echo "built $ROOT/dist/minecraft-bot.zip"
