"""Live entity detection on the Minecraft window."""
from pathlib import Path
import mss
import numpy as np
import cv2
import pygetwindow as gw
from ultralytics import YOLO

BEST_PT = Path(__file__).parent / "runs/entity_v3/weights/best.pt"
WINDOW_TITLE = "Minecraft"
CONF = 0.4


def get_minecraft_region() -> dict:
    windows = gw.getWindowsWithTitle(WINDOW_TITLE)
    if not windows:
        raise RuntimeError(f"Window '{WINDOW_TITLE}' not found. Is Minecraft running?")
    w = windows[0]
    return {"left": w.left, "top": w.top, "width": w.width, "height": w.height}


def main():
    model = YOLO(str(BEST_PT))
    region = get_minecraft_region()
    print(f"Capturing: {region}")
    print("Press Q to quit.")

    with mss.mss() as sct:
        while True:
            frame = np.array(sct.grab(region))
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            results = model(frame, conf=CONF, verbose=False)[0]
            annotated = results.plot()

            cv2.imshow("Entity Detection", annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
