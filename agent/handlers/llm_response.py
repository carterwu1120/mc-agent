from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ValidationError


class BaseLLMResponse(BaseModel):
    model_config = {"extra": "allow"}
    action: Optional[str] = None
    command: Optional[str] = None
    commands: Optional[list[str]] = None
    goal: Optional[Any] = None
    text: Optional[str] = None
    reasoning: Optional[str] = None


def parse_llm_json(raw: dict, label: str) -> dict:
    """Validate raw LLM dict, print reasoning if present, return clean dict."""
    try:
        validated = BaseLLMResponse.model_validate(raw)
        if validated.reasoning:
            print(f"[{label}] 推理: {validated.reasoning}")
        return validated.model_dump(exclude_none=True, exclude={"reasoning"})
    except ValidationError as e:
        print(f"[{label}] Pydantic 驗證失敗: {e}")
        return raw


def parse_json_object(text: str) -> dict:
    """Parse a model reply that should be one JSON object.

    Tolerates prose around the object and a reply cut off before its closing braces.
    """
    import json

    def first_object(candidate: str) -> dict | None:
        decoder = json.JSONDecoder()
        idx = candidate.find("{")
        while idx != -1:
            try:
                obj, _end = decoder.raw_decode(candidate[idx:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
            idx = candidate.find("{", idx + 1)
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Close a truncated top-level object before scanning, or the scan would return
    # its first complete nested object instead.
    stripped = text.strip()
    missing = stripped.count("{") - stripped.count("}")
    if stripped.startswith("{") and missing > 0:
        try:
            return json.loads(stripped + "}" * missing)
        except json.JSONDecodeError:
            pass
    found = first_object(text)
    if found is not None:
        return found
    raise json.JSONDecodeError("No valid JSON object found", text, 0)
