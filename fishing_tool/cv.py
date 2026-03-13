"""
cv.py — Pure OpenCV screen-understanding functions.

No game state, no config objects, no side-effects.
All functions take numpy images and return plain dicts.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

# ── HSV colour ranges ─────────────────────────────────────────────────────────
_WATER_LOWER = np.array([80, 45, 30],  dtype=np.uint8)
_WATER_UPPER = np.array([130, 255, 255], dtype=np.uint8)

# Sky / open air: bright + desaturated (V≥170 excludes cobblestone V≈100-155)
_SKY_LOWER = np.array([0, 0, 170],   dtype=np.uint8)
_SKY_UPPER = np.array([180, 60, 255], dtype=np.uint8)
# ─────────────────────────────────────────────────────────────────────────────


def _ratio(mask: np.ndarray) -> float:
    return float(np.count_nonzero(mask)) / float(mask.size) if mask.size else 0.0


def compute_water_features(img: Optional[np.ndarray]) -> dict[str, float]:
    """
    Analyse a crop for water content.
    Returns water_score, blue_ratio, brightness_std, edge_density.
    """
    if img is None or img.size == 0:
        return {"water_score": 0.0, "blue_ratio": 0.0,
                "brightness_std": 0.0, "edge_density": 0.0}

    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    lower = np.array([80, 30, 30], dtype=np.uint8)
    upper = np.array([130, 255, 255], dtype=np.uint8)
    blue_mask  = cv2.inRange(hsv, lower, upper)
    blue_ratio = _ratio(blue_mask)

    brightness_std = float(np.std(hsv[:, :, 2]))

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edge_density = _ratio(edges)

    water_score = min(
        1.0,
        0.65 * blue_ratio
        + 0.25 * min(brightness_std / 64.0, 1.0)
        + 0.10 * min(edge_density / 0.2, 1.0),
    )
    return {"water_score": water_score, "blue_ratio": blue_ratio,
            "brightness_std": brightness_std, "edge_density": edge_density}


def compute_lane_features(
    img: Optional[np.ndarray],
    low_threshold:  float = 0.42,
    high_threshold: float = 0.62,
) -> dict[str, float | str]:
    """
    3-class segmentation of the centre-lane crop.
    Returns all scoring fields plus lane_state ("CLEAR" / "RISKY" / "BLOCKED").
    """
    _blocked: dict[str, float | str] = {
        "clearance_score": 0.0, "center_water_ratio": 0.0,
        "center_nonwater_occupancy": 0.0, "connected_block_ratio": 0.0,
        "upper_open_ratio": 0.0, "upper_water_ratio": 0.0,
        "upper_nonwater_ratio": 0.0, "lower_water_ratio": 0.0,
        "lower_nonwater_ratio": 0.0, "left_water_ratio": 0.0,
        "left_nonwater_ratio": 0.0, "right_water_ratio": 0.0,
        "right_nonwater_ratio": 0.0, "dominant_block_side": "unknown",
        "move_pitch_up_score": 0.0, "move_pitch_down_score": 0.0,
        "move_yaw_left_score": 0.0, "move_yaw_right_score": 0.0,
        "residual_obstacle_score": 1.0, "edge_density": 0.0,
        "vertical_edge_ratio": 0.0, "horizontal_edge_ratio": 0.0,
        "lane_state": "BLOCKED",
    }
    if img is None or img.size == 0:
        return _blocked

    bgr     = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    hsv     = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray    = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # ── 3-class segmentation ─────────────────────────────────────────────────
    water_mask = cv2.inRange(hsv, _WATER_LOWER, _WATER_UPPER)
    kernel     = np.ones((3, 3), dtype=np.uint8)
    water_mask = cv2.morphologyEx(water_mask, cv2.MORPH_OPEN,  kernel)
    water_mask = cv2.morphologyEx(water_mask, cv2.MORPH_CLOSE, kernel)

    sky_mask      = cv2.inRange(hsv, _SKY_LOWER, _SKY_UPPER)
    obstacle_mask = cv2.bitwise_not(cv2.bitwise_or(water_mask, sky_mask))
    # ─────────────────────────────────────────────────────────────────────────

    h, w    = gray.shape
    cx1, cx2 = int(w * 0.20), int(w * 0.80)
    cy1, cy2 = int(h * 0.18), int(h * 0.88)

    core_water    = water_mask[cy1:cy2, cx1:cx2]
    core_sky      = sky_mask[cy1:cy2, cx1:cx2]
    core_obstacle = obstacle_mask[cy1:cy2, cx1:cx2]

    total = float(core_water.size) or 1.0
    center_water_ratio        = float(np.count_nonzero(core_water))    / total
    center_sky_ratio          = float(np.count_nonzero(core_sky))      / total
    center_nonwater_occupancy = float(np.count_nonzero(core_obstacle)) / total

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(core_obstacle, connectivity=8)
    largest = 0
    for i in range(1, num_labels):
        largest = max(largest, int(stats[i, cv2.CC_STAT_AREA]))
    connected_block_ratio = float(largest) / total

    upper_obs   = obstacle_mask[: max(1, int(h * 0.30)), cx1:cx2]
    lower_obs   = obstacle_mask[int(h * 0.55):, cx1:cx2]
    upper_water = water_mask[: max(1, int(h * 0.30)), cx1:cx2]
    lower_water = water_mask[int(h * 0.55):, cx1:cx2]
    left_obs    = core_obstacle[:, : max(1, core_obstacle.shape[1] // 2)]
    right_obs   = core_obstacle[:, max(1, core_obstacle.shape[1] // 2):]
    left_water  = core_water[:,   : max(1, core_water.shape[1] // 2)]
    right_water = core_water[:,   max(1, core_water.shape[1] // 2):]

    upper_open_ratio     = 1.0 - _ratio(upper_obs)
    upper_water_ratio    = _ratio(upper_water)
    upper_nonwater_ratio = _ratio(upper_obs)
    lower_water_ratio    = _ratio(lower_water)
    lower_nonwater_ratio = _ratio(lower_obs)
    left_water_ratio     = _ratio(left_water)
    left_nonwater_ratio  = _ratio(left_obs)
    right_water_ratio    = _ratio(right_water)
    right_nonwater_ratio = _ratio(right_obs)

    directional_blocks = {
        "lower": lower_nonwater_ratio - 0.6 * lower_water_ratio,
        "upper": upper_nonwater_ratio - 0.6 * upper_water_ratio,
        "left":  left_nonwater_ratio  - 0.6 * left_water_ratio,
        "right": right_nonwater_ratio - 0.6 * right_water_ratio,
    }
    dominant_block_side = max(directional_blocks, key=directional_blocks.get)

    move_pitch_up_score = min(1.0,
        0.50 * upper_water_ratio + 0.30 * upper_open_ratio
        + 0.20 * lower_nonwater_ratio - 0.20 * upper_nonwater_ratio)
    move_pitch_down_score = min(1.0,
        0.45 * lower_water_ratio + 0.20 * upper_nonwater_ratio
        - 0.25 * lower_nonwater_ratio)
    move_yaw_right_score = min(1.0,
        0.55 * right_water_ratio + 0.30 * (1.0 - right_nonwater_ratio)
        + 0.15 * left_nonwater_ratio)
    move_yaw_left_score = min(1.0,
        0.55 * left_water_ratio + 0.30 * (1.0 - left_nonwater_ratio)
        + 0.15 * right_nonwater_ratio)
    residual_obstacle_score = min(1.0,
        0.45 * lower_nonwater_ratio + 0.30 * left_nonwater_ratio
        + 0.20 * right_nonwater_ratio + 0.15 * center_nonwater_occupancy
        - 0.25 * center_water_ratio)

    edges   = cv2.Canny(blurred, 50, 150)
    edge_density = _ratio(edges)

    gx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
    ge = float(np.sum(np.abs(gx)) + np.sum(np.abs(gy))) + 1e-6
    vertical_edge_ratio   = float(np.sum(np.abs(gx))) / ge
    horizontal_edge_ratio = float(np.sum(np.abs(gy))) / ge

    openness_score = min(1.0,
        0.55 * center_water_ratio + 0.25 * center_sky_ratio
        + 0.20 * upper_open_ratio)
    obstacle_score = min(1.0,
        0.35 * center_nonwater_occupancy
        + 0.35 * min(connected_block_ratio / 0.30, 1.0)
        + 0.30 * lower_nonwater_ratio)
    orientation_penalty = 0.10 * max(0.0, horizontal_edge_ratio - 0.62)
    clearance_score = max(0.0, min(1.0,
        0.55 * openness_score + 0.45 * (1.0 - obstacle_score) - orientation_penalty))

    if clearance_score >= high_threshold:
        lane_state = "CLEAR"
    elif clearance_score >= low_threshold:
        lane_state = "RISKY"
    else:
        lane_state = "BLOCKED"

    return {
        "clearance_score": clearance_score,
        "center_water_ratio": center_water_ratio,
        "center_nonwater_occupancy": center_nonwater_occupancy,
        "connected_block_ratio": connected_block_ratio,
        "upper_open_ratio": upper_open_ratio,
        "upper_water_ratio": upper_water_ratio,
        "upper_nonwater_ratio": upper_nonwater_ratio,
        "lower_water_ratio": lower_water_ratio,
        "lower_nonwater_ratio": lower_nonwater_ratio,
        "left_water_ratio": left_water_ratio,
        "left_nonwater_ratio": left_nonwater_ratio,
        "right_water_ratio": right_water_ratio,
        "right_nonwater_ratio": right_nonwater_ratio,
        "dominant_block_side": dominant_block_side,
        "move_pitch_up_score": move_pitch_up_score,
        "move_pitch_down_score": move_pitch_down_score,
        "move_yaw_left_score": move_yaw_left_score,
        "move_yaw_right_score": move_yaw_right_score,
        "residual_obstacle_score": residual_obstacle_score,
        "edge_density": edge_density,
        "vertical_edge_ratio": vertical_edge_ratio,
        "horizontal_edge_ratio": horizontal_edge_ratio,
        "lane_state": lane_state,
    }


def compute_bobber_features(
    img: Optional[np.ndarray],
    bobber_min_score: float = 0.55,
) -> dict[str, float]:
    """
    Detect the fishing bobber (red/white pair) in a crop.
    Returns bobber_score, bobber_visible (0/1), candidate_count,
    red_white_pair_ratio.
    """
    if img is None or img.size == 0:
        return {"bobber_score": 0.0, "bobber_visible": 0.0,
                "candidate_count": 0.0, "red_white_pair_ratio": 0.0}

    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    red1 = cv2.inRange(hsv,
        np.array([0,   120, 80], dtype=np.uint8),
        np.array([10,  255, 255], dtype=np.uint8))
    red2 = cv2.inRange(hsv,
        np.array([170, 120, 80], dtype=np.uint8),
        np.array([180, 255, 255], dtype=np.uint8))
    red_mask   = cv2.bitwise_or(red1, red2)
    white_mask = cv2.inRange(hsv,
        np.array([0, 0, 160],   dtype=np.uint8),
        np.array([180, 70, 255], dtype=np.uint8))

    kernel    = np.ones((3, 3), dtype=np.uint8)
    red_mask  = cv2.morphologyEx(red_mask,   cv2.MORPH_OPEN, kernel)
    white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_OPEN, kernel)

    r_labels, _, r_stats, r_centers = cv2.connectedComponentsWithStats(red_mask,   connectivity=8)
    w_labels, _, w_stats, w_centers = cv2.connectedComponentsWithStats(white_mask, connectivity=8)

    candidate_count = 0
    pair_score      = 0.0
    for ri in range(1, r_labels):
        ra = int(r_stats[ri, cv2.CC_STAT_AREA])
        if ra < 1 or ra > 30:
            continue
        rx, ry = r_centers[ri]
        for wi in range(1, w_labels):
            wa = int(w_stats[wi, cv2.CC_STAT_AREA])
            if wa < 1 or wa > 45:
                continue
            wx, wy = w_centers[wi]
            d = float(np.hypot(rx - wx, ry - wy))
            if d > 18:
                continue
            candidate_count += 1
            pair_score = max(pair_score,
                max(0.0, 1.0 - d / 18.0) * min((ra + wa) / 18.0, 1.0))

    red_white_pair_ratio = min(candidate_count / 3.0, 1.0)
    bobber_score         = min(1.0, 0.7 * pair_score + 0.3 * red_white_pair_ratio)
    bobber_visible       = 1.0 if bobber_score >= bobber_min_score else 0.0

    return {
        "bobber_score": bobber_score,
        "bobber_visible": bobber_visible,
        "candidate_count": float(candidate_count),
        "red_white_pair_ratio": red_white_pair_ratio,
    }


def lane_water_preference(lane: dict) -> float:
    """Score how much water is visible — used for choosing walk direction."""
    directional = max(
        float(lane.get("upper_water_ratio", 0.0)),
        float(lane.get("lower_water_ratio", 0.0)),
        float(lane.get("left_water_ratio",  0.0)),
        float(lane.get("right_water_ratio", 0.0)),
    )
    return (
        float(lane.get("center_water_ratio", 0.0))
        + 0.40 * directional
        - 0.20 * float(lane.get("residual_obstacle_score", 0.0))
    )


def lane_quality(lane: dict) -> float:
    """Overall cast-quality score for a lane state."""
    return (
        float(lane.get("clearance_score", 0.0))
        + 0.35 * float(lane.get("center_water_ratio", 0.0))
        - 0.25 * float(lane.get("residual_obstacle_score", 0.0))
    )
