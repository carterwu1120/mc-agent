# Fishing Agent Tool

OCR-based screen watcher + keyword trigger + auto click helper for Minecraft fishing.

This project can be used in 2 ways:
- Standalone CLI
- Import as a Python module (for agent/plugin integration)

## Use uv (recommended)

1. Install `uv` (Windows PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

2. Sync dependencies:

```powershell
uv sync
```

3. Prepare config:

```powershell
Copy-Item .\sample_config.json .\config.json
```

4. Run:

```powershell
uv run fishing-agent --config .\config.json
```

## Optional: legacy pip workflow

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py --config .\config.json
```

## Plugin/module usage

```python
from fishing_tool import FishingAgent, FishingConfig

config = FishingConfig.from_file("config.json")
agent = FishingAgent(config)
agent.run()
```

## Notes

- `focus_region_ratio` lets you monitor only part of a window (e.g., right-bottom subtitle area).
- `water_region_ratio` can target the cast landing area for simple water-feature debugging.
- `center_lane_region_ratio`, `center_lane_clearance_low_threshold`, and `center_lane_clearance_high_threshold` let you classify the cast lane as `CLEAR`, `RISKY`, or `BLOCKED`.
- The center-lane debug score now emphasizes water-vs-non-water occupancy in the middle corridor, with edge metrics kept as supporting signals.
- Recovery adjustments now inspect both water distribution and obstacle distribution in the upper, lower, left, and right parts of the center lane, then try the opposite direction first.
- `clear_retry_limit`, `risky_retry_limit`, `blocked_retry_limit`, and the adjustment pixel settings drive the basic recovery ladder before angle-search logic gets more advanced.
- `bad_angle_failure_threshold` and `bobber_region_ratio` add a simple bad-angle memory and a bobber-candidate debug signal so obviously useless clear angles are less likely to repeat.
- Set `debug_window` to `true` to open an OpenCV preview showing the full capture, OCR ROI, water ROI, and basic feature scores.
- Real-time behavior is polling-based; use `interval_sec` around `0.05` to `0.2`.
- Check game/platform rules before using automation.
