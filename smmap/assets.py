"""The game's environment asset and harvestable databases.

Every prop, rock, building and tree the terrain places is catalogued in a
``.assetset`` or ``.harvestableset`` JSON, which gives a name, default per
material colours and -- most usefully -- a collision mesh in plain ``.obj``.
Those meshes are what let the map draw a structure's real footprint instead of a
generic blob.
"""

import glob
import json
import os

import numpy as np

# Directions used to reduce a collision mesh to a handful of extreme points.
# The convex hull through them is a close stand-in for the real hull, and small
# enough that transforming it per instance stays cheap.
_DIRS = np.array([(x, y, z)
                  for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)
                  if (x, y, z) != (0, 0, 0)], dtype=np.float32)


def euler_matrix(deg):
    """Rotation matrix for a tileson rotation triple.

    The convention is R = Rx * Ry * Rz with intrinsic degrees, verified to zero
    error against the quaternions stored in the binary .tile records over 431
    matched assets.
    """
    ex, ey, ez = np.radians(deg)
    cx, sx = np.cos(ex), np.sin(ex)
    cy, sy = np.cos(ey), np.sin(ey)
    cz, sz = np.cos(ez), np.sin(ez)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float32)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float32)
    return rx @ ry @ rz


def euler_matrices(deg):
    """euler_matrix for a whole (N, 3) block of rotations at once."""
    r = np.radians(np.asarray(deg, dtype=np.float32))
    cx, cy, cz = np.cos(r).T
    sx, sy, sz = np.sin(r).T
    m = np.empty((len(r), 3, 3), dtype=np.float32)
    m[:, 0, 0] = cy * cz
    m[:, 0, 1] = -cy * sz
    m[:, 0, 2] = sy
    m[:, 1, 0] = sx * sy * cz + cx * sz
    m[:, 1, 1] = -sx * sy * sz + cx * cz
    m[:, 1, 2] = -sx * cy
    m[:, 2, 0] = -cx * sy * cz + sx * sz
    m[:, 2, 1] = cx * sy * sz + sx * cz
    m[:, 2, 2] = cx * cy
    return m


# What a prop should read as on a map. The game names its environment art
# systematically enough that the name is a better classifier than anything in
# the databases themselves, which describe physics rather than looks.
CATEGORY_RGB = {
    "road":   (104, 104, 108),
    "ground": (126, 106, 76),
    "plant":  (44, 88, 32),
    "rock":   (142, 140, 133),
    "wreck":  (146, 104, 72),
    "build":  (198, 194, 186),
}
DEFAULT_CATEGORY = "build"

_CATEGORY_WORDS = (
    ("road", ("road", "sidewalk", "ditch", "asphalt", "curb", "pavement")),
    ("ground", ("dirtpatch", "sandpatch", "mudpatch", "puddle", "leafpile")),
    ("plant", ("foliage", "tree", "bush", "shrub", "birch", "spruce", "pine",
               "leaf", "leaves", "fern", "thorn", "plant", "flower", "sprout",
               "grass", "hay", "corn", "farmable", "crop", "weed", "coral",
               "buxus", "perennial", "clover", "vine", "moss", "stump",
               "potato", "cotton", "mushroom", "cactus", "palm", "firelog",
               "trunk", "seaweed", "kelp")),
    ("rock", ("rock", "stone", "boulder", "cliff", "canyon", "stalagmite",
              "stalactite", "crystal", "quartz", "ice formation", "gravel",
              "ore", "pebble")),
    ("wreck", ("ruin", "rubble", "wreck", "debris", "junk", "scrap", "robot",
               "drillbot", "farmbot", "totebot", "haybot", "tapebot")),
)

# Accent materials -- a red lamp or a yellow hazard stripe is a detail, not the
# colour the thing reads as from a kilometre up.
_ACCENT_WORDS = ("lamp", "light", "glow", "emissive", "stripe", "caution")


def _parse_hex_colour(s):
    s = str(s).lstrip("#")
    if len(s) < 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


class AssetDb(object):
    def __init__(self, game_dir):
        self.game_dir = game_dir
        self.by_uuid = {}
        self._shapes = {}
        self._cats = {}
        self._scan()

    # -- catalogue --------------------------------------------------------

    def _expand(self, path):
        return (path.replace("$SURVIVAL_DATA", os.path.join(self.game_dir, "Survival"))
                    .replace("$GAME_DATA", os.path.join(self.game_dir, "Data"))
                    .replace("$CHALLENGE_DATA", os.path.join(self.game_dir, "ChallengeData"))
                    .replace("/", os.sep))

    def _scan(self):
        patterns = [
            ("Survival/Terrain/Database/AssetSets", "*.assetset"),
            ("Data/Terrain/Database/AssetSets", "*.assetset"),
            ("ChallengeData/Terrain/Database/AssetSets", "*.assetset"),
            ("Survival/Harvestables/Database/HarvestableSets", "*.harvestableset"),
            ("Data/Harvestables/Database/HarvestableSets", "*.harvestableset"),
        ]
        for rel, pat in patterns:
            root = os.path.join(self.game_dir, rel.replace("/", os.sep))
            for f in glob.glob(os.path.join(root, pat)):
                try:
                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                        doc = json.load(fh)
                except Exception:
                    continue
                for entries in doc.values():
                    if not isinstance(entries, list):
                        continue
                    for a in entries:
                        if isinstance(a, dict) and "uuid" in a:
                            self.by_uuid.setdefault(a["uuid"], a)

    def name(self, uuid):
        a = self.by_uuid.get(uuid)
        return a.get("name", "") if a else ""

    def category(self, uuid):
        """Which of CATEGORY_RGB this asset belongs to, from its name."""
        c = self._cats.get(uuid)
        if c is not None:
            return c
        n = self.name(uuid).lower()
        c = DEFAULT_CATEGORY
        for cat, words in _CATEGORY_WORDS:
            if any(w in n for w in words):
                c = cat
                break
        self._cats[uuid] = c
        return c

    def colour(self, uuid, colour_map=None):
        """A representative RGB, preferring the colours the instance was painted.

        Terrain props carry a per-material colour map -- concrete, metal, leaves
        -- which is the only place the game says what an asset actually looks
        like without opening its textures.
        """
        cols = []
        for src in (colour_map, (self.by_uuid.get(uuid) or {}).get("defaultColors")):
            if not src:
                continue
            for k, v in src.items():
                if any(w in k.lower() for w in _ACCENT_WORDS):
                    continue
                c = _parse_hex_colour(v)
                if c:
                    cols.append(c)
            if cols:
                break
        if not cols:
            for v in (self.by_uuid.get(uuid) or {}).get("color") or ():
                c = _parse_hex_colour(v)
                if c:
                    cols.append(c)
        if not cols:
            return None
        return tuple(np.asarray(cols, dtype=np.float32).mean(axis=0))

    # -- collision geometry ----------------------------------------------

    def shape(self, uuid):
        """Local-space extreme points (K, 3) of the collision mesh, or None.

        Meshes are Y-up; the caller rotates them into the world's Z-up frame.
        Assets with no renderable are invisible collision volumes and are
        deliberately given no shape, so nothing draws them.
        """
        if uuid in self._shapes:
            return self._shapes[uuid]
        self._shapes[uuid] = None
        a = self.by_uuid.get(uuid)
        if not a or not a.get("col") or not a.get("renderable"):
            return None
        path = self._expand(a["col"])
        if not os.path.isfile(path):
            return None
        xs = []
        try:
            with open(path, "r", errors="replace") as fh:
                for line in fh:
                    if line[:2] == "v ":
                        p = line.split()
                        if len(p) >= 4:
                            xs.append((float(p[1]), float(p[2]), float(p[3])))
        except Exception:
            return None
        if not xs:
            return None
        v = np.asarray(xs, dtype=np.float32)
        if len(v) > len(_DIRS):
            # Keep only the vertex furthest along each probe direction.
            keep = np.unique(np.argmax(v @ _DIRS.T, axis=0))
            v = v[keep]
        self._shapes[uuid] = v
        return v
