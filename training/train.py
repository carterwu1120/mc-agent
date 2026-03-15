from pathlib import Path
import yaml
from ultralytics import YOLO

DATASET_DIR = Path(__file__).parent / "dataset/minecraft.v10i.yolov8"
RUNS_DIR = Path(__file__).parent / "runs"

def make_resolved_yaml() -> Path:
    """Create a copy of data.yaml with absolute paths."""
    original = DATASET_DIR / "data.yaml"
    with open(original) as f:
        cfg = yaml.safe_load(f)

    cfg["train"] = str(DATASET_DIR / "train" / "images")
    cfg["val"] = str(DATASET_DIR / "valid" / "images")
    cfg["test"] = str(DATASET_DIR / "test" / "images")

    resolved = DATASET_DIR / "data_resolved.yaml"
    with open(resolved, "w") as f:
        yaml.dump(cfg, f, allow_unicode=True)
    return resolved


def main():
    data_yaml = make_resolved_yaml()

    model = YOLO("yolov8s.pt")  # small: good balance of speed/accuracy
    model.train(
        data=str(data_yaml),
        epochs=100,
        imgsz=640,
        batch=32,
        device=0,           # 4090
        project=str(RUNS_DIR),
        name="entity_v1",
        exist_ok=True,
        patience=20,
    )


if __name__ == "__main__":
    main()
