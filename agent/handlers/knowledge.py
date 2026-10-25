"""
Shared domain knowledge loader.
agent/knowledge/*.md is also packaged as the agent skill's references, so prompts
and the skill read the same rules. See agent/knowledge/README.md.
"""
from functools import cache
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"


@cache
def _read(name: str) -> str:
    return (KNOWLEDGE_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


@cache
def _sections(name: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    title = None
    for line in _read(name).splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            sections[title] = line
        elif title is not None:
            sections[title] += "\n" + line
    return {k: v.strip() for k, v in sections.items()}


def knowledge(name: str, *sections: str) -> str:
    """Return a whole knowledge file, or only the named `## ` sections in the given order."""
    if not sections:
        return _read(name)
    available = _sections(name)
    missing = [s for s in sections if s not in available]
    if missing:
        raise KeyError(f"{name}.md has no section {missing}")
    return "\n\n".join(available[s] for s in sections)
