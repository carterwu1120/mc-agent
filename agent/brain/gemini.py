import google.generativeai as genai
from .base import LLMClient


class GeminiClient(LLMClient):
    """Google Gemini API"""

    def __init__(self, model: str = "gemini-2.5-flash", api_key: str | None = None):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            model_name=model,
        )

    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        # 轉換成 Gemini 格式
        history = []
        last_user = None
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            if role == "user":
                last_user = msg["content"]
            else:
                history.append({"role": "user", "parts": [last_user]})
                history.append({"role": "model", "parts": [msg["content"]]})
                last_user = None

        chat = self.model.start_chat(history=history)
        response = await chat.send_message_async(last_user or messages[-1]["content"])
        return response.text
