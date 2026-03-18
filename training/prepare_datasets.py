"""
Prepare and merge multiple datasets for YOLO training.

Usage:
    uv run -m training.prepare_datasets

Each dataset config in dataset_configs/ defines:
    SOURCE_DIR  - path relative to training/ directory
    REMAP       - dict mapping original class name -> new name (None = remove)

All splits (train/valid/test) from all datasets are pooled together,
then re-split 85/10/5 to ensure even class distribution in val/test.
"""
from __future__ import annotations

import importlib
import random
import shutil
from collections import defaultdict
from pathlib import Path

import yaml

TRAINING_DIR = Path(__file__).parent
CONFIGS_DIR = TRAINING_DIR / "dataset_configs"
OUTPUT_DIR = TRAINING_DIR / "datasets" / "merged"

TRAIN_RATIO = 0.85
VAL_RATIO = 0.10
# remainder goes to test

SEED = 42


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
    with open(source_dir / "data.yaml") as f:
        return yaml.safe_load(f)["names"]


def build_global_classes(configs: list[dict]) -> list[str]:
    classes = set()
    for cfg in configs:
        for name in load_yaml_classes(cfg["source_dir"]):
            new_name = cfg["remap"].get(name)
            if new_name is not None:
                classes.add(new_name)
    return sorted(classes)


def build_index_map(cfg: dict, global_classes: list[str]) -> dict[int, int | None]:
    original = load_yaml_classes(cfg["source_dir"])
    index_map = {}
    for i, name in enumerate(original):
        new_name = cfg["remap"].get(name)
        index_map[i] = global_classes.index(new_name) if new_name is not None else None
    return index_map


def remap_label(src: Path, index_map: dict[int, int | None]) -> list[str]:
    kept = []
    for line in src.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split()
        new_idx = index_map.get(int(parts[0]))
        if new_idx is not None:
            kept.append(f"{new_idx} {' '.join(parts[1:])}")
    return kept


def collect_all_samples(configs: list[dict], global_classes: list[str]) -> list[dict]:
    """Collect all image+label pairs from all datasets and splits into one pool."""
    samples = []
    for cfg in configs:
        source_dir = cfg["source_dir"]
        index_map = build_index_map(cfg, global_classes)

        for split in ("train", "valid", "test"):
            images_dir = source_dir / split / "images"
            labels_dir = source_dir / split / "labels"
            if not images_dir.exists():
                continue

            for img_path in images_dir.iterdir():
                label_path = labels_dir / f"{img_path.stem}.txt"
                lines = remap_label(label_path, index_map) if label_path.exists() else []
                classes_in_img = {int(l.split()[0]) for l in lines}
                samples.append({
                    "img_path": img_path,
                    "lines": lines,
                    "classes": classes_in_img,
                    "stem": f"{cfg['name']}_{img_path.stem}",
                    "suffix": img_path.suffix,
                })
    return samples


def stratified_split(samples: list[dict]) -> tuple[list, list, list]:
    """Split samples by primary class to ensure class balance in val/test."""
    random.seed(SEED)

    # Group by primary class (first class in label, or -1 for background)
    groups: dict[int, list] = defaultdict(list)
    for s in samples:
        key = min(s["classes"]) if s["classes"] else -1
        groups[key].append(s)

    train, val, test = [], [], []
    for group in groups.values():
        random.shuffle(group)
        n = len(group)
        n_val = max(1, round(n * VAL_RATIO))
        n_test = max(1, round(n * (1 - TRAIN_RATIO - VAL_RATIO)))
        test.extend(group[:n_test])
        val.extend(group[n_test:n_test + n_val])
        train.extend(group[n_test + n_val:])

    return train, val, test


def write_split(samples: list[dict], split: str):
    images_dst = OUTPUT_DIR / split / "images"
    labels_dst = OUTPUT_DIR / split / "labels"
    images_dst.mkdir(parents=True, exist_ok=True)
    labels_dst.mkdir(parents=True, exist_ok=True)

    for s in samples:
        shutil.copy2(s["img_path"], images_dst / f"{s['stem']}{s['suffix']}")
        label_dst = labels_dst / f"{s['stem']}.txt"
        label_dst.write_text("\n".join(s["lines"]) + "\n" if s["lines"] else "")


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

    samples = collect_all_samples(configs, global_classes)
    print(f"Total samples collected: {len(samples)}")

    train, val, test = stratified_split(samples)
    print(f"Split: train={len(train)}, val={len(val)}, test={len(test)}")

    write_split(train, "train")
    write_split(val, "valid")
    write_split(test, "test")

    yaml_path = write_merged_yaml(global_classes)
    print(f"\nMerged dataset ready: {OUTPUT_DIR}")
    print(f"data.yaml: {yaml_path}")


if __name__ == "__main__":
    main()
