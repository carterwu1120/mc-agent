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
from pathlib import Path

import cv2
import mss
import numpy as np

from fishing_tool.cv import compute_lane_features, compute_water_features

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


# ── visualisation-only HSV ranges (scoring uses fishing_tool.cv) ────────────
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
        water_ratio = cfg.get("water_region_ratio")
        low_thr = float(cfg.get("center_lane_clearance_low_threshold", 0.42))
        high_thr = float(cfg.get("center_lane_clearance_high_threshold", 0.62))

        lane_metrics = None
        water_metrics = None

        if center_lane_ratio:
            cl_region = sub_region(base_region, center_lane_ratio)
            draw_box(display, base_region, cl_region, (0, 255, 255), "Center Lane")
            x1 = cl_region["left"] - base_region["left"]
            y1 = cl_region["top"]  - base_region["top"]
            x2 = x1 + cl_region["width"]
            y2 = y1 + cl_region["height"]
            crop_lane_bgr = img_bgr[y1:y2, x1:x2]
            if crop_lane_bgr.size > 0:
                crop_lane_bgra = cv2.cvtColor(crop_lane_bgr, cv2.COLOR_BGR2BGRA)
                lane_metrics = compute_lane_features(
                    crop_lane_bgra,
                    low_threshold=low_thr,
                    high_threshold=high_thr,
                )

        if water_ratio:
            wr_region = sub_region(base_region, water_ratio)
            draw_box(display, base_region, wr_region, (255, 180, 0), "Water ROI")
            x1 = wr_region["left"] - base_region["left"]
            y1 = wr_region["top"]  - base_region["top"]
            x2 = x1 + wr_region["width"]
            y2 = y1 + wr_region["height"]
            crop_water_bgr = img_bgr[y1:y2, x1:x2]
            if crop_water_bgr.size > 0:
                crop_water_bgra = cv2.cvtColor(crop_water_bgr, cv2.COLOR_BGR2BGRA)
                water_metrics = compute_water_features(crop_water_bgra)

        lines = []
        if water_metrics is not None:
            lines.extend(
                [
                    f"water_score={water_metrics['water_score']:.2f}",
                    f"blue_ratio={water_metrics['blue_ratio']:.2f}",
                    f"brightness_std={water_metrics['brightness_std']:.1f}",
                    f"water_edge_density={water_metrics['edge_density']:.2f}",
                ]
            )
        if lane_metrics is not None:
            lines.extend(
                [
                    f"lane_state={lane_metrics['lane_state']}",
                    f"clearance={lane_metrics['clearance_score']:.2f}",
                    f"center_water_ratio={lane_metrics['center_water_ratio']:.2f}",
                    f"center_nonwater={lane_metrics['center_nonwater_occupancy']:.2f}",
                    f"connected_block={lane_metrics['connected_block_ratio']:.2f}",
                    f"residual_obstacle={lane_metrics['residual_obstacle_score']:.2f}",
                    f"move_pitch_up={lane_metrics['move_pitch_up_score']:.2f}",
                    f"move_pitch_down={lane_metrics['move_pitch_down_score']:.2f}",
                    f"move_yaw_left={lane_metrics['move_yaw_left_score']:.2f}",
                    f"move_yaw_right={lane_metrics['move_yaw_right_score']:.2f}",
                ]
            )

        y_text = 26
        for line in lines:
            color = (235, 235, 235)
            if "lane_state=CLEAR" in line:
                color = (90, 255, 90)
            elif "lane_state=RISKY" in line:
                color = (80, 220, 255)
            elif "lane_state=BLOCKED" in line:
                color = (110, 110, 255)
            cv2.putText(
                display,
                line,
                (16, y_text),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
                cv2.LINE_AA,
            )
            y_text += 24

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
