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
