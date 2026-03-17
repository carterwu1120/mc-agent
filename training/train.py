from pathlib import Path
from ultralytics import YOLO

MERGED_YAML = Path(__file__).parent / "datasets/merged/data.yaml"
RUNS_DIR = Path(__file__).parent / "runs"


def main():
    if not MERGED_YAML.exists():
        raise FileNotFoundError(
            f"Merged dataset not found: {MERGED_YAML}\n"
            "Run `uv run -m training.prepare_datasets` first."
        )

    model = YOLO("yolov8s.pt")
    model.train(
        data=str(MERGED_YAML),
        epochs=100,
        imgsz=640,
        batch=64,
        device=0,
        project=str(RUNS_DIR),
        name="entity_v3",
        exist_ok=True,
        patience=20,
    )


if __name__ == "__main__":
    main()
