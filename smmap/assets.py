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
import re

import numpy as np

from . import discover
from . import fbx

# A few of the game's catalogues are JSON with line comments in them, which is
# not JSON at all. One of them is the farming harvestables, and dropping it
# loses every crop in the world, so the comments are taken out rather than the
# file. Only comments outside strings go, which is what the alternation does:
# a quoted run is matched first and put back untouched.
_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)


def _strip_comments(text):
    return _COMMENT.sub(lambda m: m.group(0) if m.group(0)[:1] == '"' else "",
                        text)


def load_json(path):
    """Read one of the game's JSON files, comments and all, or None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return json.loads(_strip_comments(text))
    except ValueError:
        return None

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
               "oak", "willow", "leafy", "seaplant", "sprout", "lilly",
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

# Standing liquid -- a pond in a point of interest, a chemical bath, an oil pool
# -- is not terrain and has no collision mesh. It is a unit box whose placement
# stretches it, usually to 64 x 64 x 16 metres, and the only thing that marks it
# out is the material its renderable draws with.
UNIT_BOX = np.array([(x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5)
                     for z in (-0.5, 0.5)], dtype=np.float32)

# How much geometry a thing that collides with nothing may keep. Undergrowth is
# placed in the tens of thousands and drawn from a distance, and its art carries
# far more detail than either of those wants to pay for.
ART_TRIANGLES = 160


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
        self._meshes = {}
        self._art = {}
        self._cats = {}
        self._liquids = {}
        self._scan()

    # -- catalogue --------------------------------------------------------

    def _expand(self, path):
        p = (path.replace("$SURVIVAL_DATA", os.path.join(self.game_dir, "Survival"))
                 .replace("$GAME_DATA", os.path.join(self.game_dir, "Data"))
                 .replace("$CHALLENGE_DATA", os.path.join(self.game_dir, "ChallengeData"))
                 .replace("/", os.sep))
        return discover.resolve(p)

    def _scan(self):
        patterns = [
            ("Survival/Terrain/Database/AssetSets", "*.assetset"),
            ("Data/Terrain/Database/AssetSets", "*.assetset"),
            ("ChallengeData/Terrain/Database/AssetSets", "*.assetset"),
            ("Survival/Harvestables/Database/HarvestableSets", "*.harvestableset"),
            ("Data/Harvestables/Database/HarvestableSets", "*.harvestableset"),
            # Kinematics are the moving furniture -- guardrails, doors, lifts,
            # rails, the drill bots' gantries. props.py has always read them out
            # of the tilesons; without their own catalogue here they had no name
            # and no mesh, so they were placed and then quietly dropped.
            ("Survival/Kinematics/Database/KinematicSets", "*.kinematicset"),
            ("Data/Kinematics/Database/KinematicSets", "*.kinematicset"),
            ("ChallengeData/Kinematics/Database/KinematicSets", "*.kinematicset"),
        ]
        for rel, pat in patterns:
            root = discover.resolve(
                os.path.join(self.game_dir, rel.replace("/", os.sep)))
            for f in glob.glob(os.path.join(root, pat)):
                doc = load_json(f)
                if not isinstance(doc, dict):
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

    def liquid(self, uuid):
        """True if this asset is a body of liquid rather than a solid thing."""
        hit = self._liquids.get(uuid)
        if hit is not None:
            return hit
        out = False
        a = self.by_uuid.get(uuid)
        if a is not None and not a.get("col"):
            r = a.get("renderable")
            if isinstance(r, str):
                try:
                    with open(self._expand(r), "rb") as f:
                        r = json.load(f)
                except (OSError, ValueError):
                    r = None
            for lod in (r or {}).get("lodList") or ():
                subs = list((lod.get("subMeshMap") or {}).values())
                subs += list(lod.get("subMeshList") or ())
                out = out or any(str(s.get("material", "")).lower().startswith("water")
                                 for s in subs)
        self._liquids[uuid] = out
        return out

    # -- collision geometry ----------------------------------------------

    def _collision_path(self, uuid):
        """Where this asset's collision mesh lives, if it has one to draw.

        Assets with no renderable are invisible collision volumes -- trigger
        boxes, blockers -- and are deliberately treated as having no mesh, so
        nothing ever draws them.
        """
        a = self.by_uuid.get(uuid)
        if not a or not a.get("col") or not a.get("renderable"):
            return None
        path = self._expand(a["col"])
        return path if os.path.isfile(path) else None

    def mesh(self, uuid):
        """The collision mesh as (vertices (N, 3), triangles (M, 3)), or None.

        These are the shapes the game itself collides against: a warehouse is a
        warehouse, a boulder is that boulder. They are simpler than the art the
        game draws -- no texture, fewer faces, and a tree is the trunk and a
        cone rather than every leaf -- but they are the real geometry of the
        real object, in the real place, at the real size.

        A collision mesh is written either as a plain Wavefront ``.obj`` or as
        an FBX, and which one is nothing to do with the asset: piers, rubble
        piles, platforms and half the ruins in a world are FBX purely because
        of who exported them. Reading only the ``.obj`` half quietly drops two
        hundred of the six hundred kinds of thing a world stands up.
        """
        if uuid in self._meshes:
            return self._meshes[uuid]
        self._meshes[uuid] = None
        path = self._collision_path(uuid)
        if path is None:
            return None
        out = _read_mesh(path)
        self._meshes[uuid] = out
        return out

    def art_mesh(self, uuid, cap=ART_TRIANGLES):
        """The mesh the game draws this with, for things that collide with none.

        A quarter of everything a world places is undergrowth -- sea plants,
        buxus, column shrubs, sprouts, sunflowers -- and a player walks through
        all of it, so none of it has a collision mesh to be drawn from. The only
        geometry those have is the art, and without it a world comes out mown.

        The coarsest level the game ships is the one taken, since this is scenery
        seen from a distance, and it is thinned further if it is still dense: a
        sea plant is a hundred triangles standing in a world a hundred thousand
        times, and its smallest facets cost more than they show.
        """
        if uuid in self._art:
            return self._art[uuid]
        self._art[uuid] = None
        a = self.by_uuid.get(uuid)
        rend = (a or {}).get("renderable")
        doc = rend if isinstance(rend, dict) else (
            load_json(self._expand(rend)) if isinstance(rend, str) else None)
        lods = (doc or {}).get("lodList") or []
        if not lods:
            return None
        path = lods[-1].get("mesh")
        if not path:
            return None
        path = self._expand(path)
        if not os.path.isfile(path):
            return None
        out = _read_mesh(path)
        if out is not None and cap and len(out[1]) > cap:
            out = _thin(out[0], out[1], cap)
        self._art[uuid] = out
        return out

    def any_mesh(self, uuid):
        """Whatever geometry this asset has: what it collides with, or its art."""
        return self.mesh(uuid) or self.art_mesh(uuid)

    def shape(self, uuid):
        """Local-space extreme points (K, 3) of the collision mesh, or None.

        The same mesh as mesh(), reduced to the handful of points the flat map
        needs to fill a footprint.
        """
        if uuid in self._shapes:
            return self._shapes[uuid]
        self._shapes[uuid] = None
        m = self.mesh(uuid)
        if m is None:
            return None
        v = m[0]
        if len(v) > len(_DIRS):
            # Keep only the vertex furthest along each probe direction.
            keep = np.unique(np.argmax(v @ _DIRS.T, axis=0))
            v = v[keep]
        self._shapes[uuid] = v
        return v


def _face_index(field, n):
    """One vertex index out of an .obj face field.

    A field is ``v``, ``v/vt``, ``v//vn`` or ``v/vt/vn``, and the index is
    1-based, or negative to count back from the vertices seen so far.
    """
    s = field.split("/", 1)[0]
    if not s:
        return None
    try:
        i = int(s)
    except ValueError:
        return None
    if i > 0:
        return i - 1 if i <= n else None
    if i < 0:
        return n + i if -i <= n else None
    return None


def _read_mesh(path):
    """(vertices, triangles) from a collision mesh in either format it uses."""
    if os.path.splitext(path)[1].lower() == ".fbx":
        try:
            subs = fbx.read(path, want_uv=False)
        except Exception:
            return None
        subs = [s for s in subs if len(s.pos) and len(s.tris)]
        if not subs:
            return None
        # A collision mesh may still be several nodes -- a pier is its deck and
        # its piles -- and they are one shape as far as anything here cares.
        verts, tris, at = [], [], 0
        for s in subs:
            verts.append(s.pos)
            tris.append(s.tris + at)
            at += len(s.pos)
        return (np.concatenate(verts).astype(np.float32),
                np.concatenate(tris).astype(np.uint32))
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("ascii", "replace")
    except OSError:
        return None
    return _read_obj(text)


def _thin(verts, tris, cap):
    """Keep the ``cap`` largest facets of a mesh and drop the rest.

    Facets go smallest first rather than vertices being moved about, so every
    triangle that survives is exactly where and how big the game made it: a
    plant loses its finest fronds and keeps its shape.
    """
    e1 = verts[tris[:, 1]] - verts[tris[:, 0]]
    e2 = verts[tris[:, 2]] - verts[tris[:, 0]]
    area = np.linalg.norm(np.cross(e1, e2), axis=1)
    keep = tris[np.argpartition(-area, cap)[:cap]]
    used, faces = np.unique(keep.reshape(-1), return_inverse=True)
    return verts[used], faces.reshape(-1, 3).astype(np.uint32)


def _read_obj(text):
    """Parse a Wavefront .obj into (vertices, triangles), or None if empty.

    Only ``v`` and ``f`` matter here: the collision meshes carry no texture
    coordinates worth having and no materials at all. Faces with more than
    three corners are fanned, which is right for the convex faces a collision
    hull is made of.
    """
    verts = []
    tris = []
    for line in text.splitlines():
        tag = line[:2]
        if tag == "v ":
            p = line.split()
            if len(p) >= 4:
                try:
                    verts.append((float(p[1]), float(p[2]), float(p[3])))
                except ValueError:
                    pass
        elif tag == "f ":
            idx = []
            for field in line.split()[1:]:
                i = _face_index(field, len(verts))
                if i is not None:
                    idx.append(i)
            for k in range(1, len(idx) - 1):
                tris.append((idx[0], idx[k], idx[k + 1]))
    if not verts:
        return None
    v = np.asarray(verts, dtype=np.float32)
    if not tris:
        return None
    t = np.asarray(tris, dtype=np.uint32)
    # A degenerate face contributes nothing and would give a zero normal.
    keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    t = t[keep]
    return (v, t) if len(t) else None
