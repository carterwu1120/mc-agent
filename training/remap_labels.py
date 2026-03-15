"""
Remap YOLO label files:
- Remove class 2 (flower)
- Remap remaining indices: duck->chicken, re-index after flower removal
Old: 0=cow, 1=duck, 2=flower(remove), 3=people, 4=pig, 5=rabbit, 6=sheep
New: 0=cow, 1=chicken, 2=people, 3=pig, 4=rabbit, 5=sheep
"""
from pathlib import Path

DATASET_DIR = Path(__file__).parent / "dataset/minecraft.v10i.yolov8"

# old_index -> new_index, None = remove
REMAP = {0: 0, 1: 1, 2: None, 3: 2, 4: 3, 5: 4, 6: 5}

def remap_label_file(path: Path):
    lines = path.read_text().splitlines()
    new_lines = []
    for line in lines:
        if not line.strip():
            continue
        parts = line.split()
        old_cls = int(parts[0])
        new_cls = REMAP.get(old_cls)
        if new_cls is None:
            continue  # remove this class
        new_lines.append(f"{new_cls} {' '.join(parts[1:])}")
    path.write_text("\n".join(new_lines) + "\n" if new_lines else "")

def main():
    total, modified = 0, 0
    for split in ["train", "valid", "test"]:
        labels_dir = DATASET_DIR / split / "labels"
        if not labels_dir.exists():
            continue
        for label_file in labels_dir.glob("*.txt"):
            original = label_file.read_text()
            remap_label_file(label_file)
            remapped = label_file.read_text()
            total += 1
            if original != remapped:
                modified += 1

    print(f"Processed {total} label files, {modified} modified.")

    # Update data.yaml
    yaml_path = DATASET_DIR / "data.yaml"
    yaml_text = yaml_path.read_text()
    yaml_text = yaml_text.replace(
        "nc: 7\nnames: ['cow', 'duck', 'flower', 'people', 'pig', 'rabbit', 'sheep']",
        "nc: 6\nnames: ['cow', 'chicken', 'people', 'pig', 'rabbit', 'sheep']"
    )
    yaml_path.write_text(yaml_text)
    print("data.yaml updated.")

if __name__ == "__main__":
    main()
