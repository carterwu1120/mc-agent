from abc import ABC, abstractmethod


class LLMClient(ABC):
    """所有 LLM provider 的共同介面"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """
        送出對話，回傳模型的回覆文字。

        messages 格式：[{"role": "user", "content": "..."}]
        system：system prompt（可選）
        reasoning_effort：這次請求的推理強度（可選）
        """
        ...

    def reset_session(self, reason: str = "task_finished") -> None:
        """End provider conversation state at a durable task boundary."""

    def is_available(self) -> bool:
        """Return False while the provider is in a known retry cooldown."""
        return True

    def unavailable_reason(self) -> str | None:
        return None
