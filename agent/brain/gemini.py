import os
import google.generativeai as genai
from .base import LLMClient


class GeminiClient(LLMClient):
    """Google Gemini API"""

    def __init__(self, model: str = "gemini-2.5-flash", api_key: str | None = None):
        api_key = api_key or os.getenv("GOOGLE_API_KEY")
        genai.configure(api_key=api_key)
        self._model_name = model

    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system,
        )
        # 轉換成 Gemini 格式（history + 最後一則 user message）
        history = []
        for msg in messages[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            history.append({"role": role, "parts": [msg["content"]]})

        chat = model.start_chat(history=history)
        response = await chat.send_message_async(messages[-1]["content"])
        return response.text
