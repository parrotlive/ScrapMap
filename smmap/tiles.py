"""Index and decode the game's .tile terrain files.

Header layout (version 15):

    "TILE" | u32 version | uuid[16] | ... | u32 cellsX | u32 cellsY
    @0x28  | u32 mipDirOffset | u32 mipDirSize
    @0x3c  | u32 offset[6] | u32 compressedSize[6] | u32 uncompressedSize[6]

Each mip level is an LZ4 block holding two arrays for the whole tile:

    heights   (S/2+1)^2  x  (float32 height, uint32 mask)
    materials      S^2   x  8 uint8 weights (mat0..mat7)

with S = 65, 33, 17, 9, 5, 3 for LOD 0..5. That grid is a fixed 65x65 for every
tile regardless of how many cells the tile spans, so it only samples a 64 m
meadow at a metre; for a big point of interest it is coarse or, as with the Silo
District, empty. The real ground for those lives one grid per cell in the
.tileson, which Tile.surface reads.
"""

import base64
import binascii
import glob
import json
import os
import struct

from . import lz4

MIP_DIR = 0x3C
LOD_COUNT = 6

# One .tileson surface cell: 33x33 float32 heights, the same again for the mask
# the .tile keeps beside them, then 65x65 material weights stored plane by plane.
HEIGHT_SIDE = 33
MAT_SIDE = 65
HEIGHT_BYTES = HEIGHT_SIDE * HEIGHT_SIDE * 8
SURFACE_BYTES = HEIGHT_BYTES + 8 * MAT_SIDE * MAT_SIDE


def _unpad(s):
    """The tileson drops base64 padding."""
    return base64.b64decode(s + "=" * (-len(s) % 4))


# Ground material names in weight order, from Data/Terrain/Materials/
# gnd_standard_materialset.json. Index 8 is the base the terrain falls back to
# where all eight weights are zero.
MATERIAL_NAMES = ["Concrete", "Sand", "Stone", "Dirt",
                  "Weeds", "Rough Stone", "Hay", "Bright grass", "Grass"]


def uuid_str(raw16):
    h = raw16.hex()
    return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])


class Tile(object):
    __slots__ = ("path", "name", "uuid", "cells_x", "cells_y", "biome",
                 "_lods", "_cells")

    def __init__(self, path, uuid, cells_x, cells_y, biome):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.uuid = uuid
        self.cells_x = cells_x
        self.cells_y = cells_y
        self.biome = biome
        self._lods = {}
        self._cells = None

    @property
    def size(self):
        return max(self.cells_x, self.cells_y, 1)

    @property
    def tileson(self):
        """The companion file listing everything placed on this tile."""
        return os.path.splitext(self.path)[0] + ".tileson"

    def surface(self, ox, oy):
        """Ground for one 64 m cell of this tile: (heights, materials), or None.

        The .tile's own LOD 0 is a single 65x65 grid for the whole tile, so a
        512 m point of interest gets one sample every eight metres -- and for
        those tiles it is not even that: the Silo District's is empty, because
        the game builds its ground from the per-cell grids the .tileson carries
        instead. Those are a full 65x65 of material weights and 33x33 of heights
        for every cell, so every cell is sampled at a metre however large the
        tile is.

        Cell order is x fastest, verified by the boundary samples neighbouring
        cells share: 8744 of 8744 shared edges match exactly under this order.
        """
        cells = self._cells
        if cells is None:
            cells = []
            try:
                with open(self.tileson, "rb") as f:
                    doc = json.load(f)
                cells = ((doc.get("terrain") or {}).get("surface") or {}).get("cells") or []
            except (OSError, ValueError):
                pass
            want = max(self.cells_x, 1) * max(self.cells_y, 1)
            self._cells = cells if len(cells) == want else []
            cells = self._cells
        if ox >= self.cells_x or oy >= self.cells_y:
            return None
        i = oy * max(self.cells_x, 1) + ox
        if i >= len(cells):
            return None
        try:
            raw = lz4.decompress(_unpad(cells[i]))
        except (ValueError, binascii.Error):
            return None
        if len(raw) < SURFACE_BYTES:
            return None
        import numpy as np
        heights = np.frombuffer(raw, "<f4", count=HEIGHT_SIDE * HEIGHT_SIDE) \
                    .reshape(HEIGHT_SIDE, HEIGHT_SIDE)
        mats = np.frombuffer(raw, np.uint8, count=8 * MAT_SIDE * MAT_SIDE,
                             offset=HEIGHT_BYTES) \
                 .reshape(8, MAT_SIDE, MAT_SIDE).transpose(1, 2, 0)
        return heights, mats

    def forget(self):
        """Drop the cached surface blobs once a tile has been drawn."""
        self._cells = None

    def lod(self, level=0):
        """(heights, materials) as numpy arrays, decoded lazily and cached.

        heights   float32 (V, V)     where V = S//2 + 1
        materials uint8   (S, S, 8)
        """
        if level in self._lods:
            return self._lods[level]
        import numpy as np

        with open(self.path, "rb") as f:
            head = f.read(MIP_DIR + LOD_COUNT * 12)
            offsets = struct.unpack_from("<6I", head, MIP_DIR)
            csizes = struct.unpack_from("<6I", head, MIP_DIR + 24)
            usizes = struct.unpack_from("<6I", head, MIP_DIR + 48)
            f.seek(offsets[level])
            blob = f.read(csizes[level])

        s = [65, 33, 17, 9, 5, 3][level]
        v = s // 2 + 1
        hbytes = v * v * 8
        # Interior tiles -- warehouse floors and the like -- ship no terrain at
        # all. They are never placed on the overworld, but a flat tile is a
        # kinder answer than an exception.
        if not csizes[level] or usizes[level] < hbytes + s * s * 8:
            out = (np.zeros((v, v), dtype=np.float32),
                   np.zeros((s, s, 8), dtype=np.uint8))
            self._lods[level] = out
            return out

        raw = lz4.decompress(blob, usizes[level])
        pairs = np.frombuffer(raw[:hbytes], dtype="<u4").reshape(v * v, 2)
        heights = pairs[:, 0].copy().view("<f4").reshape(v, v)
        mats = np.frombuffer(raw[hbytes:hbytes + s * s * 8],
                             dtype=np.uint8).reshape(s, s, 8)

        out = (heights, mats)
        self._lods[level] = out
        return out


def _biome_of(relpath):
    parts = relpath.replace("\\", "/").lower().split("/")
    known = ("meadow", "forest", "autumn_forest", "burnt_forest", "desert",
             "field", "lake", "roads_and_cliffs", "roads_biomes", "poi",
             "start_area", "excavation", "ravine", "underground", "questtiles",
             "bosstrain", "legacy")
    for p in reversed(parts[:-1]):
        if p in known:
            return p
    name = parts[-1]
    for k in known:
        if k.replace("_", "") in name.replace("_", ""):
            return k
    return "other"


class TileIndex(object):
    """uuid -> Tile for every .tile shipped with the game."""

    def __init__(self, game_dir):
        self.game_dir = game_dir
        self.by_uuid = {}
        self._scan()

    def _scan(self):
        roots = [os.path.join(self.game_dir, "Survival", "Terrain", "Tiles"),
                 os.path.join(self.game_dir, "Data", "Terrain", "Tiles"),
                 os.path.join(self.game_dir, "Survival", "DungeonTiles")]
        for root in roots:
            if not os.path.isdir(root):
                continue
            for path in glob.glob(os.path.join(root, "**", "*.tile"), recursive=True):
                try:
                    with open(path, "rb") as f:
                        head = f.read(0x28)
                except OSError:
                    continue
                if len(head) < 0x28 or head[:4] != b"TILE":
                    continue
                uid = uuid_str(head[8:24])
                cx, cy = struct.unpack_from("<II", head, 0x20)
                rel = os.path.relpath(path, self.game_dir)
                self.by_uuid[uid] = Tile(path, uid, cx, cy, _biome_of(rel))

    def get(self, uuid):
        return self.by_uuid.get(str(uuid))

    def __len__(self):
        return len(self.by_uuid)
