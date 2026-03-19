import asyncio
import json
import websockets

WS_URL = "ws://localhost:3001"

async def on_state(state: dict):
    """收到 Bot 的 state 時，決定要做什麼 action"""
    print(f"[State] type={state['type']}  "
          f"pos=({state['pos']['x']:.1f}, {state['pos']['y']:.1f}, {state['pos']['z']:.1f})  "
          f"hp={state['health']}  food={state['food']}  "
          f"entities={len(state['entities'])}")

    # 目前先不做任何 action，只是印出 state
    # 之後這裡會呼叫 LLM 來決策
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
