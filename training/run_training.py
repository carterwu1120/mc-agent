"""
Prepare datasets and train in one step.

Usage:
    uv run -m training.run_training
    uv run -m training.run_training --name entity_v4
"""
import argparse
from pathlib import Path
from ultralytics import YOLO
from training.prepare_datasets import main as prepare

MERGED_YAML = Path(__file__).parent / "datasets/merged/data.yaml"
RUNS_DIR = Path(__file__).parent / "runs"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", type=str, default=None, help="Run name (e.g. entity_v4)")
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--patience", type=int, default=20)
    return parser.parse_args()


def main():
    args = parse_args()

    if args.name is None:
        args.name = input("Run name (e.g. entity_v4): ").strip()
        if not args.name:
            raise ValueError("Run name cannot be empty.")

    print("\n=== Step 1: Preparing datasets ===")
    prepare()

    print(f"\n=== Step 2: Training '{args.name}' ===")
    model = YOLO("yolov8s.pt")
    model.train(
        data=str(MERGED_YAML),
        epochs=args.epochs,
        imgsz=640,
        batch=args.batch,
        device=0,
        project=str(RUNS_DIR),
        name=args.name,
        exist_ok=False,
        patience=args.patience,
    )


if __name__ == "__main__":
    main()
