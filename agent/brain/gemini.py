import os
from google import genai
from google.genai import types
from .base import LLMClient


class GeminiClient(LLMClient):
    """Google Gemini API"""

    def __init__(self, model: str = "gemini-2.5-flash", api_key: str | None = None):
        api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self._client = genai.Client(api_key=api_key)
        self._model_name = model

    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

        config = types.GenerateContentConfig(
            system_instruction=system,
        )
        response = await self._client.aio.models.generate_content(
            model=self._model_name,
            contents=contents,
            config=config,
        )
        return response.text
