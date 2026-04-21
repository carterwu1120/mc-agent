from __future__ import annotations

import asyncio
import os
from typing import Any

from google import genai
from google.genai import types
from .base import LLMClient

_THINKING_LEVEL_MAP = {
    "minimal": types.ThinkingLevel.MINIMAL,
    "low":     types.ThinkingLevel.LOW,
    "medium":  types.ThinkingLevel.MEDIUM,
    "high":    types.ThinkingLevel.HIGH,
}


class VertexClient(LLMClient):
    """Gemini on Vertex AI via google-genai SDK (ADC auth, no API key)."""

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        project: str | None = None,
        location: str | None = None,
        thinking_level: str | None = None,
    ):
        project = project or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        if not project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Vertex AI")
        location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

        self._model_name = model
        self._thinking_level = thinking_level.lower() if thinking_level else None
        self._client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(api_version="v1"),
        )

    def _build_config(self, system: str | None) -> types.GenerateContentConfig:
        kwargs: dict[str, Any] = {
            "response_mime_type": "application/json",
        }
        if system:
            kwargs["system_instruction"] = system
        if self._thinking_level and self._thinking_level in _THINKING_LEVEL_MAP:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=_THINKING_LEVEL_MAP[self._thinking_level]
            )
        return types.GenerateContentConfig(**kwargs)

    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

        config = self._build_config(system)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.models.generate_content(
                model=self._model_name,
                contents=contents,
                config=config,
            ),
        )
        return response.text or ""

    # ── Batch methods (future use) ─────────────────────────────────────────────

    async def submit_batch(self, requests: list[dict], **kwargs) -> str:
        raise NotImplementedError("Batch inference not yet wired up in this project")

    async def wait_for_batch(self, batch_id: str, **kwargs) -> dict[str, str]:
        raise NotImplementedError("Batch inference not yet wired up in this project")
