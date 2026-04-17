import os

from openai import AsyncOpenAI
from .base import LLMClient


class OpenAIClient(LLMClient):
    """OpenAI API (gpt-5.4, gpt-5.4-mini, etc.)"""

    def __init__(self, model: str = "gpt-5.4-mini", api_key: str | None = None):
        api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._client = AsyncOpenAI(api_key=api_key)
        self._model_name = model

    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        response = await self._client.chat.completions.create(
            model=self._model_name,
            messages=all_messages,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content
