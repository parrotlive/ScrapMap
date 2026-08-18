"""Put every object the world generator placed into the scene as real geometry.

The flat map reduces a prop to a filled outline and a height, which is the
right answer for a picture you look down on. In three dimensions it is not: a
warehouse should be a warehouse from any angle, and the only way to do that is
to put the warehouse there.

What goes in is the collision mesh -- the shape the game itself collides
against, in plain .obj beside every asset in the catalogue. It is not the art
the game draws: no textures, fewer faces, and a tree is a trunk and a cone
rather than every leaf. Reading the engine's own renderable meshes would need
its binary mesh format decoded, which is a different piece of work. But the
collision mesh is the real shape of the real object, at the real place, at the
real size, and it is enough for a world you can recognise a district by.

Meshes are shared, placements are not: a world has a few hundred distinct
assets and a few hundred thousand to a few million of them standing about, so
each mesh goes up once and each instance is a transform and a colour.
"""

import numpy as np

from .assets import CATEGORY_RGB, DEFAULT_CATEGORY

CELL = 64.0

# One instance is a transform, a colour and a size, interleaved for the GPU:
#   0  position          3 x float32   (12)
#   12 rotate/scale rows 9 x float16   (18)
#   30 colour            3 x uint8     (3)
#   33 radius, metres    1 x uint8     (1)
#   34 padding           (2)
# 36 bytes, which keeps the float at the front of every record 4-byte aligned.
STRIDE = 36

# How many objects to keep by default. Every one of them is in principle
# drawable, but the page has to carry them: at 36 bytes an instance, and base64
# on top, a million objects is a fifty megabyte file. Sorted biggest first,
# a few hundred thousand keeps every building, ruin, rock and tree in a world
# and spends what it drops on the smallest scattered foliage.
DEFAULT_BUDGET = 800_000

# A prop this small is scenery in the literal sense -- a pebble, a tuft -- and
# a whole world of them costs more than it shows. It is deliberately not applied
# to what a player built: a block is a quarter of a metre and every one of them
# was put there on purpose.
MIN_RADIUS = 0.35

# How many blocks and parts of the save's own bodies to stand up. They are
# cheaper than props -- one shared cube, one instance each -- but a well-played
# world holds hundreds of thousands, and whole creations are dropped rather than
# parts of one when there are more than this.
BUILD_BUDGET = 400_000

# The mesh every block is drawn as: the unit cube, corner at the origin, so a
# shape's matrix is its size and its position is where its low corner sits.
_CUBE = (np.array([(x, y, z) for x in (0.0, 1.0) for y in (0.0, 1.0)
                   for z in (0.0, 1.0)], np.float32),
         np.array([(0, 1, 3), (0, 3, 2), (4, 7, 5), (4, 6, 7),
                   (0, 5, 1), (0, 4, 5), (2, 3, 7), (2, 7, 6),
                   (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)], np.int32))

# How much of the category colour to mix into an object's own paint. The flat
# map leans on the category to keep a town legible at a kilometre; a solid
# object is its own shape already, so it mostly gets to be its own colour.
_CATEGORY_MIX = 0.15


def _place_xy(u, v, step, w=CELL, h=CELL):
    """Tile-local metres inside a piece of tile -> east and north inside it.

    Derived from render.py's _orient by following one sample through it: rows
    are flipped there because tile rows run south to north, and the rotation is
    then applied as whole quarter turns. Written out per case rather than as a
    matrix because these are exact and a rotation by 90 degrees in floating
    point is not.

    ``w`` and ``h`` are how big the piece is, which for the overworld is always
    one 64 m cell. Underground a pocket can be any number of 16 m chunks
    across, and a quarter turn then swaps the two.
    """
    if step == 1:
        return h - v, u
    if step == 2:
        return w - u, h - v
    if step == 3:
        return v, w - u
    return u, v


class Laid(object):
    """A tile laid down somewhere that is not one whole cell of a flat grid.

    The overworld only ever puts a tile in a cell, so a cell index and a
    rotation say everything. Underground a laying can be a few chunks of a tile
    dropped a quarter of the way into a cell and lifted eighty metres off the
    floor, so it carries its own rectangle, its own place and its own height.

    Everything is in metres: ``sx``/``sy`` and ``w``/``h`` cut the piece out of
    the tile, ``east``/``north`` are where its south-west corner lands in the
    world, and ``lift`` is how far the whole thing stands off the datum.
    """

    __slots__ = ("sx", "sy", "w", "h", "east", "north", "lift", "step")

    def __init__(self, sx, sy, w, h, east, north, lift, step):
        self.sx, self.sy, self.w, self.h = sx, sy, w, h
        self.east, self.north = east, north
        self.lift = lift
        self.step = step


# The same quarter turn as a matrix, to compose with each prop's own rotation.
_RZ = [np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], np.float32),
       np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], np.float32),
       np.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], np.float32),
       np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], np.float32)]

# World axes are east, north and up; the viewer's are east, up and south.
_TO_VIEWER = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], np.float32)


class MeshLibrary(object):
    """Every distinct collision mesh the world uses, packed back to back.

    Vertices go into one buffer and triangles into one index buffer holding
    indices into it, so a mesh is a range of that index buffer and the whole
    library binds once.
    """

    def __init__(self, db):
        self.db = db
        self.ids = {}
        self.verts = []
        self.index = []
        self.spans = []          # index count per mesh; starts follow on packing
        self.bounds = []         # (centre xyz, radius) per mesh, local frame
        self.uuids = []          # which asset each mesh came from, in mesh order
        self.named = {}          # mesh index -> (name, category) for non-assets
        self._verts = 0
        self._count = 0

    def id_of(self, uuid):
        """Index of this asset's mesh, adding it if this is the first sight."""
        hit = self.ids.get(uuid)
        if hit is not None:
            return hit
        # Whatever geometry the asset has. The undergrowth collides with
        # nothing, so for a quarter of everything a world places the art is the
        # only shape there is.
        m = self.db.any_mesh(uuid)
        if m is None:
            self.ids[uuid] = -1
            return -1
        return self._add(uuid, m[0], m[1])

    def shape_of(self, key, verts, tris, name, cat):
        """Index of a mesh that is not an asset, such as a block's cube."""
        hit = self.ids.get(key)
        if hit is not None:
            return hit
        out = self._add(key, verts, tris)
        self.named[out] = (name, cat)
        return out

    def _add(self, key, v, t):
        # Indices are rebased as the mesh goes in, so the whole library shares
        # one vertex buffer and one index buffer and binds exactly once.
        self.verts.append(v)
        self.index.append(t.reshape(-1) + self._verts)
        self._verts += len(v)
        lo, hi = v.min(axis=0), v.max(axis=0)
        centre = (lo + hi) * 0.5
        self.bounds.append((centre, float(np.linalg.norm(hi - centre))))
        self.spans.append(len(t) * 3)
        self.uuids.append(key)
        out = self._count
        self._count += 1
        self.ids[key] = out
        return out

    def pack(self):
        """(vertices float32, indices uint32, spans, centres, radii)."""
        if not self.verts:
            return None
        # Byte order is spelled out: these go into the page as raw bytes and
        # come back out as WebGL buffers, which are little-endian everywhere.
        verts = np.concatenate(self.verts).astype("<f4")
        index = np.concatenate(self.index).astype("<u4")
        spans, at = [], 0
        for n in self.spans:
            spans.append((at, n))
            at += n
        centres = np.array([b[0] for b in self.bounds], np.float32)
        radii = np.array([b[1] for b in self.bounds], np.float32)
        return verts, index, spans, centres, radii


class _TileProps(object):
    """One tile's props, prepared once however many cells the tile fills."""

    __slots__ = ("pos", "mat", "mesh", "rgb", "radius", "cells", "_box")

    def __init__(self, pos, mat, mesh, rgb, radius, cells):
        self.pos = pos
        self.mat = mat
        self.mesh = mesh
        self.rgb = rgb
        self.radius = radius
        self.cells = cells       # (ox, oy) -> indices into the arrays above
        self._box = None

    def inside(self, sx, sy, w, h):
        """Indices of the props standing in a rectangle of the tile.

        Only the underground asks: a cell of the overworld is a whole cell of a
        tile and the bucketing above already answers that. The mask is built the
        first time it is wanted, so a world that never uses it never pays.
        """
        if self._box is None:
            self._box = (self.pos[:, 0], self.pos[:, 1])
        x, y = self._box
        return np.flatnonzero((x >= sx) & (x < sx + w)
                              & (y >= sy) & (y < sy + h))


def _prepare(tile, loader, db, lib):
    """Everything about a tile's props that does not depend on where it lands."""
    props = loader.expand(tile.tileson)
    if not len(props):
        return None

    mesh = np.fromiter((lib.id_of(u) for u in props.uuid), np.int32, len(props))
    keep = np.flatnonzero(mesh >= 0)
    if not len(keep):
        return None

    pos = props.pos[keep]
    mat = props.mat[keep]
    mesh = mesh[keep]

    # A mesh's own size, scaled by what the placement does to it.
    radius = np.array([lib.bounds[m][1] for m in mesh], np.float32)
    radius = radius * np.sqrt((mat * mat).sum(axis=1)).max(axis=1)

    # Colour is per asset and per instance paint, and the props of one tile are
    # shared by every cell that tile fills, so it is worked out once here.
    rgb = np.empty((len(keep), 3), np.float32)
    cache = {}
    for n, k in enumerate(keep.tolist()):
        uuid = props.uuid[k]
        tint = props.tint[k]
        key = uuid if tint is None else (uuid, id(tint))
        c = cache.get(key)
        if c is None:
            cat = np.asarray(CATEGORY_RGB[db.category(uuid)], np.float32)
            own = db.colour(uuid, tint)
            c = cat if own is None else (np.asarray(own, np.float32)
                                         * (1.0 - _CATEGORY_MIX)
                                         + cat * _CATEGORY_MIX)
            cache[key] = c
        rgb[n] = c

    # Which 64 m cell of the tile each prop stands in. A one-cell tile puts
    # them all in (0, 0); a 512 m district spreads them over sixty-four.
    ox = np.floor(pos[:, 0] / CELL).astype(np.int32)
    oy = np.floor(pos[:, 1] / CELL).astype(np.int32)
    cells = {}
    for key in set(zip(ox.tolist(), oy.tolist())):
        cells[key] = np.flatnonzero((ox == key[0]) & (oy == key[1]))
    return _TileProps(pos, mat, mesh, rgb, radius, cells)


def collect(r, db, loader, span, budget=DEFAULT_BUDGET, progress=None,
            builds=None):
    """Every placed object in the world, in the viewer's own coordinates.

    ``r`` must have been rendered with fields=True, which is what leaves behind
    the cell-to-tile map and the elevation grid this walks. ``span`` is what the
    terrain mesh covers, from terrain3d.extent.

    ``builds`` are the bodies the save itself holds -- what the player built and
    what the world welded together -- which are placed by their own coordinates
    rather than by any tile, and so go in after the rest.
    """
    if not r.placements:
        return None
    lib = MeshLibrary(db)
    elev = r.elevation

    # The viewer lays the terrain out centred on the origin with the map's
    # north-west corner at its top left, so world coordinates come back to that
    # centre before they are handed over.
    left = r.x0 * CELL + span[0] * 0.5
    top = (r.y1 + 1) * CELL - span[1] * 0.5

    pos_out, mat_out, rgb_out, mesh_out, rad_out = [], [], [], [], []
    seen = 0
    done, total = 0, sum(len(v) for v in r.placements.values())

    for tile, cells in r.placements.items():
        tp = _prepare(tile, loader, db, lib)
        if tp is None:
            done += len(cells)
            if progress:
                progress(done, total)
            continue
        for cell in cells:
            if isinstance(cell, Laid):
                idx = tp.inside(cell.sx, cell.sy, cell.w, cell.h)
                if not len(idx):
                    continue
                seen += len(idx)
                p = tp.pos[idx]
                e, n = _place_xy(p[:, 0] - cell.sx, p[:, 1] - cell.sy,
                                 cell.step, cell.w, cell.h)
                base = cell.lift
                east = cell.east + e
                north = cell.north + n
                step = cell.step
            else:
                i, j, ox, oy, step = cell
                idx = tp.cells.get((ox, oy))
                if idx is None:
                    continue
                seen += len(idx)
                p = tp.pos[idx]
                u = p[:, 0] - ox * CELL
                v = p[:, 1] - oy * CELL
                e, n = _place_xy(u, v, step)

                # The save's corner elevations lift the whole cell; the prop's
                # own z is already in the tile's frame, the same one the ground
                # is in.
                fx, fy = e / CELL, n / CELL
                base = (elev[j, i] * (1 - fx) * (1 - fy)
                        + elev[j, i + 1] * fx * (1 - fy)
                        + elev[j + 1, i] * (1 - fx) * fy
                        + elev[j + 1, i + 1] * fx * fy)

                east = (r.x0 + i) * CELL + e
                north = (r.y0 + j) * CELL + n
            # East stays east, up stays up, and north becomes negative south,
            # which is the frame the terrain mesh is laid out in.
            pos_out.append(np.stack([east - left, p[:, 2] + base, top - north],
                                    axis=1).astype(np.float32))
            mat_out.append(np.einsum("ij,njk->nik",
                                     _TO_VIEWER @ _RZ[step & 3], tp.mat[idx]))
            rgb_out.append(tp.rgb[idx])
            mesh_out.append(tp.mesh[idx])
            rad_out.append(tp.radius[idx])
        done += len(cells)
        if progress:
            progress(done, total)

    if not pos_out:
        return None
    pos = np.concatenate(pos_out)
    mat = np.concatenate(mat_out).astype(np.float32)
    rgb = np.concatenate(rgb_out)
    mesh = np.concatenate(mesh_out)
    rad = np.concatenate(rad_out)

    keep = rad >= MIN_RADIUS
    if keep.sum() < len(rad):
        pos, mat, rgb, mesh, rad = (a[keep] for a in (pos, mat, rgb, mesh, rad))
    # Over budget, the biggest things stay. Dropping the small end loses the
    # scattered undergrowth and keeps everything that makes a place a place.
    if budget and len(rad) > budget:
        pick = np.argpartition(-rad, budget)[:budget]
        pos, mat, rgb, mesh, rad = (a[pick] for a in (pos, mat, rgb, mesh, rad))

    made = _bodies(lib, builds, left, top) if builds else None
    if made is not None:
        seen += len(made[0])
        pos, mat, rgb, mesh, rad = (np.concatenate([a, b]) for a, b in
                                    zip((pos, mat, rgb, mesh, rad), made))

    return _pack(lib, pos, mat, rgb, mesh, rad, seen)


def _bodies(lib, builds, left, top):
    """The save's own bodies as instances of one cube, block by block.

    One cube, but a mesh per kind of creation: the page draws a mesh at a time
    and names it in the legend, so a vehicle sharing a mesh with a warehouse is
    a vehicle that cannot be looked at on its own. They are the same twelve
    triangles either way -- what differs is which draw they land in.
    """
    from . import creations

    got = creations.boxes(builds, cap=BUILD_BUDGET)
    if got is None:
        return None
    pos, mat, rgb, rad, kind = got
    mesh = np.empty(len(pos), np.int32)
    for name in np.unique(kind):
        cat = creations.KINDS.get(name, (DEFAULT_CATEGORY,))[0]
        mesh[kind == name] = lib.shape_of("<block:%s>" % name, _CUBE[0],
                                          _CUBE[1], str(name), cat)
    # East stays east, up stays up, north becomes negative south: the same
    # change of frame every prop above goes through.
    out = np.stack([pos[:, 0] - left, pos[:, 2], top - pos[:, 1]], axis=1)
    return (out.astype(np.float32),
            np.einsum("ij,njk->nik", _TO_VIEWER, mat).astype(np.float32),
            rgb, mesh, rad.astype(np.float32))


def _pack(lib, pos, mat, rgb, mesh, rad, seen):
    """Interleave the instances and hand back everything the page needs."""
    packed = lib.pack()
    if packed is None:
        return None
    verts, index, spans, centres, radii = packed

    # One draw call per mesh, so instances of a mesh have to be neighbours.
    order = np.argsort(mesh, kind="stable")
    pos, mat, rgb, mesh, rad = (a[order] for a in (pos, mat, rgb, mesh, rad))
    counts = np.bincount(mesh, minlength=len(spans))
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])

    n = len(pos)
    buf = np.zeros((n, STRIDE), np.uint8)
    buf[:, 0:12] = pos.astype("<f4").view(np.uint8).reshape(n, 12)
    # Transposed on the way out, so the three vectors the shader reads are the
    # matrix's columns and GLSL's column-major mat3 rebuilds it as it stands.
    buf[:, 12:30] = (np.ascontiguousarray(mat.transpose(0, 2, 1)).reshape(n, 9)
                     .astype("<f2").view(np.uint8).reshape(n, 18))
    buf[:, 30:33] = np.clip(rgb, 0, 255).astype(np.uint8)
    buf[:, 33] = np.clip(np.ceil(rad), 1, 255).astype(np.uint8)

    # Each mesh is one asset, so a draw can say what it is drawing. That is what
    # lets the page offer a legend, a search box and a filter over the objects:
    # without it every one of them is an anonymous lump of triangles.
    db = lib.db
    draws = [{"start": int(s), "count": int(c),
              "index": int(spans[m][0]), "elems": int(spans[m][1]),
              "centre": [round(float(x), 4) for x in centres[m]],
              "radius": round(float(radii[m]), 4),
              "name": lib.named.get(m, (None,))[0]
                      or db.name(lib.uuids[m]) or "unnamed",
              "cat": lib.named[m][1] if m in lib.named
                     else db.category(lib.uuids[m])}
             for m, (s, c) in enumerate(zip(starts.tolist(), counts.tolist()))
             if c]
    return {
        "verts": verts,
        "index": index,
        "instances": buf.reshape(-1),
        "draws": draws,
        "stats": {"placed": int(seen), "drawn": n,
                  "meshes": len(draws),
                  "triangles": int(sum(d["elems"] // 3 * d["count"]
                                       for d in draws))},
    }
