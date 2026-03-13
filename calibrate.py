"""
Calibration tool for fishing bot CV.

Usage:
  python calibrate.py

Opens a live window showing the Minecraft window with overlay:
  - GREEN  = detected as water
  - CYAN   = detected as sky/open air
  - RED    = detected as obstacle (solid block)
  - Yellow box = center_lane region
  - Blue box   = water region

Click on the window to print HSV of the clicked pixel.
Press 'q' to quit, 's' to save a calibration screenshot.
"""

import json
import sys
import time
from pathlib import Path

import cv2
import mss
import numpy as np

try:
    import pygetwindow as gw
    HAS_GW = True
except ImportError:
    HAS_GW = False


CONFIG_PATH = Path(__file__).parent / "config.json"


def load_region() -> dict:
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    if cfg.get("region"):
        return cfg["region"]
    if cfg.get("window_title_contains") and HAS_GW:
        wins = gw.getWindowsWithTitle(cfg["window_title_contains"])
        if wins:
            w = wins[0]
            return {"left": w.left, "top": w.top, "width": w.width, "height": w.height}
    with mss.mss() as sct:
        m = sct.monitors[1]
    return {"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}


def sub_region(base: dict, ratio: dict) -> dict:
    return {
        "left":   base["left"]   + int(base["width"]  * ratio["x"]),
        "top":    base["top"]    + int(base["height"] * ratio["y"]),
        "width":  int(base["width"]  * ratio["width"]),
        "height": int(base["height"] * ratio["height"]),
    }


def capture(region: dict) -> np.ndarray:
    with mss.mss() as sct:
        shot = sct.grab(region)
    return np.array(shot)


# ── tuneable HSV ranges ───────────────────────────────────────────────────────
WATER_LOWER = np.array([80, 45, 30], dtype=np.uint8)
WATER_UPPER = np.array([130, 255, 255], dtype=np.uint8)

# Sky / open air: bright + desaturated OR very dark (night sky)
SKY_LOWER = np.array([0, 0, 170], dtype=np.uint8)
SKY_UPPER = np.array([180, 60, 255], dtype=np.uint8)
# ─────────────────────────────────────────────────────────────────────────────


clicked_px = None  # (x, y) in the scaled display


def on_mouse(event, x, y, flags, param):
    global clicked_px
    if event == cv2.EVENT_LBUTTONDOWN:
        clicked_px = (x, y)


def draw_box(img: np.ndarray, base: dict, region: dict | None,
             color: tuple, label: str) -> None:
    if region is None:
        return
    x1 = region["left"] - base["left"]
    y1 = region["top"]  - base["top"]
    x2 = x1 + region["width"]
    y2 = y1 + region["height"]
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    cv2.putText(img, label, (x1, max(18, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)


def main():
    global clicked_px

    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        cfg = json.load(f)

    scale = 0.5  # display scale so it fits on screen

    cv2.namedWindow("calibrate", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("calibrate", on_mouse)

    print("Calibration tool running. Click on the window to inspect HSV.")
    print("Press 'q' to quit, 's' to save screenshot + masks.")

    while True:
        base_region = load_region()
        img_bgra = capture(base_region)
        img_bgr  = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        hsv      = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

        water_mask = cv2.inRange(hsv, WATER_LOWER, WATER_UPPER)
        kernel = np.ones((3, 3), dtype=np.uint8)
        water_mask = cv2.morphologyEx(water_mask, cv2.MORPH_OPEN,  kernel)
        water_mask = cv2.morphologyEx(water_mask, cv2.MORPH_CLOSE, kernel)

        sky_mask = cv2.inRange(hsv, SKY_LOWER, SKY_UPPER)

        obstacle_mask = cv2.bitwise_not(cv2.bitwise_or(water_mask, sky_mask))

        # Build coloured overlay
        overlay = img_bgr.copy()
        overlay[water_mask    > 0] = (0, 200, 0)    # green  = water
        overlay[sky_mask      > 0] = (200, 200, 0)  # cyan   = sky/open
        overlay[obstacle_mask > 0] = (0, 0, 180)    # red    = obstacle

        display = cv2.addWeighted(img_bgr, 0.55, overlay, 0.45, 0)

        # Draw region boxes
        center_lane_ratio = cfg.get("center_lane_region_ratio")
        water_ratio       = cfg.get("water_region_ratio")
        if center_lane_ratio:
            cl_region = sub_region(base_region, center_lane_ratio)
            draw_box(display, base_region, cl_region, (0, 255, 255), "Center Lane")
            # Compute clearance stats for the center lane crop
            x1 = cl_region["left"] - base_region["left"]
            y1 = cl_region["top"]  - base_region["top"]
            x2 = x1 + cl_region["width"]
            y2 = y1 + cl_region["height"]
            crop_w = water_mask[y1:y2, x1:x2]
            crop_s = sky_mask[y1:y2, x1:x2]
            crop_o = obstacle_mask[y1:y2, x1:x2]
            total  = float(crop_w.size) or 1.0
            wr = np.count_nonzero(crop_w) / total
            sr = np.count_nonzero(crop_s) / total
            obr= np.count_nonzero(crop_o) / total
            info = f"water={wr:.2f} sky={sr:.2f} obstacle={obr:.2f}"
            cv2.putText(display, info, (x1, max(18, y1 - 22)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)

        if water_ratio:
            draw_box(display, base_region, sub_region(base_region, water_ratio),
                     (255, 180, 0), "Water ROI")

        # Handle click inspection
        if clicked_px is not None:
            cx, cy = clicked_px
            # scale back to original coords
            ox = int(cx / scale)
            oy = int(cy / scale)
            if 0 <= oy < hsv.shape[0] and 0 <= ox < hsv.shape[1]:
                h_val, s_val, v_val = hsv[oy, ox]
                b, g, r = img_bgr[oy, ox]
                print(f"[CLICK] pixel ({ox},{oy})  "
                      f"BGR=({b},{g},{r})  HSV=({h_val},{s_val},{v_val})")
                # draw crosshair
                cv2.drawMarker(display, (ox, oy), (0, 255, 255),
                               cv2.MARKER_CROSS, 20, 2)
            clicked_px = None

        small = cv2.resize(display, (0, 0), fx=scale, fy=scale)
        cv2.imshow("calibrate", small)

        key = cv2.waitKey(100) & 0xFF
        if key == ord('q'):
            break
        if key == ord('s'):
            cv2.imwrite("logs/calibrate_overlay.jpg", display)
            cv2.imwrite("logs/calibrate_water_mask.jpg", water_mask)
            cv2.imwrite("logs/calibrate_sky_mask.jpg", sky_mask)
            cv2.imwrite("logs/calibrate_obstacle_mask.jpg", obstacle_mask)
            print("[SAVE] logs/calibrate_*.jpg saved")

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
