"""
System prompt templates loader.
agent/prompts/<name>.md holds a handler's instructions and output format; `$name`
placeholders are filled by the handler with command lists and shared knowledge.
See agent/prompts/README.md.
"""
from functools import cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@cache
def _template(name: str) -> Template:
    return Template((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").rstrip())


def placeholders(name: str) -> set[str]:
    # get_identifiers() is unavailable in the supported Python 3.10 runtime.
    template = _template(name)
    identifiers = set()
    for match in template.pattern.finditer(template.template):
        identifier = match.group('named') or match.group('braced')
        if identifier:
            identifiers.add(identifier)
        elif match.group('invalid') is not None:
            raise ValueError(f'Invalid placeholder in prompts/{name}.md')
    return identifiers


def render_prompt(name: str, **values: str) -> str:
    """Fill a template; a missing or unexpected value is an error, not a silent gap."""
    expected = placeholders(name)
    if set(values) != expected:
        raise KeyError(f"prompts/{name}.md expects {sorted(expected)}, got {sorted(values)}")
    return _template(name).substitute(values)
