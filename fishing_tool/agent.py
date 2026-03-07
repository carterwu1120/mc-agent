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
                "lower_nonwater_ratio": 0.0,
                "edge_density": 0.0,
                "vertical_edge_ratio": 0.0,
                "horizontal_edge_ratio": 0.0,
                "lane_state": "BLOCKED",
            }

        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)

        water_lower = np.array([80, 25, 25], dtype=np.uint8)
        water_upper = np.array([130, 255, 255], dtype=np.uint8)
        water_mask = cv2.inRange(hsv, water_lower, water_upper)
        kernel = np.ones((3, 3), dtype=np.uint8)
        water_mask = cv2.morphologyEx(water_mask, cv2.MORPH_OPEN, kernel)
        water_mask = cv2.morphologyEx(water_mask, cv2.MORPH_CLOSE, kernel)
        nonwater_mask = cv2.bitwise_not(water_mask)

        h, w = gray.shape
        core_x1 = int(w * 0.20)
        core_x2 = int(w * 0.80)
        core_y1 = int(h * 0.18)
        core_y2 = int(h * 0.88)
        core_water = water_mask[core_y1:core_y2, core_x1:core_x2]
        core_nonwater = nonwater_mask[core_y1:core_y2, core_x1:core_x2]

        center_water_ratio = float(np.count_nonzero(core_water)) / float(core_water.size)
        center_nonwater_occupancy = 1.0 - center_water_ratio

        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(core_nonwater, connectivity=8)
        largest_component = 0
        for label_idx in range(1, num_labels):
            largest_component = max(largest_component, int(stats[label_idx, cv2.CC_STAT_AREA]))
        connected_block_ratio = float(largest_component) / float(core_nonwater.size)

        upper_band = nonwater_mask[: max(1, int(h * 0.30)), core_x1:core_x2]
        lower_band = nonwater_mask[int(h * 0.55) :, core_x1:core_x2]
        upper_open_ratio = 1.0 - (
            float(np.count_nonzero(upper_band)) / float(upper_band.size)
            if upper_band.size
            else 0.0
        )
        lower_nonwater_ratio = (
            float(np.count_nonzero(lower_band)) / float(lower_band.size)
            if lower_band.size
            else 0.0
        )

        edges = cv2.Canny(blurred, 50, 150)
        edge_density = float(np.count_nonzero(edges)) / float(edges.size)

        grad_x = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
        abs_grad_x = np.abs(grad_x)
        abs_grad_y = np.abs(grad_y)

        grad_energy = float(np.sum(abs_grad_x) + np.sum(abs_grad_y)) + 1e-6
        vertical_edge_ratio = float(np.sum(abs_grad_x)) / grad_energy
        horizontal_edge_ratio = float(np.sum(abs_grad_y)) / grad_energy

        obstacle_score = min(
            1.0,
            0.35 * center_nonwater_occupancy
            + 0.35 * min(connected_block_ratio / 0.30, 1.0)
            + 0.30 * lower_nonwater_ratio,
        )
        openness_score = min(
            1.0,
            0.60 * center_water_ratio
            + 0.40 * upper_open_ratio,
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
            "lower_nonwater_ratio": lower_nonwater_ratio,
            "edge_density": edge_density,
            "vertical_edge_ratio": vertical_edge_ratio,
            "horizontal_edge_ratio": horizontal_edge_ratio,
            "lane_state": lane_state,
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

        focus_img = self._capture_region(focus_region)
        water_img = self._capture_region(water_region) if water_region else None
        center_lane_img = self._capture_region(center_lane_region) if center_lane_region else None
        features = self._compute_water_features(water_img)
        center_lane = self._compute_center_lane_features(center_lane_img)

        main = cv2.cvtColor(window_img, cv2.COLOR_BGRA2BGR)
        self._draw_region_box(main, base_region, focus_region, (0, 255, 255), "OCR ROI")
        self._draw_region_box(main, base_region, water_region, (255, 180, 0), "Water ROI")
        lane_state = center_lane["lane_state"]
        if lane_state == "CLEAR":
            lane_color = (0, 220, 0)
        elif lane_state == "RISKY":
            lane_color = (0, 215, 255)
        else:
            lane_color = (0, 0, 255)
        self._draw_region_box(main, base_region, center_lane_region, lane_color, "Center Lane")

        panel_width = 620
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

        lines = [
            f"rod_casted: {self.rod_casted}",
            f"last text: {text[:36] or '(empty)'}",
            f"water_score: {features['water_score']:.2f}",
            f"blue_ratio: {features['blue_ratio']:.2f}",
            f"brightness_std: {features['brightness_std']:.1f}",
            f"water_edge_density: {features['edge_density']:.2f}",
            f"center_clearance: {center_lane['clearance_score']:.2f}",
            f"center_lane: {lane_state}",
            f"center_water_ratio: {center_lane['center_water_ratio']:.2f}",
            f"center_nonwater_occ: {center_lane['center_nonwater_occupancy']:.2f}",
            f"connected_block_ratio: {center_lane['connected_block_ratio']:.2f}",
            f"upper_open_ratio: {center_lane['upper_open_ratio']:.2f}",
            f"lower_nonwater_ratio: {center_lane['lower_nonwater_ratio']:.2f}",
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

    def _cast_once(self, button: str) -> None:
        self._click(button)
        self.rod_casted = True
        self.cast_timestamps.append(time.time())

    def _reel_once(self, button: str) -> None:
        self._click(button)
        self.rod_casted = False

    def _recast(self, button: str) -> None:
        self._reel_once(button)
        time.sleep(self.config.recast_delay_sec)
        self._cast_once(button)

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
                return

    def _run_recover_action(self, action: str, reason_tag: str) -> None:
        button = "left" if self.config.default_button == "left" else "right"

        if action == "recast":
            self._recast(button)
            recover_note = "recast"
        elif action == "smart_recover":
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
                recover_note = "smart_recover(retrieved_then_cast)"
            elif cast_hit:
                self.rod_casted = True
                self.cast_timestamps.append(probe_click_at)
                recover_note = "smart_recover(thrown_ok)"
            elif not self.rod_casted:
                self._cast_once(button)
                recover_note = "smart_recover(unknown_then_cast)"
            else:
                recover_note = "smart_recover(unknown_keep)"
        elif action == "cast_if_idle":
            if not self.rod_casted:
                self._cast_once(button)
                recover_note = "cast_if_idle(casted)"
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

        self._run_recover_action(self.config.no_bite_timeout_action, "no_bite_timeout")
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
        self._show_debug_window(text)

        if text.strip():
            self.last_nonempty_ocr_at = time.time()

        normalized_text = self._normalize(text)
        self._sync_state_from_text(normalized_text)
        self._touch_bite_presence(normalized_text)

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
                self._recast(button)
            elif action == "cast_if_idle":
                if not self.rod_casted:
                    self._cast_once(button)
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
            self._cast_once(start_button)
            time.sleep(self.config.recast_delay_sec)

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
