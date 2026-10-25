# Shared domain knowledge

Markdown in this directory is the single source of Minecraft/bot domain knowledge
for two consumers:

- the Python agent — skills load it into their system prompts through
  `agent/skills/knowledge.py` (`knowledge(name, *sections)`), after their own
  output-format instructions;
- the packaged agent skill — `skill/build.sh` copies every file except this README
  into `references/`.

Rules for editing:

- Write interface-neutral knowledge: what commands do, which strategy fits which
  situation, quantities. Output formats (Python JSON shapes) stay in the skill's
  Python file; CLI usage (`mcctl`) stays in `skill/minecraft-bot/SKILL.md`.
- State only behaviour the bot code actually has; check `agent/bot/` before writing
  "the bot will …".
- `## ` headings are section names that Python code may load by title
  (`knowledge("bot_capabilities", "食物")`). Renaming one requires updating callers;
  `test/test_knowledge.py` checks that every requested section exists.
