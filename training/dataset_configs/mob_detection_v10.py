# https://universe.roboflow.com/minecraft-object-detection/minecraft-mob-detection/dataset/10
SOURCE_DIR = "dataset/minecraft-mob-detection-v10"

REMAP = {
    "bee":      "bee",
    "chicken":  "chicken",
    "cow":      "cow",
    "creeper":  "creeper",
    "enderman": "enderman",
    "fox":      None,       # remove - minor passive
    "frog":     None,       # remove - minor passive
    "ghast":    None,       # remove - nether only, rare
    "goat":     None,       # remove - minor passive
    "llama":    None,       # remove - minor passive
    "pig":      None,
    "sheep":    "sheep",
    "skeleton": "skeleton",
    "spider":   "spider",
    "turtle":   None,       # remove - minor passive
    "wolf":     None,
    "zombie":   "zombie",
}
