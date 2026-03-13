from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class FishingConfig:
    keywords: list[str]
    button_rules: dict[str, str]
    keyword_actions: dict[str, str]
    default_button: str
    window_title_contains: Optional[str]
    region: Optional[dict[str, int]]
    focus_region_ratio: Optional[dict[str, float]]
    cast_keyword: str
    reel_keyword: str
    interval_sec: float
    cooldown_sec: float
    recast_delay_sec: float
    languages: list[str]
    ocr_engine: str
    ocr_lang: str
    print_ocr_text: bool
    case_sensitive: bool
    stats_log_file: Optional[str]
    stats_print_interval_sec: float
    bite_presence_keywords: list[str]
    no_bite_timeout_sec: Optional[float]
    no_bite_timeout_action: str
    no_bite_recover_cooldown_sec: float
    ocr_empty_timeout_sec: Optional[float]
    ocr_empty_timeout_action: str
    ocr_empty_recover_cooldown_sec: float
    smart_recover_probe_wait_sec: float
    water_region_ratio: Optional[dict[str, float]]
    center_lane_region_ratio: Optional[dict[str, float]]
    center_lane_clearance_low_threshold: float
    center_lane_clearance_high_threshold: float
    clear_retry_limit: int
    clear_casted_timeout_recast_limit: int
    risky_retry_limit: int
    blocked_retry_limit: int
    blocked_adjustment_max_steps: int
    adjustment_settle_sec: float
    adjustment_pitch_step_pixels: int
    adjustment_yaw_step_pixels: int
    bad_angle_failure_threshold: int
    bobber_region_ratio: Optional[dict[str, float]]
    bobber_min_score: float
    blocked_scan_trigger_count: int
    blocked_probe_min_improvement: float
    blocked_scan_yaw_steps: int
    blocked_scan_pitch_steps: int
    refinement_obstacle_threshold: float
    refinement_max_steps: int
    local_refine_max_steps: int
    failed_cast_pitch_up_steps: int
    failed_cast_forward_sec: float
    viable_water_center_threshold: float
    viable_water_direction_threshold: float
    refinement_water_target: float
    debug_window: bool
    debug_window_scale: float
    vision_enabled: bool
    vision_model: str
    vision_host: str
    vision_probe_count: int
    vision_probe_yaw_pixels: int
    vision_walk_forward_sec: float
    vision_extra_push_steps: int

    @classmethod
    def from_file(cls, path: str | Path) -> "FishingConfig":
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

        no_bite_timeout_raw = data.get("no_bite_timeout_sec")
        no_bite_timeout_sec = None if no_bite_timeout_raw is None else float(no_bite_timeout_raw)
        ocr_empty_timeout_raw = data.get("ocr_empty_timeout_sec")
        ocr_empty_timeout_sec = None if ocr_empty_timeout_raw is None else float(ocr_empty_timeout_raw)

        return cls(
            keywords=data.get("keywords", []),
            button_rules=data.get("button_rules", {}),
            keyword_actions=data.get("keyword_actions", {}),
            default_button=data.get("default_button", "right"),
            window_title_contains=data.get("window_title_contains"),
            region=data.get("region"),
            focus_region_ratio=data.get("focus_region_ratio"),
            cast_keyword=data.get("cast_keyword", "Bobber thrown"),
            reel_keyword=data.get("reel_keyword", "Bobber retrieved"),
            interval_sec=float(data.get("interval_sec", 0.1)),
            cooldown_sec=float(data.get("cooldown_sec", 0.5)),
            recast_delay_sec=float(data.get("recast_delay_sec", 0.25)),
            languages=data.get("languages", ["en"]),
            ocr_engine=str(data.get("ocr_engine", "paddleocr")).lower(),
            ocr_lang=str(data.get("ocr_lang", "en")),
            print_ocr_text=bool(data.get("print_ocr_text", False)),
            case_sensitive=bool(data.get("case_sensitive", False)),
            stats_log_file=data.get("stats_log_file"),
            stats_print_interval_sec=float(data.get("stats_print_interval_sec", 30.0)),
            bite_presence_keywords=data.get("bite_presence_keywords", data.get("keywords", [])),
            no_bite_timeout_sec=no_bite_timeout_sec,
            no_bite_timeout_action=str(data.get("no_bite_timeout_action", "click")).lower(),
            no_bite_recover_cooldown_sec=float(data.get("no_bite_recover_cooldown_sec", 20.0)),
            ocr_empty_timeout_sec=ocr_empty_timeout_sec,
            ocr_empty_timeout_action=str(data.get("ocr_empty_timeout_action", "click")).lower(),
            ocr_empty_recover_cooldown_sec=float(data.get("ocr_empty_recover_cooldown_sec", 20.0)),
            smart_recover_probe_wait_sec=float(data.get("smart_recover_probe_wait_sec", 0.35)),
            water_region_ratio=data.get("water_region_ratio"),
            center_lane_region_ratio=data.get("center_lane_region_ratio"),
            center_lane_clearance_low_threshold=float(
                data.get("center_lane_clearance_low_threshold", 0.42)
            ),
            center_lane_clearance_high_threshold=float(
                data.get("center_lane_clearance_high_threshold", 0.62)
            ),
            clear_retry_limit=int(data.get("clear_retry_limit", 0)),
            clear_casted_timeout_recast_limit=int(
                data.get("clear_casted_timeout_recast_limit", 2)
            ),
            risky_retry_limit=int(data.get("risky_retry_limit", 0)),
            blocked_retry_limit=int(data.get("blocked_retry_limit", 0)),
            blocked_adjustment_max_steps=int(data.get("blocked_adjustment_max_steps", 12)),
            adjustment_settle_sec=float(data.get("adjustment_settle_sec", 0.2)),
            adjustment_pitch_step_pixels=int(data.get("adjustment_pitch_step_pixels", 18)),
            adjustment_yaw_step_pixels=int(data.get("adjustment_yaw_step_pixels", 26)),
            bad_angle_failure_threshold=int(data.get("bad_angle_failure_threshold", 1)),
            bobber_region_ratio=data.get("bobber_region_ratio"),
            bobber_min_score=float(data.get("bobber_min_score", 0.55)),
            blocked_scan_trigger_count=int(data.get("blocked_scan_trigger_count", 2)),
            blocked_probe_min_improvement=float(data.get("blocked_probe_min_improvement", 0.08)),
            blocked_scan_yaw_steps=int(data.get("blocked_scan_yaw_steps", 10)),
            blocked_scan_pitch_steps=int(data.get("blocked_scan_pitch_steps", 4)),
            refinement_obstacle_threshold=float(
                data.get("refinement_obstacle_threshold", 0.32)
            ),
            refinement_max_steps=int(data.get("refinement_max_steps", 2)),
            local_refine_max_steps=int(data.get("local_refine_max_steps", 6)),
            failed_cast_pitch_up_steps=int(data.get("failed_cast_pitch_up_steps", 4)),
            failed_cast_forward_sec=float(data.get("failed_cast_forward_sec", 0.18)),
            viable_water_center_threshold=float(
                data.get("viable_water_center_threshold", 0.34)
            ),
            viable_water_direction_threshold=float(
                data.get("viable_water_direction_threshold", 0.45)
            ),
            refinement_water_target=float(data.get("refinement_water_target", 0.52)),
            debug_window=bool(data.get("debug_window", False)),
            debug_window_scale=float(data.get("debug_window_scale", 0.75)),
            vision_enabled=bool(data.get("vision_enabled", True)),
            vision_model=str(data.get("vision_model", "llava:7b")),
            vision_host=str(data.get("vision_host", "http://localhost:11434")),
            vision_probe_count=int(data.get("vision_probe_count", 8)),
            vision_probe_yaw_pixels=int(data.get("vision_probe_yaw_pixels", 300)),
            vision_walk_forward_sec=float(data.get("vision_walk_forward_sec", 1.5)),
            vision_extra_push_steps=int(data.get("vision_extra_push_steps", 3)),
        )
