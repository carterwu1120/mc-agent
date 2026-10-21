"""Single configuration entrypoint for subscription-backed CLI agents."""
import os

from .cli_agent import CLIAgentClient


def build_client() -> CLIAgentClient:
    provider = os.getenv("LLM_PROVIDER", "codex").strip().lower()
    if provider not in ("codex", "claude"):
        raise ValueError(
            f"LLM_PROVIDER={provider!r} is no longer supported. "
            "Set LLM_PROVIDER=codex or claude and sign in with your subscription. "
            "Direct model API providers have been removed on this branch."
        )
    model = os.getenv("LLM_MODEL", "").strip() or None
    return CLIAgentClient(provider, model=model)
