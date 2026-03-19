import asyncio
import json
import websockets
from dotenv import load_dotenv

from agent.brain import LLMClient, OllamaClient

load_dotenv()

WS_URL = "ws://localhost:3001"

# ── 在這裡切換 LLM ────────────────────────────────────────
# llm = GeminiClient()   # 需要 GOOGLE_API_KEY（有免費額度）
llm: LLMClient = OllamaClient(model="qwen3:14b")

SYSTEM_PROMPT = """你是一個 Minecraft 機器人的大腦。
你會收到目前的遊戲狀態（JSON），請決定下一步行動。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "fish"}
{"command": "move"}
{"command": "chat", "text": "你想說的話"}
{"command": "idle"}
"""

async def on_state(state: dict) -> dict | None:
    print(f"[State] type={state['type']}  "
          f"pos=({state['pos']['x']:.1f}, {state['pos']['y']:.1f}, {state['pos']['z']:.1f})  "
          f"hp={state['health']}  food={state['food']}  "
          f"entities={len(state['entities'])}")

    # 只在 tick 時讓 LLM 決策（忽略其他事件）
    # LLM 決策暫時關閉，只用手動 ! 指令測試
    return None

async def run():
    print(f"[Agent] 連線到 {WS_URL} ...")
    async with websockets.connect(WS_URL) as ws:
        print("[Agent] 已連線！等待 state...")
        async for raw in ws:
            state = json.loads(raw)
            action = await on_state(state)
            if action:
                await ws.send(json.dumps(action))

if __name__ == "__main__":
    asyncio.run(run())
