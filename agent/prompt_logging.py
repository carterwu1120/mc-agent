"""Keep full prompts available for opt-in debugging without flooding normal logs."""
import os


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def log_prompt(label: str, prompt: str) -> None:
    if _enabled("AGENT_LOG_PROMPTS"):
        print(f"[{label}] Prompt:\n{prompt}\n---")
    else:
        print(f"[{label}] prompt ready ({len(prompt)} chars)")
