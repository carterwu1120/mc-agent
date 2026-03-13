from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import easyocr
import mss
import numpy as np
import pyautogui
import pygetwindow as gw
from paddleocr import PaddleOCR

from .config import FishingConfig


@dataclass
class TriggerResult:
    matched: bool
    keyword: Optional[str] = None
    action: Optional[str] = None
    button: Optional[str] = None
    text: str = ""


class FishingAgent:
    def __init__(self, config: FishingConfig):
        self.config = config
        self.last_trigger_time = 0.0
        self.rod_casted = False

        self.started_at = time.time()
        self.cast_timestamps: list[float] = []
        self.next_stats_print_at = self.started_at + max(1.0, self.config.stats_print_interval_sec)
        self.last_bite_seen_at = self.started_at
        self.last_no_bite_recover_at = 0.0
        self.last_nonempty_ocr_at = self.started_at
        self.last_ocr_empty_recover_at = 0.0
        self.last_lane_state = "UNKNOWN"
        self.last_lane_score = 0.0
        self.last_adjustment_note = "none"
        self.recovery_retry_count = 0
        self.recovery_ladder_index = 0
        self.recovery_offsets = {"pitch": 0, "yaw": 0}
        self.recovery_plan: list[tuple[int, int, str]] = []
        self.angle_failure_counts: dict[tuple[int, int], int] = {}
        self.blocked_streak = 0
        self.last_bobber_visible = False
        self.last_bobber_score = 0.0
        self.optimized_angle_key: Optional[tuple[int, int]] = None
        self.failed_pitch_forward_used = False
        self.clear_casted_timeout_streak = 0
        self.last_ocr_text = ""

        if self.config.ocr_engine == "easyocr":
            self.reader = easyocr.Reader(config.languages, gpu=False)
            self.paddle_reader = None
        else:
            self.reader = None
            self.paddle_reader = PaddleOCR(
                use_angle_cls=False,
                lang=self.config.ocr_lang,
                show_log=False,
            )

    def _lane_snapshot(self) -> dict[str, float | str]:
        center_lane_region = self._center_lane_region()
        center_lane_img = self._capture_region(center_lane_region) if center_lane_region else None
        lane = self._compute_center_lane_features(center_lane_img)
        self.last_lane_state = str(lane["lane_state"])
        self.last_lane_score = float(lane["clearance_score"])
        if self.config.debug_window:
            self._show_debug_window(self.last_ocr_text)
        return lane

    def _capture_region(self, region: dict[str, int]) -> np.ndarray:
        with mss.mss() as sct:
            shot = sct.grab(region)
        return np.array(shot)

    def _normalize(self, text: str) -> str:
        if self.config.case_sensitive:
            return text
        return text.lower()

    def _window_region(self) -> dict[str, int]:
        if self.config.region:
            return self.config.region
        if self.config.window_title_contains:
            windows = gw.getWindowsWithTitle(self.config.window_title_contains)
            if not windows:
                raise RuntimeError(f"Window not found: {self.config.window_title_contains}")
            w = windows[0]
            return {"left": w.left, "top": w.top, "width": w.width, "height": w.height}
        with mss.mss() as sct:
            monitor = sct.monitors[1]
        return {
            "left": monitor["left"],
            "top": monitor["top"],
            "width": monitor["width"],
            "height": monitor["height"],
        }

    def _focus_region(self) -> dict[str, int]:
        base = self._window_region()
        return self._sub_region(base, self.config.focus_region_ratio)

    def _water_region(self) -> Optional[dict[str, int]]:
        if not self.config.water_region_ratio:
            return None
        return self._sub_region(self._window_region(), self.config.water_region_ratio)

    def _center_lane_region(self) -> Optional[dict[str, int]]:
        if not self.config.center_lane_region_ratio:
            return None
        return self._sub_region(self._window_region(), self.config.center_lane_region_ratio)

    def _bobber_region(self) -> Optional[dict[str, int]]:
        ratio = self.config.bobber_region_ratio or self.config.water_region_ratio
        if not ratio:
            return None
        return self._sub_region(self._window_region(), ratio)

    def _sub_region(
        self, base: dict[str, int], ratio: Optional[dict[str, float]]
    ) -> dict[str, int]:
        if not ratio:
            return base

        x = int(base["left"] + base["width"] * float(ratio.get("x", 0.0)))
        y = int(base["top"] + base["height"] * float(ratio.get("y", 0.0)))
        w = int(base["width"] * float(ratio.get("width", 1.0)))
        h = int(base["height"] * float(ratio.get("height", 1.0)))

        return {
            "left": max(x, base["left"]),
            "top": max(y, base["top"]),
            "width": max(1, min(w, base["left"] + base["width"] - x)),
            "height": max(1, min(h, base["top"] + base["height"] - y)),
        }

    def _ocr_with_easyocr(self, img: np.ndarray) -> list[str]:
        if self.reader is None:
            return []
        return self.reader.readtext(img, detail=0, paragraph=True)

    def _ocr_with_paddle(self, img: np.ndarray) -> list[str]:
        if self.paddle_reader is None:
            return []
        gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        scaled = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        binary = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        result = self.paddle_reader.ocr(binary, cls=False)

        texts: list[str] = []
        if not result:
            return texts

        for line in result:
            if not line:
                continue
            for item in line:
                if not item or len(item) < 2:
                    continue
                text_info = item[1]
                if isinstance(text_info, (list, tuple)) and text_info:
                    texts.append(str(text_info[0]))
        return texts

    def capture_text(self) -> str:
        region = self._focus_region()
        img = self._capture_region(region)

        if self.config.ocr_engine == "easyocr":
            texts = self._ocr_with_easyocr(img)
        else:
            texts = self._ocr_with_paddle(img)

        return " ".join(texts).strip()

    def _compute_water_features(self, img: Optional[np.ndarray]) -> dict[str, float]:
        if img is None or img.size == 0:
            return {
                "water_score": 0.0,
                "blue_ratio": 0.0,
                "brightness_std": 0.0,
                "edge_density": 0.0,
            }

        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        lower = np.array([80, 30, 30], dtype=np.uint8)
        upper = np.array([130, 255, 255], dtype=np.uint8)
        blue_mask = cv2.inRange(hsv, lower, upper)
        blue_ratio = float(np.count_nonzero(blue_mask)) / float(blue_mask.size)

        value_channel = hsv[:, :, 2]
        brightness_std = float(np.std(value_channel))

        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = float(np.count_nonzero(edges)) / float(edges.size)

        water_score = min(
            1.0,
            0.65 * blue_ratio
            + 0.25 * min(brightness_std / 64.0, 1.0)
            + 0.10 * min(edge_density / 0.2, 1.0),
        )

        return {
            "water_score": water_score,
            "blue_ratio": blue_ratio,
            "brightness_std": brightness_std,
            "edge_density": edge_density,
        }

    def _compute_center_lane_features(self, img: Optional[np.ndarray]) -> dict[str, float]:
        if img is None or img.size == 0:
            return {
                "clearance_score": 0.0,
                "center_water_ratio": 0.0,
                "center_nonwater_occupancy": 0.0,
                "connected_block_ratio": 0.0,
                "upper_open_ratio": 0.0,
                "upper_water_ratio": 0.0,
                "upper_nonwater_ratio": 0.0,
                "lower_water_ratio": 0.0,
                "lower_nonwater_ratio": 0.0,
                "left_water_ratio": 0.0,
                "left_nonwater_ratio": 0.0,
                "right_water_ratio": 0.0,
                "right_nonwater_ratio": 0.0,
                "dominant_block_side": "unknown",
                "move_pitch_up_score": 0.0,
                "move_pitch_down_score": 0.0,
                "move_yaw_left_score": 0.0,
                "move_yaw_right_score": 0.0,
                "residual_obstacle_score": 1.0,
                "edge_density": 0.0,
                "vertical_edge_ratio": 0.0,
                "horizontal_edge_ratio": 0.0,
                "lane_state": "BLOCKED",
            }

        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)

        # ── 3-class segmentation: water / sky+open / obstacle ────────────────
        water_lower = np.array([80, 45, 30], dtype=np.uint8)
        water_upper = np.array([130, 255, 255], dtype=np.uint8)
        water_mask = cv2.inRange(hsv, water_lower, water_upper)
        kernel = np.ones((3, 3), dtype=np.uint8)
        water_mask = cv2.morphologyEx(water_mask, cv2.MORPH_OPEN, kernel)
        water_mask = cv2.morphologyEx(water_mask, cv2.MORPH_CLOSE, kernel)

        # Sky / open air: bright + desaturated.
        # V≥170 excludes most solid blocks (cobblestone V≈100-155) while
        # keeping actual sky/clouds (V≈170-255) and horizon haze.
        sky_lower = np.array([0, 0, 170], dtype=np.uint8)
        sky_upper = np.array([180, 60, 255], dtype=np.uint8)
        sky_mask = cv2.inRange(hsv, sky_lower, sky_upper)

        # Obstacle = neither water nor sky
        obstacle_mask = cv2.bitwise_not(cv2.bitwise_or(water_mask, sky_mask))
        # ─────────────────────────────────────────────────────────────────────

        h, w = gray.shape
        core_x1 = int(w * 0.20)
        core_x2 = int(w * 0.80)
        core_y1 = int(h * 0.18)
        core_y2 = int(h * 0.88)
        core_water    = water_mask[core_y1:core_y2, core_x1:core_x2]
        core_sky      = sky_mask[core_y1:core_y2, core_x1:core_x2]
        core_obstacle = obstacle_mask[core_y1:core_y2, core_x1:core_x2]

        total = float(core_water.size) or 1.0
        center_water_ratio        = float(np.count_nonzero(core_water))    / total
        center_sky_ratio          = float(np.count_nonzero(core_sky))      / total
        center_nonwater_occupancy = float(np.count_nonzero(core_obstacle)) / total  # true obstacle ratio

        # Largest connected obstacle component in core
        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(core_obstacle, connectivity=8)
        largest_component = 0
        for label_idx in range(1, num_labels):
            largest_component = max(largest_component, int(stats[label_idx, cv2.CC_STAT_AREA]))
        connected_block_ratio = float(largest_component) / total

        # Band slices (using obstacle_mask so sky ≠ blocked)
        upper_obs_band   = obstacle_mask[: max(1, int(h * 0.30)), core_x1:core_x2]
        lower_obs_band   = obstacle_mask[int(h * 0.55) :, core_x1:core_x2]
        upper_water_band = water_mask[: max(1, int(h * 0.30)), core_x1:core_x2]
        lower_water_band = water_mask[int(h * 0.55) :, core_x1:core_x2]
        left_obs_band    = core_obstacle[:, : max(1, core_obstacle.shape[1] // 2)]
        right_obs_band   = core_obstacle[:, max(1, core_obstacle.shape[1] // 2) :]
        left_water_band  = core_water[:, : max(1, core_water.shape[1] // 2)]
        right_water_band = core_water[:, max(1, core_water.shape[1] // 2) :]

        def _r(m: np.ndarray) -> float:
            return float(np.count_nonzero(m)) / float(m.size) if m.size else 0.0

        upper_open_ratio   = 1.0 - _r(upper_obs_band)   # sky + water in upper = open
        upper_water_ratio  = _r(upper_water_band)
        upper_nonwater_ratio = _r(upper_obs_band)        # obstacle in upper band
        lower_water_ratio  = _r(lower_water_band)
        lower_nonwater_ratio = _r(lower_obs_band)        # obstacle in lower band
        left_water_ratio   = _r(left_water_band)
        left_nonwater_ratio  = _r(left_obs_band)
        right_water_ratio  = _r(right_water_band)
        right_nonwater_ratio = _r(right_obs_band)

        directional_blocks = {
            "lower": lower_nonwater_ratio - 0.6 * lower_water_ratio,
            "upper": upper_nonwater_ratio - 0.6 * upper_water_ratio,
            "left":  left_nonwater_ratio  - 0.6 * left_water_ratio,
            "right": right_nonwater_ratio - 0.6 * right_water_ratio,
        }
        dominant_block_side = max(directional_blocks, key=directional_blocks.get)

        move_pitch_up_score = min(
            1.0,
            0.50 * upper_water_ratio
            + 0.30 * upper_open_ratio
            + 0.20 * lower_nonwater_ratio
            - 0.20 * upper_nonwater_ratio,
        )
        move_pitch_down_score = min(
            1.0,
            0.45 * lower_water_ratio
            + 0.20 * upper_nonwater_ratio
            - 0.25 * lower_nonwater_ratio,
        )
        move_yaw_right_score = min(
            1.0,
            0.55 * right_water_ratio
            + 0.30 * (1.0 - right_nonwater_ratio)
            + 0.15 * left_nonwater_ratio,
        )
        move_yaw_left_score = min(
            1.0,
            0.55 * left_water_ratio
            + 0.30 * (1.0 - left_nonwater_ratio)
            + 0.15 * right_nonwater_ratio,
        )
        residual_obstacle_score = min(
            1.0,
            0.45 * lower_nonwater_ratio
            + 0.30 * left_nonwater_ratio
            + 0.20 * right_nonwater_ratio
            + 0.15 * center_nonwater_occupancy
            - 0.25 * center_water_ratio,
        )

        edges = cv2.Canny(blurred, 50, 150)
        edge_density = float(np.count_nonzero(edges)) / float(edges.size)

        grad_x = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
        abs_grad_x = np.abs(grad_x)
        abs_grad_y = np.abs(grad_y)

        grad_energy = float(np.sum(abs_grad_x) + np.sum(abs_grad_y)) + 1e-6
        vertical_edge_ratio   = float(np.sum(abs_grad_x)) / grad_energy
        horizontal_edge_ratio = float(np.sum(abs_grad_y)) / grad_energy

        # openness: water is best, open sky also counts as clear
        openness_score = min(
            1.0,
            0.55 * center_water_ratio
            + 0.25 * center_sky_ratio
            + 0.20 * upper_open_ratio,
        )
        obstacle_score = min(
            1.0,
            0.35 * center_nonwater_occupancy
            + 0.35 * min(connected_block_ratio / 0.30, 1.0)
            + 0.30 * lower_nonwater_ratio,
        )
        orientation_penalty = 0.10 * max(0.0, horizontal_edge_ratio - 0.62)
        clearance_score = max(
            0.0,
            min(1.0, 0.55 * openness_score + 0.45 * (1.0 - obstacle_score) - orientation_penalty),
        )
        if clearance_score >= self.config.center_lane_clearance_high_threshold:
            lane_state = "CLEAR"
        elif clearance_score >= self.config.center_lane_clearance_low_threshold:
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

    def _compute_bobber_features(self, img: Optional[np.ndarray]) -> dict[str, float]:
        if img is None or img.size == 0:
            return {
                "bobber_score": 0.0,
                "bobber_visible": 0.0,
                "candidate_count": 0.0,
                "red_white_pair_ratio": 0.0,
            }

        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

        red_mask_1 = cv2.inRange(
            hsv,
            np.array([0, 120, 80], dtype=np.uint8),
            np.array([10, 255, 255], dtype=np.uint8),
        )
        red_mask_2 = cv2.inRange(
            hsv,
            np.array([170, 120, 80], dtype=np.uint8),
            np.array([180, 255, 255], dtype=np.uint8),
        )
        red_mask = cv2.bitwise_or(red_mask_1, red_mask_2)
        white_mask = cv2.inRange(
            hsv,
            np.array([0, 0, 160], dtype=np.uint8),
            np.array([180, 70, 255], dtype=np.uint8),
        )

        kernel = np.ones((3, 3), dtype=np.uint8)
        red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, kernel)
        white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_OPEN, kernel)

        red_labels, _, red_stats, red_centroids = cv2.connectedComponentsWithStats(
            red_mask, connectivity=8
        )
        white_labels, _, white_stats, white_centroids = cv2.connectedComponentsWithStats(
            white_mask, connectivity=8
        )

        candidate_count = 0
        pair_score = 0.0
        for r_idx in range(1, red_labels):
            r_area = int(red_stats[r_idx, cv2.CC_STAT_AREA])
            if r_area < 1 or r_area > 30:
                continue
            rx, ry = red_centroids[r_idx]
            for w_idx in range(1, white_labels):
                w_area = int(white_stats[w_idx, cv2.CC_STAT_AREA])
                if w_area < 1 or w_area > 45:
                    continue
                wx, wy = white_centroids[w_idx]
                distance = float(np.hypot(rx - wx, ry - wy))
                if distance > 18:
                    continue
                candidate_count += 1
                pair_score = max(
                    pair_score,
                    max(0.0, 1.0 - distance / 18.0) * min((r_area + w_area) / 18.0, 1.0),
                )

        red_white_pair_ratio = min(candidate_count / 3.0, 1.0)
        bobber_score = min(1.0, 0.7 * pair_score + 0.3 * red_white_pair_ratio)
        bobber_visible = 1.0 if bobber_score >= self.config.bobber_min_score else 0.0
        self.last_bobber_visible = bobber_visible >= 0.5
        self.last_bobber_score = bobber_score

        return {
            "bobber_score": bobber_score,
            "bobber_visible": bobber_visible,
            "candidate_count": float(candidate_count),
            "red_white_pair_ratio": red_white_pair_ratio,
        }

    def _draw_region_box(
        self,
        frame: np.ndarray,
        base: dict[str, int],
        region: Optional[dict[str, int]],
        color: tuple[int, int, int],
        label: str,
    ) -> None:
        if region is None:
            return

        x1 = max(0, region["left"] - base["left"])
        y1 = max(0, region["top"] - base["top"])
        x2 = min(frame.shape[1] - 1, x1 + region["width"])
        y2 = min(frame.shape[0] - 1, y1 + region["height"])
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame,
            label,
            (x1, max(18, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )

    def _draw_preview_tile(
        self,
        canvas: np.ndarray,
        img: Optional[np.ndarray],
        top_left: tuple[int, int],
        size: tuple[int, int],
        title: str,
    ) -> None:
        x, y = top_left
        width, height = size
        cv2.rectangle(canvas, (x, y), (x + width, y + height), (70, 70, 70), 1)
        cv2.putText(
            canvas,
            title,
            (x, max(20, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (220, 220, 220),
            2,
            cv2.LINE_AA,
        )

        if img is None or img.size == 0:
            cv2.putText(
                canvas,
                "N/A",
                (x + 10, y + height // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (180, 180, 180),
                2,
                cv2.LINE_AA,
            )
            return

        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        preview = cv2.resize(bgr, (width, height), interpolation=cv2.INTER_AREA)
        canvas[y : y + height, x : x + width] = preview

    def _show_debug_window(self, text: str) -> None:
        if not self.config.debug_window:
            return

        base_region = self._window_region()
        window_img = self._capture_region(base_region)
        focus_region = self._focus_region()
        water_region = self._water_region()
        center_lane_region = self._center_lane_region()
        bobber_region = self._bobber_region()

        focus_img = self._capture_region(focus_region)
        water_img = self._capture_region(water_region) if water_region else None
        center_lane_img = self._capture_region(center_lane_region) if center_lane_region else None
        bobber_img = self._capture_region(bobber_region) if bobber_region else None
        features = self._compute_water_features(water_img)
        center_lane = self._compute_center_lane_features(center_lane_img)
        bobber = self._compute_bobber_features(bobber_img)

        main = cv2.cvtColor(window_img, cv2.COLOR_BGRA2BGR)
        self._draw_region_box(main, base_region, focus_region, (0, 255, 255), "OCR ROI")
        self._draw_region_box(main, base_region, water_region, (255, 180, 0), "Water ROI")
        self._draw_region_box(main, base_region, bobber_region, (255, 110, 110), "Bobber ROI")
        lane_state = center_lane["lane_state"]
        if lane_state == "CLEAR":
            lane_color = (0, 220, 0)
        elif lane_state == "RISKY":
            lane_color = (0, 215, 255)
        else:
            lane_color = (0, 0, 255)
        self._draw_region_box(main, base_region, center_lane_region, lane_color, "Center Lane")

        panel_width = 820
        canvas_height = max(main.shape[0], 720)
        canvas = np.zeros((canvas_height, main.shape[1] + panel_width, 3), dtype=np.uint8)
        canvas[: main.shape[0], : main.shape[1]] = main
        cv2.rectangle(
            canvas,
            (main.shape[1], 0),
            (canvas.shape[1] - 1, canvas.shape[0] - 1),
            (40, 40, 40),
            -1,
        )

        self._draw_preview_tile(canvas, focus_img, (main.shape[1] + 20, 50), (180, 110), "OCR")
        self._draw_preview_tile(
            canvas,
            water_img,
            (main.shape[1] + 220, 50),
            (180, 110),
            "Water",
        )
        self._draw_preview_tile(
            canvas,
            center_lane_img,
            (main.shape[1] + 420, 50),
            (180, 110),
            "Center Lane",
        )
        self._draw_preview_tile(
            canvas,
            bobber_img,
            (main.shape[1] + 620, 50),
            (180, 110),
            "Bobber",
        )

        lines = [
            f"rod_casted: {self.rod_casted}",
            f"last text: {text[:36] or '(empty)'}",
            f"last_lane_state: {self.last_lane_state}",
            f"last_adjustment: {self.last_adjustment_note}",
            f"bobber_visible: {self.last_bobber_visible}",
            f"bobber_score: {bobber['bobber_score']:.2f}",
            f"bobber_candidates: {int(bobber['candidate_count'])}",
            (
                "recovery_offsets: "
                f"pitch={self.recovery_offsets['pitch']} yaw={self.recovery_offsets['yaw']}"
            ),
            f"water_score: {features['water_score']:.2f}",
            f"blue_ratio: {features['blue_ratio']:.2f}",
            f"brightness_std: {features['brightness_std']:.1f}",
            f"water_edge_density: {features['edge_density']:.2f}",
            f"center_clearance: {center_lane['clearance_score']:.2f}",
            f"center_lane: {lane_state}",
            f"center_water_ratio: {center_lane['center_water_ratio']:.2f}",
            f"center_nonwater_occ: {center_lane['center_nonwater_occupancy']:.2f}",
            f"connected_block_ratio: {center_lane['connected_block_ratio']:.2f}",
            f"dominant_block_side: {center_lane['dominant_block_side']}",
            f"upper_open_ratio: {center_lane['upper_open_ratio']:.2f}",
            f"upper_water_ratio: {center_lane['upper_water_ratio']:.2f}",
            f"upper_nonwater_ratio: {center_lane['upper_nonwater_ratio']:.2f}",
            f"lower_water_ratio: {center_lane['lower_water_ratio']:.2f}",
            f"lower_nonwater_ratio: {center_lane['lower_nonwater_ratio']:.2f}",
            f"left_water_ratio: {center_lane['left_water_ratio']:.2f}",
            f"left_nonwater_ratio: {center_lane['left_nonwater_ratio']:.2f}",
            f"right_water_ratio: {center_lane['right_water_ratio']:.2f}",
            f"right_nonwater_ratio: {center_lane['right_nonwater_ratio']:.2f}",
            f"move_pitch_up_score: {center_lane['move_pitch_up_score']:.2f}",
            f"move_pitch_down_score: {center_lane['move_pitch_down_score']:.2f}",
            f"move_yaw_left_score: {center_lane['move_yaw_left_score']:.2f}",
            f"move_yaw_right_score: {center_lane['move_yaw_right_score']:.2f}",
            f"residual_obstacle_score: {center_lane['residual_obstacle_score']:.2f}",
            f"refinement_water_target: {self.config.refinement_water_target:.2f}",
            f"viable_water: {'yes' if self._lane_has_viable_water(center_lane) else 'no'}",
            f"center_edge_density: {center_lane['edge_density']:.2f}",
            f"vertical_edge_ratio: {center_lane['vertical_edge_ratio']:.2f}",
            f"horizontal_edge_ratio: {center_lane['horizontal_edge_ratio']:.2f}",
            f"lane_low_threshold: {self.config.center_lane_clearance_low_threshold:.2f}",
            f"lane_high_threshold: {self.config.center_lane_clearance_high_threshold:.2f}",
            f"fail gap sec: {time.time() - self.last_bite_seen_at:.1f}",
            "ESC/q closes preview",
        ]

        y = 220
        for line in lines:
            color = (230, 230, 230)
            if "CLEAR" in line:
                color = (100, 255, 100)
            elif "RISKY" in line:
                color = (80, 220, 255)
            elif "BLOCKED" in line:
                color = (120, 120, 255)
            cv2.putText(
                canvas,
                line,
                (main.shape[1] + 20, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                color,
                1,
                cv2.LINE_AA,
            )
            y += 34

        scale = max(0.2, self.config.debug_window_scale)
        resized = cv2.resize(
            canvas,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA,
        )
        cv2.imshow("Fishing Agent Debug", resized)
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            self.config.debug_window = False
            cv2.destroyWindow("Fishing Agent Debug")

    def _select_button(self, normalized_text: str) -> str:
        for key, button in self.config.button_rules.items():
            k = key if self.config.case_sensitive else key.lower()
            if k in normalized_text:
                return "left" if button == "left" else "right"
        return "left" if self.config.default_button == "left" else "right"

    def _click(self, button: str) -> None:
        pyautogui.click(button=button)

    def _lane_has_viable_water(self, lane: Optional[dict[str, float | str]] = None) -> bool:
        lane = lane or {}
        center_water = float(lane.get("center_water_ratio", 0.0))
        upper_open = float(lane.get("upper_open_ratio", 0.0))
        best_directional_water = max(
            float(lane.get("upper_water_ratio", 0.0)),
            float(lane.get("lower_water_ratio", 0.0)),
            float(lane.get("left_water_ratio", 0.0)),
            float(lane.get("right_water_ratio", 0.0)),
        )
        return (
            center_water >= self.config.viable_water_center_threshold
            or best_directional_water >= self.config.viable_water_direction_threshold
            or (
                center_water >= self.config.viable_water_center_threshold * 0.9
                and upper_open >= 0.58
            )
        )

    def _ensure_cast_lane_ready(self) -> str:
        lane = self._lane_snapshot()
        lane_state = str(lane["lane_state"])
        start_lane_state = lane_state
        attempts = 0
        viable_water = self._lane_has_viable_water(lane)

        if lane_state == "BLOCKED":
            self.blocked_streak += 1
        else:
            self.blocked_streak = 0

        # Already fishing-ready and no known angle issues — skip all adjustments
        if lane_state == "CLEAR" and not self._current_angle_is_bad():
            self.optimized_angle_key = self._current_angle_key()
            return lane_state

        should_broad_scan = lane_state == "BLOCKED" and (
            not viable_water
            or self.blocked_streak >= self.config.blocked_scan_trigger_count
            or self._best_move_score(lane) < self.config.blocked_probe_min_improvement
        )

        if should_broad_scan:
            improved, scanned_lane, scan_label = self._scan_for_open_lane(lane)
            self.last_adjustment_note = scan_label
            lane = scanned_lane
            lane_state = str(lane["lane_state"])
            if improved and lane_state != "BLOCKED":
                self.blocked_streak = 0
                self.optimized_angle_key = None
                return lane_state

            # CV sweep failed — unified LLM scan (angle + walk in one pass)
            if lane_state == "BLOCKED":
                improved, scanned_lane, scan_label = self._ollama_unified_scan(lane)
                self.last_adjustment_note = scan_label
                lane = scanned_lane
                lane_state = str(lane["lane_state"])
                if improved:
                    self.blocked_streak = 0
                    self.optimized_angle_key = None
                    return lane_state if lane_state != "BLOCKED" else "RISKY"

            # Ollama walk also failed — last resort CV water walk
            if lane_state == "BLOCKED":
                moved, water_lane, water_label = self._scan_for_most_water_and_forward(lane)
                self.last_adjustment_note = water_label
                lane = water_lane
                lane_state = str(lane["lane_state"])
                if moved and lane_state != "BLOCKED":
                    self.blocked_streak = 0
                    self.optimized_angle_key = None
                    return lane_state

        if lane_state != "BLOCKED" and self._current_angle_is_bad():
            improved, pitched_lane, pitch_label = self._pitch_up_after_failed_cast(lane)
            self.last_adjustment_note = pitch_label
            lane = pitched_lane
            lane_state = str(lane["lane_state"])
            if improved and lane_state != "BLOCKED":
                self.optimized_angle_key = self._current_angle_key()
                return lane_state
            if (
                not improved
                and pitch_label == "failed_pitch_up_limit"
                and not self.failed_pitch_forward_used
            ):
                _, forwarded_lane, forward_label = self._forward_after_failed_pitch(lane)
                self.last_adjustment_note = forward_label
                lane = forwarded_lane
                lane_state = str(lane["lane_state"])
                self.failed_pitch_forward_used = True

        if (
            lane_state == "RISKY"
            and self._current_angle_key() != self.optimized_angle_key
        ):
            refined, refined_lane, refine_label = self._local_refine_lane(lane)
            if refined:
                lane = refined_lane
                lane_state = str(lane["lane_state"])
                self.last_adjustment_note = refine_label
            self.optimized_angle_key = self._current_angle_key()

        refinement_steps = 0
        while (
            lane_state != "BLOCKED"
            and self._current_angle_key() != self.optimized_angle_key
            and (
                not self._current_angle_is_bad()
                or self._lane_has_viable_water(lane)
                or float(lane.get("center_water_ratio", 0.0)) < self.config.refinement_water_target
            )
            and (
                float(lane.get("residual_obstacle_score", 0.0))
                >= self.config.refinement_obstacle_threshold
                or float(lane.get("center_water_ratio", 0.0)) < self.config.refinement_water_target
                or max(
                    float(lane.get("left_nonwater_ratio", 0.0)),
                    float(lane.get("right_nonwater_ratio", 0.0)),
                ) >= self.config.refinement_obstacle_threshold
            )
            and refinement_steps < max(0, self.config.refinement_max_steps)
        ):
            pitch_delta, yaw_delta, label = self._build_recovery_plan(lane)[0]
            before_score = float(lane["clearance_score"])
            before_residual = float(lane.get("residual_obstacle_score", 0.0))
            before_center_water = float(lane.get("center_water_ratio", 0.0))
            self._adjust_view(pitch_delta, yaw_delta)
            self.last_adjustment_note = f"refine:{label}"
            refined_lane = self._lane_snapshot()
            refined_state = str(refined_lane["lane_state"])
            refined_score = float(refined_lane["clearance_score"])
            refined_residual = float(refined_lane.get("residual_obstacle_score", 0.0))
            refined_center_water = float(refined_lane.get("center_water_ratio", 0.0))

            if (
                refined_state == "BLOCKED"
                or refined_score < before_score
                or refined_residual > before_residual + 0.03
                or (
                    before_center_water < self.config.refinement_water_target
                    and refined_center_water + 0.02 < before_center_water
                )
            ):
                self._revert_last_adjustment(pitch_delta, yaw_delta)
                lane = self._lane_snapshot()
                lane_state = str(lane["lane_state"])
                self.last_adjustment_note = f"refine:{label}->reverted"
                break

            lane = refined_lane
            lane_state = refined_state
            refinement_steps += 1

        while (
            lane_state == "BLOCKED"
            or (self._current_angle_is_bad() and not self._lane_has_viable_water(lane))
        ) and attempts < max(1, self.config.blocked_adjustment_max_steps):
            pitch_delta, yaw_delta, label = self._next_recovery_adjustment(lane)
            self._adjust_view(pitch_delta, yaw_delta)
            self.last_adjustment_note = label
            lane = self._lane_snapshot()
            lane_state = str(lane["lane_state"])
            if start_lane_state != "BLOCKED" and lane_state == "BLOCKED":
                self._revert_last_adjustment(pitch_delta, yaw_delta)
                lane = self._lane_snapshot()
                lane_state = str(lane["lane_state"])
                self.last_adjustment_note = f"{label}->reverted"
                break
            attempts += 1

        if lane_state == "BLOCKED" or self._current_angle_is_bad():
            self.last_adjustment_note = f"blocked_after_{attempts}_adjustments"
        elif attempts > 0:
            self.blocked_streak = 0
            self.last_adjustment_note = f"{self.last_adjustment_note}->ready:{lane_state}"

        return lane_state

    def _cast_once(self, button: str, require_ready_lane: bool = True) -> bool:
        if require_ready_lane:
            lane_state = self._ensure_cast_lane_ready()
            if lane_state == "BLOCKED":
                return False
        self._click(button)
        self.rod_casted = True
        self.cast_timestamps.append(time.time())
        return True

    def _reel_once(self, button: str) -> None:
        self._click(button)
        self.rod_casted = False

    def _recast(self, button: str, require_ready_lane: bool = True) -> bool:
        self._reel_once(button)
        time.sleep(self.config.recast_delay_sec)
        return self._cast_once(button, require_ready_lane=require_ready_lane)

    def _sync_state_from_text(self, normalized_text: str) -> None:
        cast_kw = self._normalize(self.config.cast_keyword)
        reel_kw = self._normalize(self.config.reel_keyword)
        if cast_kw and cast_kw in normalized_text:
            self.rod_casted = True
        if reel_kw and reel_kw in normalized_text:
            self.rod_casted = False

    def _resolve_action(self, matched_keyword: str) -> str:
        action = self.config.keyword_actions.get(matched_keyword)
        if not action:
            return "click"
        return action

    def _stats_snapshot(self) -> tuple[int, float, float]:
        cast_count = len(self.cast_timestamps)
        runtime = max(0.0, time.time() - self.started_at)
        if cast_count < 2:
            avg_cast_interval = 0.0
        else:
            intervals = [
                self.cast_timestamps[i] - self.cast_timestamps[i - 1]
                for i in range(1, cast_count)
            ]
            avg_cast_interval = sum(intervals) / len(intervals)
        return cast_count, avg_cast_interval, runtime

    def _emit_stats(self, final: bool = False) -> None:
        cast_count, avg_cast_interval, runtime = self._stats_snapshot()
        tag = "STATS-FINAL" if final else "STATS"
        msg = (
            f"[{tag}] casts={cast_count} avg_cast_interval_sec={avg_cast_interval:.2f} "
            f"runtime_sec={runtime:.2f}"
        )
        print(msg)

        if self.config.stats_log_file:
            log_path = Path(self.config.stats_log_file)
            if not log_path.is_absolute():
                log_path = Path.cwd() / log_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")

    def _touch_bite_presence(self, normalized_text: str) -> None:
        for kw in self.config.bite_presence_keywords:
            target = kw if self.config.case_sensitive else kw.lower()
            if target and target in normalized_text:
                self.last_bite_seen_at = time.time()
                self._reset_recovery_progress("bite_seen")
                return

    def _reset_recovery_progress(self, reason: str) -> None:
        self.recovery_retry_count = 0
        self.recovery_ladder_index = 0
        self.recovery_plan = []
        self.recovery_offsets = {"pitch": 0, "yaw": 0}
        self.blocked_streak = 0
        self.last_adjustment_note = f"reset:{reason}"
        self.angle_failure_counts[self._current_angle_key()] = 0
        if reason != "bite_seen":
            self.optimized_angle_key = None
        self.failed_pitch_forward_used = False

    def _current_angle_key(self) -> tuple[int, int]:
        return (self.recovery_offsets["pitch"], self.recovery_offsets["yaw"])

    def _mark_current_angle_failure(self) -> None:
        key = self._current_angle_key()
        self.angle_failure_counts[key] = self.angle_failure_counts.get(key, 0) + 1
        self.optimized_angle_key = None
        self.failed_pitch_forward_used = False

    def _current_angle_is_bad(self) -> bool:
        key = self._current_angle_key()
        return self.angle_failure_counts.get(key, 0) >= self.config.bad_angle_failure_threshold

    def _build_recovery_plan(
        self, lane: Optional[dict[str, float | str]] = None
    ) -> list[tuple[int, int, str]]:
        lane = lane or {}
        preferred_move = self._preferred_move_name(lane)
        move_scores = [
            ("pitch_up", float(lane.get("move_pitch_up_score", 0.0))),
            ("pitch_down", float(lane.get("move_pitch_down_score", 0.0))),
            ("yaw_left", float(lane.get("move_yaw_left_score", 0.0))),
            ("yaw_right", float(lane.get("move_yaw_right_score", 0.0))),
        ]
        move_scores.sort(
            key=lambda item: (
                0 if item[0] == preferred_move else 1,
                -item[1],
            )
        )

        move_to_steps = {
            "pitch_up": [(2, 0, "probe_pitch+big"), (1, 0, "follow_pitch+small")],
            "pitch_down": [(-2, 0, "probe_pitch-big"), (-1, 0, "follow_pitch-small")],
            "yaw_left": [(0, -2, "probe_yaw_left_big"), (0, -1, "follow_yaw_left_small")],
            "yaw_right": [(0, 2, "probe_yaw_right_big"), (0, 1, "follow_yaw_right_small")],
        }

        primary: list[tuple[int, int, str]] = []
        for move_name, _ in move_scores:
            primary.extend(move_to_steps[move_name])

        fallback = [
            (-self.recovery_offsets["pitch"], 0, "reset_pitch"),
            (0, -self.recovery_offsets["yaw"], "reset_yaw"),
            (1, 0, "fallback_pitch_up"),
            (0, 1, "fallback_yaw_right"),
            (0, -1, "fallback_yaw_left"),
        ]
        return primary + fallback

    def _best_move_score(self, lane: Optional[dict[str, float | str]] = None) -> float:
        lane = lane or {}
        return max(
            float(lane.get("move_pitch_up_score", 0.0)),
            float(lane.get("move_pitch_down_score", 0.0)),
            float(lane.get("move_yaw_left_score", 0.0)),
            float(lane.get("move_yaw_right_score", 0.0)),
        )

    def _lane_quality(self, lane: Optional[dict[str, float | str]] = None) -> float:
        lane = lane or {}
        return (
            float(lane.get("clearance_score", 0.0))
            + 0.35 * float(lane.get("center_water_ratio", 0.0))
            - 0.25 * float(lane.get("residual_obstacle_score", 0.0))
        )

    def _lane_water_preference(self, lane: Optional[dict[str, float | str]] = None) -> float:
        lane = lane or {}
        directional_water = max(
            float(lane.get("upper_water_ratio", 0.0)),
            float(lane.get("lower_water_ratio", 0.0)),
            float(lane.get("left_water_ratio", 0.0)),
            float(lane.get("right_water_ratio", 0.0)),
        )
        return (
            float(lane.get("center_water_ratio", 0.0))
            + 0.40 * directional_water
            - 0.20 * float(lane.get("residual_obstacle_score", 0.0))
        )

    def _should_force_pitch_up(self, lane: Optional[dict[str, float | str]] = None) -> bool:
        lane = lane or {}
        lower_nonwater_ratio = float(lane.get("lower_nonwater_ratio", 0.0))
        upper_water_ratio = float(lane.get("upper_water_ratio", 0.0))
        upper_open_ratio = float(lane.get("upper_open_ratio", 0.0))
        return lower_nonwater_ratio >= 0.42 and (
            upper_water_ratio >= 0.32 or upper_open_ratio >= 0.58
        )

    def _preferred_move_name(self, lane: Optional[dict[str, float | str]] = None) -> str:
        lane = lane or {}
        left_nonwater_ratio = float(lane.get("left_nonwater_ratio", 0.0))
        right_nonwater_ratio = float(lane.get("right_nonwater_ratio", 0.0))
        upper_water_ratio = float(lane.get("upper_water_ratio", 0.0))
        upper_open_ratio = float(lane.get("upper_open_ratio", 0.0))
        move_scores = [
            ("pitch_up", float(lane.get("move_pitch_up_score", 0.0))),
            ("pitch_down", float(lane.get("move_pitch_down_score", 0.0))),
            (
                "yaw_left",
                float(lane.get("move_yaw_left_score", 0.0))
                - 0.45 * left_nonwater_ratio
                - 0.20 * max(0.0, left_nonwater_ratio - right_nonwater_ratio),
            ),
            (
                "yaw_right",
                float(lane.get("move_yaw_right_score", 0.0))
                - 0.45 * right_nonwater_ratio
                - 0.20 * max(0.0, right_nonwater_ratio - left_nonwater_ratio),
            ),
        ]
        if self._should_force_pitch_up(lane):
            return "pitch_up"
        if left_nonwater_ratio >= 0.40 and left_nonwater_ratio >= right_nonwater_ratio + 0.08:
            return "pitch_up" if upper_water_ratio >= 0.28 or upper_open_ratio >= 0.52 else "yaw_right"
        if right_nonwater_ratio >= 0.40 and right_nonwater_ratio >= left_nonwater_ratio + 0.08:
            return "pitch_up" if upper_water_ratio >= 0.28 or upper_open_ratio >= 0.52 else "yaw_left"
        move_scores.sort(key=lambda item: item[1], reverse=True)
        return move_scores[0][0]

    def _local_refine_lane(
        self, base_lane: Optional[dict[str, float | str]] = None
    ) -> tuple[bool, dict[str, float | str], str]:
        base_lane = base_lane or self._lane_snapshot()
        if str(base_lane.get("lane_state", "BLOCKED")) == "BLOCKED":
            return False, base_lane, "local_refine_skip_blocked"

        lane_state = str(base_lane.get("lane_state", "BLOCKED"))
        if lane_state == "CLEAR":
            return False, base_lane, "local_refine_skip_clear"

        if lane_state == "RISKY":
            left_nonwater_ratio = float(base_lane.get("left_nonwater_ratio", 0.0))
            right_nonwater_ratio = float(base_lane.get("right_nonwater_ratio", 0.0))
            if left_nonwater_ratio > right_nonwater_ratio:
                move_name = "yaw_right"
            elif right_nonwater_ratio > left_nonwater_ratio:
                move_name = "yaw_left"
            else:
                yaw_left_score = float(base_lane.get("move_yaw_left_score", 0.0))
                yaw_right_score = float(base_lane.get("move_yaw_right_score", 0.0))
                move_name = "yaw_left" if yaw_left_score >= yaw_right_score else "yaw_right"
        else:
            move_name = self._preferred_move_name(base_lane)
        direction_map = {
            "pitch_up": (1, 0),
            "pitch_down": (-1, 0),
            "yaw_left": (0, -1),
            "yaw_right": (0, 1),
        }
        pitch_unit, yaw_unit = direction_map[move_name]
        best_lane = base_lane
        best_quality = self._lane_quality(base_lane)
        best_offset = 0
        moved_pitch = 0
        moved_yaw = 0

        for magnitude in range(1, max(1, self.config.local_refine_max_steps) + 1):
            self._adjust_view(pitch_unit, yaw_unit)
            moved_pitch += pitch_unit
            moved_yaw += yaw_unit
            lane = self._lane_snapshot()
            if str(lane["lane_state"]) == "BLOCKED":
                break
            quality = self._lane_quality(lane)
            if quality > best_quality + 0.01:
                best_quality = quality
                best_lane = lane
                best_offset = magnitude

        if best_offset == 0:
            if moved_pitch or moved_yaw:
                self._adjust_view(-moved_pitch, -moved_yaw)
            return False, self._lane_snapshot(), f"local_refine_{move_name}_none"

        target_pitch = pitch_unit * best_offset
        target_yaw = yaw_unit * best_offset
        if moved_pitch != target_pitch or moved_yaw != target_yaw:
            self._adjust_view(target_pitch - moved_pitch, target_yaw - moved_yaw)
        return True, self._lane_snapshot(), f"local_refine_{move_name}_{best_offset}"

    def _pitch_up_after_failed_cast(
        self, base_lane: Optional[dict[str, float | str]] = None
    ) -> tuple[bool, dict[str, float | str], str]:
        base_lane = base_lane or self._lane_snapshot()
        if str(base_lane.get("lane_state", "BLOCKED")) == "BLOCKED":
            return False, base_lane, "failed_pitch_skip_blocked"
        current_pitch_offset = self.recovery_offsets["pitch"]
        if current_pitch_offset >= max(1, self.config.failed_cast_pitch_up_steps):
            return False, base_lane, "failed_pitch_up_limit"

        self._adjust_view(1, 0)
        lane = self._lane_snapshot()
        lane_state = str(lane["lane_state"])
        if lane_state == "BLOCKED":
            self._revert_last_adjustment(1, 0)
            return False, self._lane_snapshot(), "failed_pitch_up_blocked_revert"

        return True, lane, f"failed_pitch_up_step_{self.recovery_offsets['pitch']}"

    def _forward_after_failed_pitch(
        self, base_lane: Optional[dict[str, float | str]] = None
    ) -> tuple[bool, dict[str, float | str], str]:
        base_lane = base_lane or self._lane_snapshot()
        if self.rod_casted:
            button = "left" if self.config.default_button == "left" else "right"
            self._reel_once(button)
            time.sleep(max(0.05, self.config.recast_delay_sec))
        hold_sec = max(0.05, self.config.failed_cast_forward_sec)
        pyautogui.keyDown("w")
        try:
            time.sleep(hold_sec)
        finally:
            pyautogui.keyUp("w")
        time.sleep(max(0.0, self.config.adjustment_settle_sec))
        return True, self._lane_snapshot(), f"failed_forward_w_{hold_sec:.2f}s"

    def _collect_probe_screenshots(self) -> tuple[list[np.ndarray], int]:
        """
        Rotate 360° in equal steps, capture a full-window screenshot at each position.
        Returns (screenshots, total_pixels_moved_right) so caller can undo the rotation.
        """
        count = self.config.vision_probe_count
        step = self.config.vision_probe_yaw_pixels
        screenshots: list[np.ndarray] = []
        total_moved = 0

        for i in range(count):
            region = self._window_region()
            img = self._capture_region(region)
            screenshots.append(img)
            if i < count - 1:
                pyautogui.moveRel(step, 0, duration=0)
                total_moved += step
                time.sleep(max(0.05, self.config.adjustment_settle_sec))

        return screenshots, total_moved

    def _ollama_unified_scan(
        self, base_lane: Optional[dict[str, float | str]] = None
    ) -> tuple[bool, dict[str, float | str], str]:
        """
        Single 360° LLM probe that handles both casting and walking decisions.
        Per direction: CLEAR/BLOCKED + WATER/NO_WATER.
        Priority:
          score 3 (CLEAR+WATER)  → face it, cast immediately (no walk needed)
          score 2 (BLOCKED+WATER) → face it, walk toward water
          score 1 (CLEAR+NO_WATER) → face it, try casting anyway
          score 0 → skip
        """
        from .vision import ask_single_probe
        import cv2 as _cv2

        if not self.config.vision_enabled:
            return False, base_lane or self._lane_snapshot(), "ollama_disabled"

        count = self.config.vision_probe_count
        step = self.config.vision_probe_yaw_pixels
        results: list[tuple[bool, bool, int]] = []  # (clear, water, score)
        total_moved = 0
        early_exit = False

        if self.config.debug_window:
            import os
            os.makedirs("logs/probes", exist_ok=True)

        print("[VISION] unified 360° probe...")
        for i in range(count):
            region = self._window_region()
            img = self._capture_region(region)

            if self.config.debug_window:
                labeled = img.copy()
                lbl = str(i + 1)
                _cv2.putText(labeled, lbl, (8, 36), _cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 4, _cv2.LINE_AA)
                _cv2.putText(labeled, lbl, (8, 36), _cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2, _cv2.LINE_AA)
                _cv2.imwrite(f"logs/probes/probe_{i + 1}.jpg", labeled)

            clear, water = ask_single_probe(
                img, i + 1,
                model=self.config.vision_model,
                host=self.config.vision_host,
            )
            score = (2 if clear else 0) + (1 if water else 0)
            results.append((clear, water, score))
            print(f"[VISION] probe {i + 1}: {'CLEAR' if clear else 'BLOCKED'}, {'WATER' if water else 'NO_WATER'} (score={score})")

            # Best case: can cast here right now
            if clear and water:
                print(f"[VISION] probe {i + 1}: CLEAR+WATER, casting from here")
                early_exit = True
                break

            if i < count - 1:
                pyautogui.moveRel(step, 0, duration=0)
                total_moved += step
                time.sleep(max(0.05, self.config.adjustment_settle_sec))
        else:
            early_exit = False

        best_score = max(r[2] for r in results)
        if best_score == 0:
            print("[VISION] no usable direction found")
            if total_moved > 0:
                pyautogui.moveRel(-total_moved, 0, duration=0)
                time.sleep(max(0.05, self.config.adjustment_settle_sec))
            return False, base_lane or self._lane_snapshot(), "ollama_all_blocked"

        best_idx = next(i for i, r in enumerate(results) if r[2] == best_score)
        best_clear, best_water, _ = results[best_idx]

        if not early_exit:
            # Return to origin then rotate to best direction
            if total_moved > 0:
                pyautogui.moveRel(-total_moved, 0, duration=0)
                time.sleep(max(0.05, self.config.adjustment_settle_sec))
            target_pixels = best_idx * step
            if target_pixels != 0:
                pyautogui.moveRel(target_pixels, 0, duration=0)
                time.sleep(max(0.05, self.config.adjustment_settle_sec))
        # else: already facing best direction

        # CLEAR+WATER or CLEAR+NO_WATER → just cast, no walking
        if best_clear:
            lane = self._lane_snapshot()
            label = f"ollama_cast_probe_{best_idx + 1}of{count}"
            print(f"[VISION] facing probe {best_idx + 1}, ready to cast")
            return True, lane, label

        # BLOCKED+WATER → need to walk toward water
        pre_walk_lane = self._lane_snapshot()
        pre_water_score = self._lane_water_preference(pre_walk_lane)
        pre_clearance = float(pre_walk_lane["clearance_score"])
        print(f"[VISION] walking toward probe {best_idx + 1} (pre_water={pre_water_score:.2f})...")

        if self.rod_casted:
            button = self.config.default_button
            self._reel_once(button)
            time.sleep(max(0.05, self.config.recast_delay_sec))

        hold_sec = max(0.1, self.config.vision_walk_forward_sec)
        pyautogui.keyDown("w")
        try:
            time.sleep(hold_sec)
        finally:
            pyautogui.keyUp("w")
        time.sleep(max(0.0, self.config.adjustment_settle_sec))

        lane = self._lane_snapshot()
        post_state = str(lane["lane_state"])
        post_clearance = float(lane["clearance_score"])
        post_water_score = self._lane_water_preference(lane)

        # Extra pushes if LLM confirmed water but CV still BLOCKED
        if post_state == "BLOCKED":
            for push in range(self.config.vision_extra_push_steps):
                print(f"[VISION] still BLOCKED, pushing forward (step {push + 1}/{self.config.vision_extra_push_steps})...")
                pyautogui.keyDown("w")
                try:
                    time.sleep(hold_sec)
                finally:
                    pyautogui.keyUp("w")
                time.sleep(max(0.0, self.config.adjustment_settle_sec))
                lane = self._lane_snapshot()
                post_state = str(lane["lane_state"])
                post_clearance = float(lane["clearance_score"])
                post_water_score = self._lane_water_preference(lane)
                print(f"[VISION] push {push + 1}: {post_state} clearance={post_clearance:.2f}")
                if post_state != "BLOCKED":
                    break

        water_improved = post_water_score > pre_water_score + 0.04
        clearance_improved = post_clearance > pre_clearance + 0.04
        moved = post_state != "BLOCKED" or water_improved or clearance_improved or best_water

        label = (
            f"ollama_walk_{best_idx + 1}of{count}"
            f"_fwd{hold_sec:.1f}s"
            f"_water{pre_water_score:.2f}->{post_water_score:.2f}"
        )
        print(f"[VISION] after walk: {post_state} clearance={post_clearance:.2f} water={post_water_score:.2f} moved={moved}")
        return moved, lane, label

    def _scan_for_open_lane(
        self, base_lane: Optional[dict[str, float | str]] = None
    ) -> tuple[bool, dict[str, float | str], str]:
        base_lane = base_lane or self._lane_snapshot()
        base_score = float(base_lane["clearance_score"])
        best_lane = base_lane
        best_score = base_score
        best_adjustment = (0, 0, "scan_none")

        # Broad blocked-lane scan: pick the more promising side first, then
        # sweep continuously in that direction before trying the opposite side.
        yaw_left_score = float(base_lane.get("move_yaw_left_score", 0.0))
        yaw_right_score = float(base_lane.get("move_yaw_right_score", 0.0))
        yaw_sign = -1 if yaw_left_score >= yaw_right_score else 1

        pitch_up_score = float(base_lane.get("move_pitch_up_score", 0.0))
        pitch_down_score = float(base_lane.get("move_pitch_down_score", 0.0))
        pitch_signs = [1, -1] if pitch_up_score >= pitch_down_score else [-1, 1]

        yaw_base_unit = 4
        pitch_base_unit = 3

        if self._should_force_pitch_up(base_lane):
            pitch_phase_result = self._scan_pitch_for_open_lane(
                base_lane,
                base_score,
                best_score,
                best_adjustment,
                pitch_signs,
                pitch_base_unit,
            )
            if pitch_phase_result is not None:
                return pitch_phase_result

        moved_yaw_total = 0
        for magnitude in range(1, max(1, self.config.blocked_scan_yaw_steps) + 1):
            yaw_steps = yaw_sign * yaw_base_unit
            self._adjust_view(0, yaw_steps)
            moved_yaw_total += yaw_steps
            lane = self._lane_snapshot()
            score = float(lane["clearance_score"])
            lane_state = str(lane["lane_state"])
            if lane_state == "CLEAR":
                return True, lane, f"scan_yaw_{'left' if yaw_sign < 0 else 'right'}_{magnitude}_clear"
            if lane_state == "RISKY":
                return True, lane, f"scan_yaw_{'left' if yaw_sign < 0 else 'right'}_{magnitude}_risky"
            if score > best_score:
                best_lane = lane
                best_score = score
                best_adjustment = (
                    0,
                    moved_yaw_total,
                    f"scan_yaw_{'left' if yaw_sign < 0 else 'right'}_{magnitude}",
                )

        if best_score >= base_score + self.config.blocked_probe_min_improvement:
            best_pitch_steps, best_yaw_steps, label = best_adjustment
            if moved_yaw_total != best_yaw_steps:
                self._adjust_view(0, best_yaw_steps - moved_yaw_total)
            lane = self._lane_snapshot()
            return True, lane, label

        if moved_yaw_total != 0:
            self._adjust_view(0, -moved_yaw_total)

        pitch_phase_result = self._scan_pitch_for_open_lane(
            best_lane,
            base_score,
            best_score,
            best_adjustment,
            pitch_signs,
            pitch_base_unit,
        )
        if pitch_phase_result is not None:
            return pitch_phase_result

        return False, best_lane, "scan_no_improvement"

    def _scan_pitch_for_open_lane(
        self,
        best_lane: dict[str, float | str],
        base_score: float,
        best_score: float,
        best_adjustment: tuple[int, int, str],
        pitch_signs: list[int],
        pitch_base_unit: int,
    ) -> Optional[tuple[bool, dict[str, float | str], str]]:
        current_best_lane = best_lane
        current_best_score = best_score
        current_best_adjustment = best_adjustment

        for sign in pitch_signs:
            moved_pitch_total = 0
            for magnitude in range(1, max(1, self.config.blocked_scan_pitch_steps) + 1):
                pitch_steps = sign * pitch_base_unit
                self._adjust_view(pitch_steps, 0)
                moved_pitch_total += pitch_steps
                lane = self._lane_snapshot()
                score = float(lane["clearance_score"])
                lane_state = str(lane["lane_state"])
                if lane_state == "CLEAR":
                    return True, lane, f"scan_pitch_{'up' if sign > 0 else 'down'}_{magnitude}_clear"
                if lane_state == "RISKY":
                    return True, lane, f"scan_pitch_{'up' if sign > 0 else 'down'}_{magnitude}_risky"
                if score > current_best_score:
                    current_best_lane = lane
                    current_best_score = score
                    current_best_adjustment = (
                        moved_pitch_total,
                        0,
                        f"scan_pitch_{'up' if sign > 0 else 'down'}_{magnitude}",
                    )
            if current_best_score >= base_score + self.config.blocked_probe_min_improvement:
                best_pitch_steps, _, label = current_best_adjustment
                if moved_pitch_total != best_pitch_steps:
                    self._adjust_view(best_pitch_steps - moved_pitch_total, 0)
                lane = self._lane_snapshot()
                return True, lane, label
            if moved_pitch_total != 0:
                self._adjust_view(-moved_pitch_total, 0)
        return None

    def _scan_for_most_water_and_forward(
        self, base_lane: Optional[dict[str, float | str]] = None
    ) -> tuple[bool, dict[str, float | str], str]:
        """
        Single-pass yaw sweep + drift-corrected walk toward the most-water direction.

        Phase 1 – continuous sweep (leftmost → rightmost, no back-and-forth):
          only 2 large moves, minimising accumulated reset drift.

        Phase 2 – verify + fine-tune:
          re-measure score at the target direction after rotating.
          If actual ≥ 70 % of expected → close enough, walk.
          Otherwise scan ±FINE_RANGE small steps to correct drift and
          stop at the first position that meets the threshold.
        """
        MATCH_RATIO = 0.80  # accept direction if actual/expected >= this

        base_lane = base_lane or self._lane_snapshot()
        best_lane = base_lane
        best_water_score = self._lane_water_preference(base_lane)

        yaw_base_unit = 4
        half_steps = max(1, self.config.blocked_scan_yaw_steps)

        # ── Phase 1: continuous sweep leftmost → rightmost ────────────────────
        self._adjust_view(0, -half_steps * yaw_base_unit)
        current_yaw = -half_steps * yaw_base_unit
        best_yaw = current_yaw

        lane = self._lane_snapshot()
        score = self._lane_water_preference(lane)
        if score > best_water_score:
            best_water_score, best_lane, best_yaw = score, lane, current_yaw

        for _ in range(half_steps * 2):
            self._adjust_view(0, yaw_base_unit)
            current_yaw += yaw_base_unit
            lane = self._lane_snapshot()
            score = self._lane_water_preference(lane)
            if score > best_water_score:
                best_water_score, best_lane, best_yaw = score, lane, current_yaw

        # Single correction: current (rightmost) → best
        delta_yaw = best_yaw - current_yaw
        if delta_yaw != 0:
            self._adjust_view(0, delta_yaw)

        direction = "left" if best_yaw < 0 else ("right" if best_yaw > 0 else "base")
        label = f"water_yaw_{direction}_off{best_yaw}"
        print(f"[WATER-SCAN] sweep best={best_water_score:.2f} at offset={best_yaw}")

        # ── Phase 2: verify and fine-tune if rotation drifted ────────────────
        verify_lane = self._lane_snapshot()
        actual_score = self._lane_water_preference(verify_lane)
        threshold = best_water_score * MATCH_RATIO
        print(f"[WATER-SCAN] verify actual={actual_score:.2f} threshold={threshold:.2f}")

        if actual_score < threshold and best_water_score > 0.05:
            # Keep sweeping left one step at a time until score meets threshold.
            # The sweep already went left→right, so drifting right is the likely
            # error; continuing left corrects it. Stop at first match.
            fine_best_score = actual_score
            fine_best_lane  = verify_lane
            fine_steps      = 0
            found           = False
            max_fine        = half_steps * 2  # don't search more than the full sweep width

            for _ in range(max_fine):
                self._adjust_view(0, -yaw_base_unit)
                fine_steps += 1
                lane = self._lane_snapshot()
                s = self._lane_water_preference(lane)
                print(f"[WATER-SCAN] fine left step={fine_steps} score={s:.2f}")
                if s > fine_best_score:
                    fine_best_score, fine_best_lane = s, lane
                if s >= threshold:
                    found = True
                    break

            best_lane = fine_best_lane
            label += f"_fine-{fine_steps}({'ok' if found else 'approx'})"
            print(f"[WATER-SCAN] fine done score={fine_best_score:.2f} found={found}")

        moved, lane, forward_label = self._forward_after_failed_pitch(best_lane)
        return moved, lane, f"{label}->{forward_label}"

    def _retry_limit_for_lane(self, lane_state: str) -> int:
        if lane_state == "CLEAR":
            return max(0, self.config.clear_retry_limit)
        if lane_state == "RISKY":
            return max(0, self.config.risky_retry_limit)
        return max(0, self.config.blocked_retry_limit)

    def _smart_recover_probe(self, button: str) -> str:
        probe_click_at = time.time()
        self._click(button)
        time.sleep(max(0.05, self.config.smart_recover_probe_wait_sec))

        probe_text = self.capture_text()
        if self.config.print_ocr_text:
            print(f"[OCR-PROBE] {probe_text}")
        if probe_text.strip():
            self.last_nonempty_ocr_at = time.time()

        probe_normalized = self._normalize(probe_text)
        self._sync_state_from_text(probe_normalized)
        self._touch_bite_presence(probe_normalized)

        cast_kw = self._normalize(self.config.cast_keyword)
        reel_kw = self._normalize(self.config.reel_keyword)
        cast_hit = cast_kw and cast_kw in probe_normalized
        reel_hit = reel_kw and reel_kw in probe_normalized

        if reel_hit:
            self._cast_once(button)
            return "smart_recover(retrieved_then_cast)"
        if cast_hit:
            self.rod_casted = True
            self.cast_timestamps.append(probe_click_at)
            return "smart_recover(thrown_ok)"
        if not self.rod_casted:
            self._cast_once(button)
            return "smart_recover(unknown_then_cast)"
        return "smart_recover(unknown_keep)"

    def _adjust_view(self, pitch_steps: int = 0, yaw_steps: int = 0) -> None:
        dx = yaw_steps * self.config.adjustment_yaw_step_pixels
        dy = -pitch_steps * self.config.adjustment_pitch_step_pixels
        if dx == 0 and dy == 0:
            return
        pyautogui.moveRel(dx, dy, duration=0)
        self.recovery_offsets["pitch"] += pitch_steps
        self.recovery_offsets["yaw"] += yaw_steps
        time.sleep(max(0.0, self.config.adjustment_settle_sec))

    def _revert_last_adjustment(self, pitch_steps: int, yaw_steps: int) -> None:
        self._adjust_view(-pitch_steps, -yaw_steps)
        self.last_adjustment_note = f"revert({pitch_steps},{yaw_steps})"

    def _next_recovery_adjustment(
        self, lane: Optional[dict[str, float | str]] = None
    ) -> tuple[int, int, str]:
        if not self.recovery_plan or self.recovery_ladder_index >= len(self.recovery_plan):
            self.recovery_plan = self._build_recovery_plan(lane)
            self.recovery_ladder_index = 0

        if self.recovery_ladder_index < len(self.recovery_plan):
            pitch_delta, yaw_delta, label = self.recovery_plan[self.recovery_ladder_index]
            self.recovery_ladder_index += 1
            return pitch_delta, yaw_delta, label

        reset_pitch = -self.recovery_offsets["pitch"]
        reset_yaw = -self.recovery_offsets["yaw"]
        self.recovery_ladder_index = 0
        self.recovery_plan = []
        return reset_pitch, reset_yaw, "reset_to_base"

    def _cast_for_recovery(self, button: str) -> bool:
        if self.rod_casted:
            return self._recast(button, require_ready_lane=True)
        return self._cast_once(button, require_ready_lane=True)

    def _run_recover_action(self, action: str, reason_tag: str) -> None:
        button = "left" if self.config.default_button == "left" else "right"

        if action == "recast":
            if self._recast(button, require_ready_lane=True):
                recover_note = "recast"
            else:
                recover_note = "recast(blocked_no_cast)"
        elif action == "smart_recover":
            lane = self._lane_snapshot()
            lane_state = str(lane["lane_state"])
            lane_score = float(lane["clearance_score"])
            retry_limit = self._retry_limit_for_lane(lane_state)
            is_clear_timeout = (
                reason_tag in ("no_bite_timeout_no_cast", "no_bite_timeout_casted")
                and lane_state == "CLEAR"
            )
            if not is_clear_timeout:
                self.clear_casted_timeout_streak = 0

            # Keep good lanes stable: for timeout-based recovery on CLEAR view,
            # first decide whether timeout happened with/without a cast.
            if reason_tag in ("no_bite_timeout_no_cast", "no_bite_timeout_casted") and lane_state == "CLEAR":
                self.clear_casted_timeout_streak += 1
                recast_limit = max(0, self.config.clear_casted_timeout_recast_limit)
                if self.clear_casted_timeout_streak <= recast_limit:
                    casted = self._cast_for_recovery(button)
                    recover_note = (
                        "smart_recover(clear_timeout_recast,"
                        f"reason={reason_tag},"
                        f"state={lane_state}:{lane_score:.2f},"
                        f"streak={self.clear_casted_timeout_streak}/{recast_limit},"
                        f"casted={casted})"
                    )
                else:
                    improved, pitched_lane, pitch_label = self._pitch_up_after_failed_cast(lane)
                    moved = False
                    moved_lane = pitched_lane
                    move_label = "skip_forward"
                    if not improved:
                        moved, moved_lane, move_label = self._forward_after_failed_pitch(
                            pitched_lane
                        )
                    casted = self._cast_for_recovery(button)
                    final_lane = self._lane_snapshot()
                    final_state = str(final_lane["lane_state"])
                    final_score = float(final_lane["clearance_score"])
                    recover_note = (
                        "smart_recover(clear_timeout_escalate,"
                        f"reason={reason_tag},"
                        f"state={lane_state}:{lane_score:.2f},"
                        f"streak={self.clear_casted_timeout_streak},"
                        f"pitch={improved}:{pitch_label},"
                        f"forward={moved}:{move_label},"
                        f"final={final_state}:{final_score:.2f},"
                        f"casted={casted})"
                    )
                    self.clear_casted_timeout_streak = 0
                self.recovery_retry_count = 0
                print(f"[RECOVER] {reason_tag} action={recover_note}")
                return

            if lane_state == "BLOCKED":
                ready_lane = self._ensure_cast_lane_ready()
                lane = self._lane_snapshot()
                lane_state = str(lane["lane_state"])
                lane_score = float(lane["clearance_score"])
                if ready_lane == "BLOCKED":
                    recover_note = (
                        "smart_recover(blocked_gate,"
                        f"state={lane_state}:{lane_score:.2f})"
                    )
                    print(f"[RECOVER] {reason_tag} action={recover_note}")
                    return

            if self.recovery_retry_count < retry_limit:
                self.recovery_retry_count += 1
                recover_note = (
                    f"{self._smart_recover_probe(button)} lane={lane_state} "
                    f"retry={self.recovery_retry_count}/{retry_limit}"
                )
            else:
                pitch_delta, yaw_delta, label = self._next_recovery_adjustment(lane)
                self._adjust_view(pitch_delta, yaw_delta)
                updated_lane = self._lane_snapshot()
                self.recovery_retry_count = 0
                casted = self._cast_for_recovery(button)
                post_lane_state = str(updated_lane["lane_state"])
                post_lane_score = float(updated_lane["clearance_score"])

                # If timeout recovery keeps us in RISKY, nudge forward once and retry cast.
                if (
                    reason_tag in ("no_bite_timeout", "no_bite_timeout_casted")
                    and lane_state == "RISKY"
                    and post_lane_state == "RISKY"
                ):
                    moved, moved_lane, move_label = self._forward_after_failed_pitch(updated_lane)
                    moved_lane_state = str(moved_lane["lane_state"])
                    moved_lane_score = float(moved_lane["clearance_score"])
                    casted_after_move = self._cast_for_recovery(button)
                    recover_note = (
                        "smart_recover(adjust="
                        f"{label},before={lane_state}:{lane_score:.2f},"
                        f"after={post_lane_state}:{post_lane_score:.2f},"
                        f"risky_forward={moved}:{move_label},"
                        f"after_forward={moved_lane_state}:{moved_lane_score:.2f},"
                        f"casted={casted},casted_after_forward={casted_after_move})"
                    )
                else:
                    recover_note = (
                        "smart_recover(adjust="
                        f"{label},before={lane_state}:{lane_score:.2f},"
                        f"after={post_lane_state}:{post_lane_score:.2f},"
                        f"casted={casted})"
                    )
                self.last_adjustment_note = label
        elif action == "cast_if_idle":
            if not self.rod_casted:
                casted = self._cast_once(button, require_ready_lane=True)
                recover_note = "cast_if_idle(casted)" if casted else "cast_if_idle(blocked)"
            else:
                recover_note = "cast_if_idle(skip_already_casted)"
        else:
            self._click(button)
            recover_note = "click"

        print(f"[RECOVER] {reason_tag} action={recover_note}")

    def _handle_no_bite_timeout(self) -> None:
        timeout = self.config.no_bite_timeout_sec
        if timeout is None or timeout <= 0:
            return

        now = time.time()
        if now - self.last_bite_seen_at < timeout:
            return

        cooldown = max(0.0, self.config.no_bite_recover_cooldown_sec)
        if now - self.last_no_bite_recover_at < cooldown:
            return

        if self.rod_casted:
            self._mark_current_angle_failure()
            self._run_recover_action(self.config.no_bite_timeout_action, "no_bite_timeout_casted")
        else:
            self._run_recover_action(self.config.no_bite_timeout_action, "no_bite_timeout_no_cast")
        self.last_no_bite_recover_at = now
        self.last_bite_seen_at = now

    def _handle_ocr_empty_timeout(self) -> None:
        timeout = self.config.ocr_empty_timeout_sec
        if timeout is None or timeout <= 0:
            return

        now = time.time()
        if now - self.last_nonempty_ocr_at < timeout:
            return

        cooldown = max(0.0, self.config.ocr_empty_recover_cooldown_sec)
        if now - self.last_ocr_empty_recover_at < cooldown:
            return

        self._run_recover_action(self.config.ocr_empty_timeout_action, "ocr_empty_timeout")
        self.last_ocr_empty_recover_at = now
        self.last_nonempty_ocr_at = now

    def step(self) -> TriggerResult:
        text = self.capture_text()
        if self.config.print_ocr_text:
            print(f"[OCR] {text}")
        self.last_ocr_text = text
        self._show_debug_window(text)

        if text.strip():
            self.last_nonempty_ocr_at = time.time()

        normalized_text = self._normalize(text)
        self._sync_state_from_text(normalized_text)
        self._touch_bite_presence(normalized_text)
        if not text.strip():
            lane = self._lane_snapshot()
            self.last_lane_state = str(lane["lane_state"])
            self.last_lane_score = float(lane["clearance_score"])

        for kw in self.config.keywords:
            target = kw if self.config.case_sensitive else kw.lower()
            if target not in normalized_text:
                continue

            now = time.time()
            if now - self.last_trigger_time < self.config.cooldown_sec:
                return TriggerResult(matched=False, text=text)

            action = self._resolve_action(kw)
            button = self._select_button(normalized_text)

            if action == "recast":
                self._recast(button, require_ready_lane=True)
            elif action == "cast_if_idle":
                if not self.rod_casted:
                    self._cast_once(button, require_ready_lane=True)
            elif action == "reel_only":
                self._reel_once(button)
            else:
                self._click(button)

            self.last_trigger_time = now
            self.last_bite_seen_at = now
            return TriggerResult(
                matched=True,
                keyword=kw,
                action=action,
                button=button,
                text=text,
            )

        return TriggerResult(matched=False, text=text)

    def run(self) -> None:
        print("FishingAgent started. Press Ctrl+C to stop.")

        start_button = "left" if self.config.default_button == "left" else "right"
        if not self.rod_casted:
            print("[BOOT] no cast state detected, casting once.")
            if self._cast_once(start_button, require_ready_lane=True):
                time.sleep(self.config.recast_delay_sec)
            else:
                print("[BOOT] lane stayed blocked, skipped initial cast.")

        while True:
            try:
                result = self.step()
                now = time.time()
                if result.matched:
                    print(
                        f"[TRIGGER] keyword={result.keyword} action={result.action} button={result.button} casted={self.rod_casted}"
                    )

                self._handle_no_bite_timeout()
                self._handle_ocr_empty_timeout()

                if now >= self.next_stats_print_at:
                    self._emit_stats(final=False)
                    self.next_stats_print_at = now + max(1.0, self.config.stats_print_interval_sec)
                time.sleep(self.config.interval_sec)
            except KeyboardInterrupt:
                self._emit_stats(final=True)
                if self.config.debug_window:
                    cv2.destroyAllWindows()
                print("Stopped.")
                break
            except Exception as e:
                print(f"[WARN] {e}")
                time.sleep(max(self.config.interval_sec, 0.2))
