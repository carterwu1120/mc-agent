from abc import ABC, abstractmethod


class LLMClient(ABC):
    """所有 LLM provider 的共同介面"""

    @abstractmethod
    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        """
        送出對話，回傳模型的回覆文字。

        messages 格式：[{"role": "user", "content": "..."}]
        system：system prompt（可選）
        """
        ...
