import os

from ollama import AsyncClient
from .base import LLMClient


class OllamaClient(LLMClient):
    """本地 Ollama（免費、無網路）"""

    def __init__(self, model: str = "qwen3:14b", host: str | None = None):
        host = host or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        print(host)
        self.model = model
        self.client = AsyncClient(host=host)

    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        response = await self.client.chat(
            model=self.model,
            messages=all_messages,
        )
        return response["message"]["content"]
