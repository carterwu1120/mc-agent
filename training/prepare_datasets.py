"""
Prepare and merge multiple datasets for YOLO training.

Usage:
    uv run -m training.prepare_datasets

Each dataset config in dataset_configs/ defines:
    SOURCE_DIR  - path relative to training/ directory
    REMAP       - dict mapping original class name -> new name (None = remove)

Output: training/datasets/merged/ with unified train/valid/test splits.
"""
from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import yaml

TRAINING_DIR = Path(__file__).parent
CONFIGS_DIR = TRAINING_DIR / "dataset_configs"
OUTPUT_DIR = TRAINING_DIR / "datasets" / "merged"


def load_configs() -> list[dict]:
    configs = []
    for path in sorted(CONFIGS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"training.dataset_configs.{path.stem}"
        mod = importlib.import_module(module_name)
        configs.append({
            "name": path.stem,
            "source_dir": TRAINING_DIR / mod.SOURCE_DIR,
            "remap": mod.REMAP,
        })
    print(f"Found {len(configs)} dataset config(s): {[c['name'] for c in configs]}")
    return configs


def load_yaml_classes(source_dir: Path) -> list[str]:
    yaml_path = source_dir / "data.yaml"
    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)
    return cfg["names"]


def build_global_classes(configs: list[dict]) -> list[str]:
    """Collect all unique output class names across all datasets, sorted."""
    classes = set()
    for cfg in configs:
        original = load_yaml_classes(cfg["source_dir"])
        for name in original:
            new_name = cfg["remap"].get(name)
            if new_name is not None:
                classes.add(new_name)
    return sorted(classes)


def remap_label_file(
    src: Path,
    dst: Path,
    index_map: dict[int, int],  # old_index -> new_index
) -> int:
    """Write remapped label file. Returns number of kept annotations."""
    lines = src.read_text().splitlines()
    kept = []
    for line in lines:
        if not line.strip():
            continue
        parts = line.split()
        old_idx = int(parts[0])
        new_idx = index_map.get(old_idx)
        if new_idx is None:
            continue
        kept.append(f"{new_idx} {' '.join(parts[1:])}")
    dst.write_text("\n".join(kept) + "\n" if kept else "")
    return len(kept)


def process_dataset(cfg: dict, global_classes: list[str], split: str) -> tuple[int, int]:
    source_dir = cfg["source_dir"]
    remap = cfg["remap"]
    original_classes = load_yaml_classes(source_dir)

    # old_index -> new_index (None = remove)
    index_map: dict[int, int | None] = {}
    for i, name in enumerate(original_classes):
        new_name = remap.get(name)
        if new_name is not None:
            index_map[i] = global_classes.index(new_name)
        else:
            index_map[i] = None

    images_src = source_dir / split / "images"
    labels_src = source_dir / split / "labels"
    images_dst = OUTPUT_DIR / split / "images"
    labels_dst = OUTPUT_DIR / split / "labels"
    images_dst.mkdir(parents=True, exist_ok=True)
    labels_dst.mkdir(parents=True, exist_ok=True)

    if not images_src.exists():
        return 0, 0

    copied, skipped = 0, 0
    for img_path in images_src.iterdir():
        stem = f"{cfg['name']}_{img_path.stem}"
        label_src = labels_src / f"{img_path.stem}.txt"

        # Copy image
        shutil.copy2(img_path, images_dst / f"{stem}{img_path.suffix}")

        # Remap label
        if label_src.exists():
            kept = remap_label_file(label_src, labels_dst / f"{stem}.txt", index_map)
            if kept > 0:
                copied += 1
            else:
                skipped += 1
        else:
            # background image — write empty label
            (labels_dst / f"{stem}.txt").write_text("")
            copied += 1

    return copied, skipped


def write_merged_yaml(global_classes: list[str]) -> Path:
    yaml_path = OUTPUT_DIR / "data.yaml"
    cfg = {
        "train": str(OUTPUT_DIR / "train" / "images"),
        "val":   str(OUTPUT_DIR / "valid" / "images"),
        "test":  str(OUTPUT_DIR / "test"  / "images"),
        "nc":    len(global_classes),
        "names": global_classes,
    }
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f, allow_unicode=True)
    return yaml_path


def main():
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
        print(f"Cleared existing output: {OUTPUT_DIR}")

    configs = load_configs()
    global_classes = build_global_classes(configs)
    print(f"Global classes ({len(global_classes)}): {global_classes}")

    for split in ("train", "valid", "test"):
        total_copied = total_skipped = 0
        for cfg in configs:
            copied, skipped = process_dataset(cfg, global_classes, split)
            total_copied += copied
            total_skipped += skipped
        print(f"  {split}: {total_copied} images kept, {total_skipped} images with no annotations")

    yaml_path = write_merged_yaml(global_classes)
    print(f"\nMerged dataset ready: {OUTPUT_DIR}")
    print(f"data.yaml: {yaml_path}")


if __name__ == "__main__":
    main()
