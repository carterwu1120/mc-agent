"""
Show class distribution for a dataset or merged dataset.

Usage:
    uv run -m training.inspect_dataset              # merged dataset
    uv run -m training.inspect_dataset --source dataset/minecraft-mob-detection-v10
"""
import argparse
from collections import defaultdict
from pathlib import Path

import yaml

TRAINING_DIR = Path(__file__).parent


def count_instances(labels_dir: Path) -> dict[str, int]:
    counts = defaultdict(int)
    for f in labels_dir.glob("*.txt"):
        for line in f.read_text().splitlines():
            if line.strip():
                counts[int(line.split()[0])] += 1
    return counts


def inspect(source_dir: Path):
    yaml_path = source_dir / "data.yaml"
    if not yaml_path.exists():
        # merged dataset uses absolute paths
        yaml_path = source_dir / "data.yaml"
    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)
    names = cfg["names"]

    print(f"\nDataset: {source_dir.name}")
    print(f"Classes: {len(names)}\n")

    total_instances = defaultdict(int)
    total_images = defaultdict(int)

    for split in ("train", "valid", "test"):
        labels_dir = source_dir / split / "labels"
        if not labels_dir.exists():
            continue
        counts = count_instances(labels_dir)
        for idx, count in counts.items():
            total_instances[idx] += count

        images_dir = source_dir / split / "images"
        if images_dir.exists():
            # count images that contain each class
            for f in (source_dir / split / "labels").glob("*.txt"):
                classes_in_file = set()
                for line in f.read_text().splitlines():
                    if line.strip():
                        classes_in_file.add(int(line.split()[0]))
                for idx in classes_in_file:
                    total_images[idx] += 1

    print(f"{'Class':<20} {'Images':>8} {'Instances':>10}")
    print("-" * 42)
    for idx, name in enumerate(names):
        images = total_images.get(idx, 0)
        instances = total_instances.get(idx, 0)
        bar = "█" * (instances // 20)
        print(f"{name:<20} {images:>8} {instances:>10}  {bar}")

    print(f"\nTotal instances: {sum(total_instances.values())}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str, default=None,
                        help="Dataset dir relative to training/ (default: merged)")
    args = parser.parse_args()

    if args.source:
        source_dir = TRAINING_DIR / args.source
    else:
        source_dir = TRAINING_DIR / "datasets/merged"

    if not source_dir.exists():
        raise FileNotFoundError(f"Not found: {source_dir}")

    inspect(source_dir)


if __name__ == "__main__":
    main()
