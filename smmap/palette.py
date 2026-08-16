"""Terrain colours.

These are the mean colours of the game's own ground diffuse textures, in weight
order, taken from Data/Terrain/Materials/gnd_standard_materialset.json. Index 8
("Grass") is the base layer the terrain shows wherever all eight weights are
zero, which is most of a meadow.
"""

MATERIAL_RGB = [
    (114, 123, 108),   # 0 Concrete
    (210, 149, 68),    # 1 Sand
    (111, 121, 108),   # 2 Stone
    (111, 91, 51),     # 3 Dirt
    (105, 103, 17),    # 4 Weeds
    (95, 103, 95),     # 5 Rough Stone
    (192, 132, 37),    # 6 Hay
    (117, 126, 0),     # 7 Bright grass
]
BASE_RGB = (98, 134, 0)        # 8 Grass -- shown where no weight is present

OCEAN_RGB = (38, 86, 128)      # cells outside the generated world
UNKNOWN_RGB = (150, 60, 150)   # a tile uuid the installed game does not have

# The world has a water plane at z = 0; anything below it reads as water.
WATER_SHALLOW_RGB = (74, 141, 168)
WATER_DEEP_RGB = (24, 61, 104)

# A point of interest can stand its own pool above that plane, and not all of
# them hold water. Indexed by the liquid kind detail.py records.
LIQUID_RGB = [
    (WATER_SHALLOW_RGB, WATER_DEEP_RGB),
    ((140, 168, 46), (74, 100, 20)),      # chemicals
    ((52, 46, 40), (22, 19, 16)),         # oil
]


def recompute_from_game(game_dir):
    """Re-derive the palette from the installed textures.

    Not needed normally -- the constants above already come from the game -- but
    it keeps the tool honest if Axolot ever reskins the ground materials.
    """
    import json
    import os
    import numpy as np
    from PIL import Image

    from . import discover

    cfg = discover.resolve(os.path.join(game_dir, "Data", "Terrain", "Materials",
                                        "gnd_standard_materialset.json"))
    with open(cfg, "r", encoding="utf-8", errors="replace") as f:
        textures = json.load(f)["groundMaterials"]["textures"]

    out = []
    for t in textures:
        p = (t["diffuse"].replace("$GAME_DATA", os.path.join(game_dir, "Data"))
                          .replace("$SURVIVAL_DATA", os.path.join(game_dir, "Survival"))
                          .replace("/", os.sep))
        with Image.open(discover.resolve(p)) as im:
            a = np.asarray(im.convert("RGB")).reshape(-1, 3)
        out.append(tuple(int(round(v)) for v in a.mean(axis=0)))
    return out[:8], (out[8] if len(out) > 8 else BASE_RGB)
