# System prompt templates

One template per Python handler (`agent/handlers/<name>.py`), rendered once at import
or per call by `render_prompt(name, **values)` from `agent/handlers/prompts.py`.

- A template holds what is specific to that handler: its role, its JSON output format
  (which must match the handler's parser) and Python-only mechanics such as
  `plan_context`, pending steps or `{new_chest_id}`.
- `$name` placeholders are filled by the handler, typically `$commands`
  (`command_list(...)`) and shared domain knowledge (`knowledge(...)` from
  `agent/knowledge/`). Every placeholder must be supplied and no extra values are
  accepted. Write a literal dollar sign as `$$`.
- Domain rules shared with the agent skill belong in `agent/knowledge/`, not here.
- User prompts built from live state stay in the handler code.
- `test/test_prompts.py` checks that every template is rendered and fully filled;
  `test/test_knowledge.py` enforces per-prompt size budgets.
