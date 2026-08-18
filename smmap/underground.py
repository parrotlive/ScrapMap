"""Map the floors under the world.

The overworld is one grid of tiles laid side by side. The underground is eight
worlds of them stacked, and the difference that matters is the third axis: a
floor is sixteen chunks of sixteen metres tall, and the same cell holds a
tunnel at one height, a cave at another and solid rock in between. So a cell no
longer names one tile. It names a column.

Everything here reads that column out of the same place the overworld comes
from -- ``sm.terrainData.save``, the cell grid the game writes when it generates
a world -- and lays it flat again. What the grid holds per cell is:

    uid, rotation, xOffset, yOffset   the tile the floor was built on, if any
    caves[]                           whole cells of a cave tile, packed
    pockets[]                         part-cells of a pocket tile, packed
    spawners[]                        where the floor puts something to fight

and per world, ``tunnels``: the corridors the generator dug between them, as
polylines in metres, each labelled with what is in its walls.

The caves and pockets are packed into single integers, laid out by
``AddCaveCellData`` and ``AddPocketCellData`` in the game's chunk_raster.lua:

    cave    tile index | z << 8 | (height - 1) << 12 | cellX << 16
                       | cellY << 18 | rotation << 20
    pocket  tile index | (x | y << 2 | z << 4) << 8
                       | (w - 1 | (d - 1) << 2 | (h - 1) << 4) << 16
                       | srcX << 24 | srcY << 26 | rotation << 28

with the cave's offsets counted in cells of the tile it comes from and the
pocket's in chunks, which is the whole reason a pocket can sit a quarter of the
way into a cell and a cave cannot.

The picture that comes out is the floor of everything that was dug, seen from
above: whatever is highest at a point is what is drawn there, and rock that was
never dug is drawn as rock. It is a plan of the workings rather than a
photograph of them -- the cave walls themselves are built by the game at run
time out of voxel meshes that no file on disk holds the result of -- but where
the workings are, how deep they run, how tall they stand and what is in them all
come from the save, and none of it is guessed.
"""

import numpy as np

from . import palette
from .detail import NO_LIQUID
from .render import MapRenderer, _orient, _smooth
from .savefile import CELL_SIZE, CHUNK_SIZE, CHUNKS_PER_CELL
from .smlua import Uuid, Vec3

# A floor is this many chunks tall -- 256 m -- from the game's
# survival/terrain/underground/chunk_raster.lua, MIN_HEIGHT_IN_CHUNKS to
# MAX_HEIGHT_IN_CHUNKS.
FLOOR_CHUNKS = 16

# The world script class each floor is created with, in the order
# survival_constants.lua's UNDERGROUND_DEFS lists them, so the index is the
# depth the game passes in. The label is what the lift shows: the scrapyard and
# the surface get letters rather than numbers, which is the game's own joke and
# also the only way to tell floor 5 on the panel from depth 5 in the save.
FLOORS = (
    ("UndergroundWorldMiningHub",      "Mining Hub",      "1"),
    ("UndergroundWorldTutorial",       "Onboarding",      "2"),
    ("UndergroundWorldStation1",       "Station One",     "3"),
    ("UndergroundWorldDrill1",         "Drill One",       "4"),
    ("UndergroundWorldScrapyard",      "Scrapyard",       "T"),
    ("UndergroundWorldDrill2",         "Drill Two",       "5"),
    ("UndergroundWorldStation2",       "Station Two",     "6"),
    ("UndergroundWorldFinalBossLobby", "Drillbot Lobby",  "7"),
)

_BY_CLASS = {cls.lower(): (i + 1, name, label)
             for i, (cls, name, label) in enumerate(FLOORS)}

# What a tunnel was dug for. The generator settles this after pathing, in
# terrain_underground.lua, and it is the difference between a corridor to walk
# and a seam worth following. The width is how far across carve_tunnels.lua
# sweeps its cross-section: eight metres for a plain tunnel, and a vein pinches
# and swells along its length around a narrower mean.
TUNNEL_KINDS = {
    "TtDefault":        ("Tunnel", (122, 112, 100), 8.0),
    "MainVein":         ("Vein", (150, 132, 96), 7.0),
    "CrossVein":        ("Vein", (150, 132, 96), 7.0),
    "TtVeinRich":       ("Rich vein", (198, 160, 72), 6.0),
    "TtVeinT1":         ("Ore vein", (166, 138, 96), 7.0),
    "TtVeinT4":         ("Deep vein", (128, 146, 178), 7.0),
    "TtVeinSparkstone": ("Sparkstone vein", (176, 150, 196), 5.0),
}
_TUNNEL_FALLBACK = ("Tunnel", (122, 112, 100), 8.0)


class Piece(object):
    """One laying of one tile: where it comes from, where it goes, how tall.

    Source and destination are both counted in chunks -- the tile's own grid
    for the source, the world's for the destination -- because a pocket is
    placed to the chunk and a cave to the cell, and chunks are the units both
    of them divide into.
    """

    __slots__ = ("tile", "sx", "sy", "sw", "sh", "dx", "dy", "z0", "nz", "rot",
                 "kind")

    def __init__(self, tile, sx, sy, sw, sh, dx, dy, z0, nz, rot, kind):
        self.tile = tile
        self.sx, self.sy, self.sw, self.sh = sx, sy, sw, sh
        self.dx, self.dy = dx, dy
        self.z0, self.nz = z0, nz
        self.rot = rot
        self.kind = kind

    @property
    def dw(self):
        return self.sh if self.rot & 1 else self.sw

    @property
    def dh(self):
        return self.sw if self.rot & 1 else self.sh


class Tunnel(object):
    __slots__ = ("kind", "colour", "width", "points")

    def __init__(self, kind, colour, width, points):
        self.kind = kind
        self.colour = colour
        self.width = width            # metres across
        self.points = points          # [(x, y, z)] in metres

    @property
    def length(self):
        out = 0.0
        for a, b in zip(self.points, self.points[1:]):
            out += ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
                    + (b[2] - a[2]) ** 2) ** 0.5
        return out


class Floor(object):
    """One underground world, ready to render."""

    def __init__(self, world, grid, depth, name, label):
        self.world = world
        self.grid = grid
        self.depth = depth
        self.name = name
        self.label = label
        self.pieces = []
        self.tunnels = []
        self.spawners = 0
        self.missing = set()

    @property
    def ceiling(self):
        """How far up the workings on this floor reach, in metres."""
        hi = max((p.z0 + p.nz for p in self.pieces), default=0)
        for t in self.tunnels:
            for p in t.points:
                hi = max(hi, int(p[2] // CHUNK_SIZE) + 1)
        return min(max(hi, 1), FLOOR_CHUNKS) * CHUNK_SIZE

    def __repr__(self):
        return "<Floor %d %s: %d pieces, %d tunnels>" % (
            self.depth, self.name, len(self.pieces), len(self.tunnels))


# -- reading a floor out of a save ---------------------------------------


def floors(save_file, tile_index):
    """Every underground floor this save has generated, shallowest first.

    A floor only exists once the player has taken the lift down to it -- the
    game creates the world lazily -- so this is exactly the part of the
    underground that has been visited, and nothing below it.
    """
    out = []
    for wid, world in sorted(save_file.worlds().items()):
        hit = _BY_CLASS.get(world.cls.lower())
        if hit is None:
            continue
        grid = save_file.terrain_data(wid)
        if not isinstance(grid, dict) or "bounds" not in grid:
            continue
        depth, name, label = hit
        # The save is allowed to disagree with the table: it was written by the
        # game and the table was typed out from it.
        depth = int(world.data.get("depth") or depth)
        out.append(_build(Floor(world, grid, depth, name, label), tile_index))
    out.sort(key=lambda f: f.depth)
    return out


def _tile_list(grid, tile_index):
    """The floor's tile table as a 1-based list of Tile, None where unknown."""
    raw = grid.get("tileList") or {}
    if not raw:
        return []
    top = max(k for k in raw if isinstance(k, int))
    out = [None] * (top + 1)
    for k, v in raw.items():
        if isinstance(k, int) and isinstance(v, Uuid):
            out[k] = tile_index.get(v)
    return out


def _rows(grid, key):
    return grid.get(key) or {}


def _build(floor, tile_index):
    grid = floor.grid
    b = grid["bounds"]
    x0, x1 = int(b["xMin"]), int(b["xMax"])
    y0, y1 = int(b["yMin"]), int(b["yMax"])
    lookup = _tile_list(grid, tile_index)

    uid = _rows(grid, "uid")
    rot = _rows(grid, "rotation")
    xoff = _rows(grid, "xOffset")
    yoff = _rows(grid, "yOffset")
    caves = _rows(grid, "caves")
    pockets = _rows(grid, "pockets")
    spawners = _rows(grid, "spawners")
    n = CHUNKS_PER_CELL

    for cy in range(y0, y1 + 1):
        urow = uid.get(cy) or {}
        rrow = rot.get(cy) or {}
        xrow = xoff.get(cy) or {}
        yrow = yoff.get(cy) or {}
        crow = caves.get(cy) or {}
        prow = pockets.get(cy) or {}
        srow = spawners.get(cy) or {}
        for cx in range(x0, x1 + 1):
            dx, dy = (cx - x0) * n, (cy - y0) * n

            # The floor's own tile, laid out cell by cell like the overworld's.
            u = urow.get(cx)
            if isinstance(u, Uuid) and not u.is_nil():
                tile = tile_index.get(u)
                if tile is None:
                    floor.missing.add(str(u))
                else:
                    floor.pieces.append(Piece(
                        tile,
                        int(xrow.get(cx, 0) or 0) * n,
                        int(yrow.get(cx, 0) or 0) * n, n, n,
                        dx, dy, 0, FLOOR_CHUNKS, int(rrow.get(cx, 0) or 0) & 3,
                        "floor"))

            for v in (crow.get(cx) or {}).values():
                p = _cave(v, lookup, dx, dy, n)
                if p is not None:
                    floor.pieces.append(p)
            for v in (prow.get(cx) or {}).values():
                p = _pocket(v, lookup, dx, dy)
                if p is not None:
                    floor.pieces.append(p)
            floor.spawners += len(srow.get(cx) or {})

    floor.tunnels = _tunnels(grid)
    # Deepest first, so what is drawn last is what is on top. Ties go to the
    # taller piece: a cave and the tunnel that meets it start at the same
    # chunk, and the cave is the place worth seeing.
    floor.pieces.sort(key=lambda p: (p.z0, p.nz))
    return floor


def _cave(value, lookup, dx, dy, n):
    value = int(value) & 0xFFFFFFFF
    tile = _at(lookup, value & 0xFF)
    if tile is None:
        return None
    return Piece(tile,
                 ((value >> 16) & 0x3) * n, ((value >> 18) & 0x3) * n, n, n,
                 dx, dy,
                 (value >> 8) & 0xF, ((value >> 12) & 0xF) + 1,
                 (value >> 20) & 0x3, "cave")


def _pocket(value, lookup, dx, dy):
    value = int(value) & 0xFFFFFFFF
    tile = _at(lookup, value & 0xFF)
    if tile is None:
        return None
    d = (value >> 8) & 0xFF
    s = (value >> 16) & 0xFF
    return Piece(tile,
                 (value >> 24) & 0x3, (value >> 26) & 0x3,
                 (s & 0x3) + 1, ((s >> 2) & 0x3) + 1,
                 dx + (d & 0x3), dy + ((d >> 2) & 0x3),
                 (d >> 4) & 0xF, ((s >> 4) & 0xF) + 1,
                 (value >> 28) & 0x3, "pocket")


def _at(lookup, index):
    return lookup[index] if 0 < index < len(lookup) else None


def _tunnels(grid):
    out = []
    for t in (grid.get("tunnels") or {}).values():
        if not isinstance(t, dict):
            continue
        pts = [p for p in (t.get("positions") or {}).values()
               if isinstance(p, Vec3)]
        if len(pts) < 2:
            continue
        kind, colour, width = TUNNEL_KINDS.get(t.get("tunnelType"),
                                               _TUNNEL_FALLBACK)
        out.append(Tunnel(kind, colour, width, pts))
    return out


# -- rendering -----------------------------------------------------------


class UndergroundRenderer(MapRenderer):
    """A floor of the underground, drawn the way the overworld is.

    Same tiles, same props, same hillshade, same water. What changes is that a
    cell is a column rather than a square, so the loop runs over layings of
    tiles instead of over cells, and keeps whichever laying is highest at each
    point -- which is what looking down at a cave system gets you.
    """

    def __init__(self, floor, tile_index, px=32, asset_db=None, structures=True):
        # Placements land on chunk boundaries, so a chunk has to be a whole
        # number of pixels or a pocket would drift a fraction of a chunk from
        # where the save puts it.
        px = max(CHUNKS_PER_CELL, int(round(px / 4.0)) * 4)
        MapRenderer.__init__(self, floor.grid, tile_index, px=px,
                             asset_db=asset_db, structures=structures)
        self.floor = floor
        self.missing = set(floor.missing)
        self.ceiling = floor.ceiling
        self.tunnel_pixels = 0

    @staticmethod
    def _blend(mats):
        """Ground colour, over rock rather than over grass.

        The underground's tiles mostly carry no ground materials at all -- their
        surfaces are cut out of the rock by the game rather than painted -- so
        the base is what almost every pixel of a floor ends up being.
        """
        w = mats.astype(np.float32)
        total = w.sum(axis=2, keepdims=True)
        mat_rgb = np.asarray(palette.MATERIAL_RGB, dtype=np.float32)
        layered = w.reshape(-1, 8).dot(mat_rgb).reshape(w.shape[0], w.shape[1], 3)
        layered /= np.where(total > 0, total, 1.0)
        cov = np.clip(total / 255.0, 0.0, 1.0)
        base = np.asarray(palette.CAVE_FLOOR_RGB, dtype=np.float32)
        return base * (1.0 - cov) + layered * cov

    def render(self, hillshade=True, water=True, progress=None, fields=False):
        px = self.px
        ch = px // CHUNKS_PER_CELL
        rows, cols = self.h * px, self.w * px

        img = np.empty((rows, cols, 3), dtype=np.float32)
        img[:] = palette.BEDROCK_RGB
        # Rock that was never dug is not a surface, and a picture can only draw
        # one. Once the floors are known it is dropped below all of them, so the
        # workings stand at their real heights over a flat dark nothing -- the
        # same answer the overworld gives the sea outside it.
        hmap = np.zeros((rows, cols), dtype=np.float32)
        # What is visible at each point, so a laying only wins where it is
        # actually the top one. Rock loses to anything.
        zbuf = np.full((rows, cols), -1e9, dtype=np.float32)
        top = np.zeros((rows, cols), dtype=np.float32) if self.structures else None
        pools = np.full((rows, cols), NO_LIQUID, np.float32) if self.baker else None
        kinds = np.zeros((rows, cols), np.uint8) if self.baker else None

        groups = {}
        for p in self.floor.pieces:
            groups.setdefault(p.tile, []).append(p)
        # What the solid view stands the props up from. A laying underground is
        # a rectangle of chunks at a height rather than a cell of a flat grid,
        # so it carries its own frame instead of an index into one.
        self.placements = {t: [self._laid(p) for p in v]
                           for t, v in groups.items()}

        total, done = len(self.floor.pieces), 0
        for tile, pieces in groups.items():
            self.used[tile.name] = self.used.get(tile.name, 0) + len(pieces)
            colour, ground = self._tile_arrays(tile)
            overlay = None
            if self.baker:
                seen = self.baker.drawn
                overlay = self.baker.bake(tile, ground)
                self.props += (self.baker.drawn - seen) * len(pieces)
            for p in pieces:
                self._place(p, ch, rows, colour, ground, overlay,
                            img, hmap, zbuf, top, pools, kinds)
            done += len(pieces)
            if progress:
                progress(done, total)

        self._dig(ch, rows, img, hmap, zbuf, top)

        self.dug = zbuf > -1e8
        # Rock the workings never reached has no floor to speak of, and letting
        # it into the range would put every real floor in the same band.
        dug = hmap[self.dug]
        self.floor_lo = float(dug.min()) if dug.size else 0.0
        self.floor_hi = float(dug.max()) if dug.size else self.ceiling
        hmap[~self.dug] = self.floor_lo - CHUNK_SIZE
        self.height_map = hmap
        self._tint(img, hmap)

        # Every drop of water down here is a volume some tile places, so with
        # nothing read out of the tiles there is none -- and a level of zero
        # would drown the whole floor, since a floor starts at zero.
        level = np.full(hmap.shape, NO_LIQUID, np.float32) if pools is None \
            else pools
        if fields:
            self._keep(img, hmap, top, level, kinds)
        if hillshade:
            shade = self._shade(_smooth(hmap, 2))
            shade = np.where(hmap < level, 1.0 + (shade - 1.0) * 0.25, shade)
            # Only the workings are lit. Rock nobody dug is not ground with a
            # slope, it is the page the plan is drawn on, and shading it puts a
            # bevel round every chamber that reads as terrain.
            img[self.dug] *= shade[self.dug][:, None]
        if top is not None:
            self._light_structures(img, top)
        if water:
            img = self._apply_water(img, hmap, top, level, kinds)
        return np.clip(img, 0, 255).astype(np.uint8)

    def _tint(self, img, hmap, strength=0.5):
        """Shift the dug ground towards cool as it goes down.

        Applied as a colour of mean one so it only turns the hue: a plan of a
        cave system has no sky to light it, and dimming the deep parts would
        fight the hillshade for the same pixel.
        """
        span = self.floor_hi - self.floor_lo
        if span < CHUNK_SIZE or not self.dug.any():
            return
        lo = np.asarray(palette.DEPTH_LOW_RGB, dtype=np.float32)
        hi = np.asarray(palette.DEPTH_HIGH_RGB, dtype=np.float32)
        lo, hi = lo / lo.mean(), hi / hi.mean()
        t = np.clip((hmap[self.dug] - self.floor_lo) / span, 0.0, 1.0)[:, None]
        mix = lo * (1.0 - t) + hi * t
        img[self.dug] *= 1.0 - strength + mix * strength

    def _laid(self, p):
        from .objects3d import Laid

        return Laid(p.sx * CHUNK_SIZE, p.sy * CHUNK_SIZE,
                    p.sw * CHUNK_SIZE, p.sh * CHUNK_SIZE,
                    self.x0 * CELL_SIZE + p.dx * CHUNK_SIZE,
                    self.y0 * CELL_SIZE + p.dy * CHUNK_SIZE,
                    p.z0 * CHUNK_SIZE, p.rot)

    def _place(self, p, ch, rows, colour, ground, overlay,
               img, hmap, zbuf, top, pools, kinds):
        """Draw one laying wherever it is the highest thing at that point."""
        sy, sx = p.sy * ch, p.sx * ch
        sh, sw = p.sh * ch, p.sw * ch
        if sy + sh > colour.shape[0] or sx + sw > colour.shape[1]:
            return
        cut = (slice(sy, sy + sh), slice(sx, sx + sw))

        rgb = _orient(colour[cut], p.rot).copy()
        base = p.z0 * CHUNK_SIZE
        g = _orient(ground[cut], p.rot) + base
        stand = None
        surface = kind = None
        if overlay is not None:
            if overlay.cover is not None:
                a = _orient(overlay.cover[cut], p.rot)[:, :, None]
                rgb *= 1.0 - a
                rgb += _orient(overlay.rgb[cut], p.rot) * a
                stand = _orient(overlay.top[cut], p.rot)
            if overlay.surface is not None:
                here = _orient(overlay.surface[cut], p.rot)
                surface = np.where(here > NO_LIQUID, here + base, NO_LIQUID)
                kind = _orient(overlay.kind[cut], p.rot)

        dh, dw = rgb.shape[0], rgb.shape[1]
        # North is up in the image, so the last chunk row of the world is the
        # first row of the picture.
        iy = rows - (p.dy * ch) - dh
        ix = p.dx * ch
        if iy < 0 or ix < 0 or iy + dh > rows or ix + dw > img.shape[1]:
            return
        box = (slice(iy, iy + dh), slice(ix, ix + dw))

        visible = g if stand is None else g + stand
        win = visible > zbuf[box]
        if not win.any():
            return
        np.copyto(zbuf[box], visible, where=win)
        np.copyto(img[box], rgb, where=win[:, :, None])
        np.copyto(hmap[box], g, where=win)
        if top is not None and stand is not None:
            np.copyto(top[box], stand, where=win)
        elif top is not None:
            np.copyto(top[box], 0.0, where=win)
        if pools is not None and surface is not None:
            np.copyto(pools[box], surface, where=win)
            np.copyto(kinds[box], kind, where=win)

    def _dig(self, ch, rows, img, hmap, zbuf, top):
        """Cut the tunnels in along the path they were dug.

        The corridors are not tiles: the generator paths them through the rock
        between the caves and carves a tube along the path afterwards. Without
        them a drill floor is a scatter of rooms with nothing joining them,
        which is not what the floor is.

        The chunk raster the pather writes reserves a whole chunk per step, but
        what is actually cut is the cross-section carve_tunnels.lua sweeps --
        four metres or so either side of the line, wider for a rich vein -- so
        that is what is drawn. A corridor comes out the width it really is
        rather than four times it.
        """
        rgb_rows, cols = img.shape[0], img.shape[1]
        mpp = CELL_SIZE / self.px
        x0, y0 = self.x0 * CELL_SIZE, self.y1 * CELL_SIZE + CELL_SIZE
        for t in self.floor.tunnels:
            rgb = np.asarray(t.colour, dtype=np.float32)
            rad = max(1.2, t.width * 0.5 / mpp)
            for a, b in zip(t.points, t.points[1:]):
                for x, y, z in _walk(a, b, mpp):
                    # North at the top: the image runs south as it goes down.
                    cx = (x - x0) / mpp
                    cy = (y0 - y) / mpp
                    self.tunnel_pixels += _disc(
                        img, hmap, zbuf, top, cx, cy, rad,
                        float(z // CHUNK_SIZE) * CHUNK_SIZE, rgb)


def _walk(a, b, mpp):
    """Steps from a to b close enough together that the tube has no gaps."""
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    far = max(abs(dx), abs(dy), abs(dz))
    steps = max(1, int(far / (mpp * 0.7)) + 1)
    for i in range(steps + 1):
        f = i / float(steps)
        yield a[0] + dx * f, a[1] + dy * f, a[2] + dz * f


def _disc(img, hmap, zbuf, top, cx, cy, rad, z, rgb):
    """Stamp one round section of tunnel, if it is above whatever is there."""
    rows, cols = zbuf.shape
    x0 = max(0, int(cx - rad))
    x1 = min(cols, int(cx + rad) + 1)
    y0 = max(0, int(cy - rad))
    y1 = min(rows, int(cy + rad) + 1)
    if x0 >= x1 or y0 >= y1:
        return 0
    yy = (np.arange(y0, y1, dtype=np.float32) + 0.5 - cy)[:, None]
    xx = (np.arange(x0, x1, dtype=np.float32) + 0.5 - cx)[None, :]
    box = (slice(y0, y1), slice(x0, x1))
    win = (xx * xx + yy * yy <= rad * rad) & (z > zbuf[box])
    if not win.any():
        return 0
    np.copyto(zbuf[box], z, where=win)
    np.copyto(hmap[box], z, where=win)
    np.copyto(img[box], rgb, where=win[:, :, None])
    if top is not None:
        np.copyto(top[box], 0.0, where=win)
    return int(win.sum())
