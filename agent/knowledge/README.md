# Shared domain knowledge

Markdown in this directory is the single source of Minecraft/bot domain knowledge
for two consumers:

- the Python agent — handlers load it into their system prompts through
  `agent/handlers/knowledge.py` (`knowledge(name, *sections)`), after their own
  output-format instructions;
- the agent skill — `scripts/build_skill.sh` copies every file except this README
  into the package's `references/`.

Rules for editing:

- Write interface-neutral knowledge: what commands do, which strategy fits which
  situation, quantities. Output formats (Python JSON shapes) stay in the handler's
  Python file; CLI usage (`mcctl`) stays in `agent/skill/SKILL.md`.
- State only behaviour the bot code actually has; check `agent/bot/` before writing
  "the bot will …".
- `## ` headings are section names that Python code may load by title
  (`knowledge("bot_capabilities", "食物")`). Renaming one requires updating callers;
  `test/test_knowledge.py` checks that every requested section exists.
