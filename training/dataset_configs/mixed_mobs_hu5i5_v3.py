# https://universe.roboflow.com/yashws/minecraft-hu5i5/dataset/3
SOURCE_DIR = "dataset/minecraft-hu5i5-v3"

REMAP = {
    "Angry Villager": "villager",
    "Character":      None,        # remove
    "Cow":            "cow",
    "Creeper":        "creeper",
    "Donkey":         None,       # remove - only 4 instances total
    "Duck":           "chicken",
    "Fish":           None,       # remove - too few instances
    "Horse":          None,       # remove - too few instances
    "Pig":            "pig",
    "Sheep":          "sheep",
    "Skeleton":       "skeleton",
    "Spider":         "spider",
    "Zombie":         "zombie",
}
