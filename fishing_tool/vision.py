from __future__ import annotations

import base64
import re
from typing import Optional

import cv2
import numpy as np


def _encode_image(img: np.ndarray, index: int) -> str:
    if img.ndim == 3 and img.shape[2] == 4:
        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    else:
        bgr = img.copy()
    small = cv2.resize(bgr, (512, 384), interpolation=cv2.INTER_AREA)
    # Draw index label so LLM can clearly reference each image
    label = str(index)
    cv2.putText(small, label, (8, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(small, label, (8, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2, cv2.LINE_AA)
    _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode("utf-8")


def _parse_index(text: str, count: int) -> Optional[int]:
    matches = re.findall(r"\b([1-9]\d*)\b", text)
    # Take the last valid number — models typically state their final answer last
    for m in reversed(matches):
        idx = int(m) - 1
        if 0 <= idx < count:
            return idx
    return None


def _parse_walk_labels(text: str, count: int) -> Optional[int]:
    """
    Parse per-image WALKABLE/BLOCKED and WATER/NO_WATER labels.
    Expected format per line: "1: WALKABLE, WATER"
    Scoring: WALKABLE+WATER=2, WALKABLE+NO_WATER=1, BLOCKED=0.
    Returns 0-based index of highest-scoring walkable direction, or None.
    """
    scores: dict[int, int] = {}
    for line in text.splitlines():
        m = re.match(
            r"\s*([1-9]\d*)\s*[:.)]?\s*(WALKABLE|BLOCKED)[^A-Z]*(NO_WATER|WATER)",
            line,
            re.IGNORECASE,
        )
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < count:
                walkable = m.group(2).upper() == "WALKABLE"
                water = m.group(3).upper() == "WATER"
                scores[idx] = (2 if walkable else 0) + (1 if water else 0)
    if not scores:
        return None
    best_idx = max(scores, key=lambda i: scores[i])
    if scores[best_idx] == 0:
        return None  # all blocked
    return best_idx


def ask_single_probe(
    screenshot: np.ndarray,
    index: int,
    model: str = "llava:7b",
    host: str = "http://localhost:11434",
) -> tuple[bool, bool]:
    """
    Unified single-frame probe for both casting and walking decisions.
    Returns (clear, has_water).
    clear=True means no wall/fence/block blocking the path forward.
    """
    try:
        import ollama
    except ImportError:
        return True, False

    image = _encode_image(screenshot, index)
    prompt = (
        "This is a Minecraft screenshot. "
        "Answer two questions about what is directly in front of the player:\n"
        "1. CLEAR or BLOCKED — is the path forward open? "
        "BLOCKED means a wall, fence, or solid block fills the center.\n"
        "2. WATER or NO_WATER — is blue or teal water surface visible?\n"
        "Reply with exactly two words separated by a comma. Example: CLEAR, WATER"
    )

    try:
        client = ollama.Client(host=host)
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": [image]}],
        )
        text = response["message"]["content"].upper()
        clear = "CLEAR" in text
        water = "NO_WATER" not in text and "WATER" in text
        return clear, water
    except Exception as e:
        print(f"[VISION] ask_single_probe error: {e}")
        return True, False


def ask_best_fishing_angle(
    screenshots: list[np.ndarray],
    model: str = "llava:7b",
    host: str = "http://localhost:11434",
) -> Optional[int]:
    """
    Given screenshots taken at equal yaw intervals (covering 360°),
    ask Ollama which view is best for fishing.
    Returns 0-based index, or None on failure.
    """
    try:
        import ollama
    except ImportError:
        print("[VISION] ollama package not installed, skipping")
        return None

    count = len(screenshots)
    images = [_encode_image(img, i + 1) for i, img in enumerate(screenshots)]
    prompt = (
        f"You are helping a Minecraft fishing bot choose the best camera angle. "
        f"Each image is labeled with a number (1 to {count}) in the top-left corner. "
        f"The images are taken at equal yaw intervals covering 360°. "
        f"In Minecraft, water appears as a flat blue or teal surface. "
        f"Choose the image number that shows open water directly in front of the player "
        f"with no walls, fences, or solid blocks blocking the line of sight to the water. "
        f"If no image shows clear water, pick the one with the most visible water surface. "
        f"Reply with ONLY a single number from 1 to {count}. Do not explain."
    )

    try:
        client = ollama.Client(host=host)
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": images}],
        )
        text = response["message"]["content"]
        print(f"[VISION] angle response: {text.strip()}")
        return _parse_index(text, count)
    except Exception as e:
        print(f"[VISION] ask_best_fishing_angle error: {e}")
        return None


def ask_single_fishing_angle(
    screenshot: np.ndarray,
    index: int,
    model: str = "llava:7b",
    host: str = "http://localhost:11434",
) -> tuple[bool, bool]:
    """
    Ask Ollama if this angle is suitable for casting a fishing rod.
    Returns (castable, has_water).
    """
    try:
        import ollama
    except ImportError:
        return True, False

    image = _encode_image(screenshot, index)
    prompt = (
        "This is a Minecraft screenshot. "
        "Answer two questions about what is directly in front of the player:\n"
        "1. CASTABLE or BLOCKED — can the player cast a fishing rod forward? "
        "BLOCKED means a wall, fence, or solid block fills the center and would stop the cast.\n"
        "2. WATER or NO_WATER — is blue or teal water surface visible in front?\n"
        "Reply with exactly two words separated by a comma. Example: CASTABLE, WATER"
    )

    try:
        client = ollama.Client(host=host)
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": [image]}],
        )
        text = response["message"]["content"].upper()
        castable = "CASTABLE" in text
        water = "NO_WATER" not in text and "WATER" in text
        return castable, water
    except Exception as e:
        print(f"[VISION] ask_single_fishing_angle error: {e}")
        return True, False


def ask_single_direction(
    screenshot: np.ndarray,
    index: int,
    model: str = "llava:7b",
    host: str = "http://localhost:11434",
) -> tuple[bool, bool]:
    """
    Ask Ollama to classify a single screenshot.
    Returns (walkable, has_water).
    """
    try:
        import ollama
    except ImportError:
        return True, False

    image = _encode_image(screenshot, index)
    prompt = (
        "This is a Minecraft screenshot. "
        "Answer two questions about what is directly in front of the player:\n"
        "1. WALKABLE or BLOCKED — can the player walk forward? "
        "BLOCKED means a wall, fence, or solid block fills the center of the view.\n"
        "2. WATER or NO_WATER — is blue or teal water surface visible?\n"
        "Reply with exactly two words separated by a comma. Example: WALKABLE, WATER"
    )

    try:
        client = ollama.Client(host=host)
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": [image]}],
        )
        text = response["message"]["content"].upper()
        walkable = "WALKABLE" in text
        water = "NO_WATER" not in text and "WATER" in text
        return walkable, water
    except Exception as e:
        print(f"[VISION] ask_single_direction error: {e}")
        return True, False


def ask_best_walk_direction(
    screenshots: list[np.ndarray],
    model: str = "llava:7b",
    host: str = "http://localhost:11434",
) -> Optional[int]:
    """
    Given screenshots taken at equal yaw intervals (covering 360°),
    ask Ollama which direction to walk to reach open water.
    Returns 0-based index, or None on failure.
    """
    try:
        import ollama
    except ImportError:
        print("[VISION] ollama package not installed, skipping")
        return None

    count = len(screenshots)
    images = [_encode_image(img, i + 1) for i, img in enumerate(screenshots)]
    prompt = (
        f"You are analyzing {count} Minecraft screenshots taken at equal yaw intervals covering 360°. "
        f"Each image is labeled with a number (1 to {count}) in the top-left corner. "
        f"For EACH image, output exactly one line using this format:\n"
        f"[number]: WALKABLE or BLOCKED, WATER or NO_WATER\n\n"
        f"WALKABLE = the player can walk forward — no wall, fence, or solid block fills the center.\n"
        f"BLOCKED = a wall, fence, or solid block is directly in front and blocks walking.\n"
        f"WATER = blue or teal water surface is visible.\n"
        f"NO_WATER = no water visible.\n\n"
        f"Example:\n"
        f"1: WALKABLE, WATER\n"
        f"2: BLOCKED, NO_WATER\n"
        f"3: WALKABLE, NO_WATER\n\n"
        f"Output ONLY the {count} classification lines. No explanation."
    )

    try:
        client = ollama.Client(host=host)
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": images}],
        )
        text = response["message"]["content"]
        print(f"[VISION] walk direction response:\n{text.strip()}")
        return _parse_walk_labels(text, count)
    except Exception as e:
        print(f"[VISION] ask_best_walk_direction error: {e}")
        return None
