"""Bake the props standing on a tile into a top-down layer.

Everything that makes a place recognisable -- silos, warehouses, fences, ruins,
rocks, trees -- stands on the ground rather than being part of it, so a map
drawn from the height field alone shows an empty field where a factory is.

Each prop is reduced to the footprint of its collision mesh: the extreme points
of the mesh are rotated and scaled into world space, and the resulting outline
is filled with the prop's colour, deepest first, so a wall drawn over a bush
wins. The outline is approximated by its support function in twelve directions,
which is a convex polygon slightly larger than the true hull -- at two metres
per pixel the difference is invisible and it keeps the fill to a handful of
array operations per prop.

Liquid goes into its own layer, and it is the only water there is. The world has
no sea plane: the ocean, every lake and every pond is a box a tile places, with
a material rather than a shape, and the top of that box is the surface. Ordinary
ground ripples a metre either side of zero, so drawing water wherever the ground
dips below it puddles half the meadows in the world.
"""

import numpy as np

from .assets import CATEGORY_RGB, UNIT_BOX
from .props import PropLoader

# Support directions for the footprint polygon. Twelve is enough that a rotated
# wall does not visibly gain corners.
_NDIR = 12
_ANG = np.arange(_NDIR, dtype=np.float32) * (2.0 * np.pi / _NDIR)
_DIRS = np.stack([np.cos(_ANG), np.sin(_ANG)], axis=1)

# Extreme points kept per collision mesh, matching assets._DIRS.
_NPTS = 26

# How much of a prop's own colour to keep. The rest is the map colour for its
# kind, which is what stops a town reading as noise.
_TINT = 0.42

# A prop has to stand clear of the ground to be drawn, so mine workings and
# buried collision volumes do not print through the mountain above them.
_CLEARANCE = 0.25

# A body of liquid needs no such margin: its shoreline is simply where the
# ground it sits in comes up through it. The small epsilon is only there so
# ground lying exactly at the surface reads as dry rather than as a film.
_WET = 0.05

# Nowhere is water unless a tile puts it there, so an unwritten pixel has to
# hold a level no ground can be under.
NO_LIQUID = -1e9

# Not every pool holds water: index into palette.LIQUID_RGB.
_KINDS = (("chemical", 1), ("oil", 2))


def _kind_of(name):
    low = name.lower()
    for word, kind in _KINDS:
        if word in low:
            return kind
    return 0


class Overlay(object):
    __slots__ = ("rgb", "cover", "top", "surface", "kind")

    def __init__(self, rgb, cover, top, surface, kind):
        self.rgb = rgb
        self.cover = cover
        self.top = top
        self.surface = surface
        self.kind = kind


class DetailBaker(object):
    def __init__(self, db, px, structures=True):
        self.db = db
        self.px = px
        self.mpp = 64.0 / px          # metres per pixel
        # Liquid is terrain, not decoration: it is baked even when the props
        # are not, because turning the buildings off should not drain the sea.
        self.structures = structures
        self.loader = PropLoader(db.game_dir)
        self._pad = {}
        self.drawn = 0
        self.liquids = 0

    def _points(self, uuid):
        """Collision extreme points padded to a fixed count, or None."""
        if uuid in self._pad:
            return self._pad[uuid]
        s = self.db.shape(uuid)
        # Repeating points cannot change a maximum, so padding is free here.
        p = None if s is None else np.resize(s, (_NPTS, 3)).astype(np.float32)
        self._pad[uuid] = p
        return p

    def _colours(self, uuids, tints, idx):
        """(n, 3) draw colour per prop, memoised by uuid within the tile."""
        cache = {}
        out = np.empty((len(idx), 3), dtype=np.float32)
        for n, i in enumerate(idx):
            uuid = uuids[i]
            c = cache.get(uuid)
            if c is None:
                cat = np.asarray(CATEGORY_RGB[self.db.category(uuid)], np.float32)
                own = self.db.colour(uuid, tints[i])
                c = cat if own is None else cat * (1.0 - _TINT) + np.asarray(own) * _TINT
                cache[uuid] = c
            out[n] = c
        return out

    def _footprints(self, local, pos, mat):
        """Outlines in tile metres: (lo, hi, support values, top, bottom)."""
        w = np.einsum("nij,nkj->nki", mat, local) + pos[:, None, :]
        xy = w[:, :, :2]
        sup = (xy.reshape(-1, 2) @ _DIRS.T
               ).reshape(len(local), -1, _NDIR).max(axis=1)
        return (xy.min(axis=1), xy.max(axis=1), sup,
                w[:, :, 2].max(axis=1), w[:, :, 2].min(axis=1))

    def _sunk(self, lo, hi, top, bot, surface):
        """Which props are more than half under a liquid, so the water covers them.

        A prop's height is one number for the whole of it, which is fine for a
        silo and wrong for the rock a lake is a hollow in: that is a bowl forty
        metres across whose rim breaks the surface, and taking the rim as its
        height everywhere inside its outline drains the lake. Whether the
        surface stands above the middle of a prop tells a pier from a basin.
        """
        if surface is None:
            return np.zeros(len(top), dtype=bool)
        d = surface.shape[0]
        cx = np.clip(((lo[:, 0] + hi[:, 0]) * 0.5 / self.mpp).astype(np.int32), 0, d - 1)
        cy = np.clip(((lo[:, 1] + hi[:, 1]) * 0.5 / self.mpp).astype(np.int32), 0, d - 1)
        return surface[cy, cx] > (top + bot) * 0.5

    def bake(self, tile, ground):
        """Overlay for one tile, or None if nothing is placed on it.

        ``ground`` is the tile's terrain height in metres at the same resolution
        as the result. ``top`` is how far each prop stands above that ground,
        which is what gives the map its relief and its shadows.
        """
        props = self.loader.expand(tile.tileson)
        solid, liquid = [], []
        for n, uuid in enumerate(props.uuid):
            if self._points(uuid) is not None:
                if self.structures:
                    solid.append(n)
            elif self.db.liquid(uuid):
                liquid.append(n)
        if not solid and not liquid:
            return None
        self.drawn += len(solid)
        self.liquids += len(liquid)

        rgb = cover = height = surface = kind = None

        # The liquid goes down first, because a prop needs to know whether it is
        # standing in it before it can say how tall it stands.
        if liquid:
            idx = np.asarray(liquid)
            surface = np.full(ground.shape, NO_LIQUID, dtype=np.float32)
            kind = np.zeros(ground.shape, dtype=np.uint8)
            lo, hi, sup, top, _ = self._footprints(
                np.broadcast_to(UNIT_BOX, (len(liquid),) + UNIT_BOX.shape),
                props.pos[idx], props.mat[idx])
            kinds = np.array([_kind_of(self.db.name(props.uuid[i])) for i in liquid],
                             dtype=np.uint8)
            self._flood(lo, hi, sup, top, kinds, ground, surface, kind)

        if solid:
            rgb = np.zeros(ground.shape + (3,), dtype=np.float32)
            cover = np.zeros(ground.shape, dtype=np.float32)
            height = np.zeros(ground.shape, dtype=np.float32)
            idx = np.asarray(solid)
            lo, hi, sup, top, bot = self._footprints(
                np.stack([self._pad[props.uuid[i]] for i in solid]),
                props.pos[idx], props.mat[idx])
            colour = self._colours(props.uuid, props.tint, solid)
            sunk = self._sunk(lo, hi, top, bot, surface)
            # Props narrower than a pixel would fill nothing at all, and dropping
            # them would empty out every forest, so they are stamped as single
            # pixels in one pass instead of being rasterised.
            tiny = (hi - lo).max(axis=1) < 1.2 * self.mpp
            self._stamp(np.flatnonzero(tiny), (lo + hi) * 0.5, top, colour,
                        sunk, ground, rgb, cover, height)
            self._fill(np.flatnonzero(~tiny), lo, hi, sup, top, colour,
                       sunk, ground, rgb, cover, height)

        return Overlay(rgb, cover, height, surface, kind)

    # -- rasterising ------------------------------------------------------

    def _stamp(self, idx, mid, top, colour, sunk, ground, rgb, cover, height):
        if not len(idx):
            return
        d = ground.shape[0]
        idx = idx[np.argsort(top[idx], kind="stable")]     # tallest wins
        cx = np.clip((mid[idx, 0] / self.mpp).astype(np.int32), 0, d - 1)
        cy = np.clip((mid[idx, 1] / self.mpp).astype(np.int32), 0, d - 1)
        z = top[idx] - ground[cy, cx]
        ok = z > _CLEARANCE
        cx, cy, z, idx = cx[ok], cy[ok], z[ok], idx[ok]
        rgb[cy, cx] = colour[idx]
        cover[cy, cx] = 1.0
        # A submerged prop is still drawn -- and then the water is drawn over it
        # -- but it stands no higher than the bed it is on.
        height[cy, cx] = np.where(sunk[idx], 0.0, z)

    def _boxes(self, idx, lo, hi, sup, d):
        """Pixel bounds and the pre-projected grid the polygon test needs."""
        mpp = self.mpp
        # Half a pixel of slack keeps thin walls from falling between samples.
        sup = sup + 0.5 * mpp
        x0 = np.clip(np.floor(lo[:, 0] / mpp).astype(np.int32), 0, d)
        y0 = np.clip(np.floor(lo[:, 1] / mpp).astype(np.int32), 0, d)
        x1 = np.clip(np.ceil(hi[:, 0] / mpp).astype(np.int32) + 1, 0, d)
        y1 = np.clip(np.ceil(hi[:, 1] / mpp).astype(np.int32) + 1, 0, d)
        centres = (np.arange(d, dtype=np.float32) + 0.5) * mpp
        return sup, x0, y0, x1, y1, centres[:, None] * _DIRS[:, 0], \
            centres[:, None] * _DIRS[:, 1]

    def _fill(self, idx, lo, hi, sup, top, colour, sunk, ground, rgb, cover, height):
        if not len(idx):
            return
        d = ground.shape[0]
        sup, x0, y0, x1, y1, px, py = self._boxes(idx, lo, hi, sup, d)
        for n in idx[np.argsort(top[idx], kind="stable")]:
            a, b, c, e = y0[n], y1[n], x0[n], x1[n]
            if a >= b or c >= e:
                continue
            inside = ((py[a:b, None, :] + px[None, c:e, :]) <= sup[n]).all(axis=2)
            z = top[n] - ground[a:b, c:e]
            inside &= z > _CLEARANCE
            if not inside.any():
                continue
            np.copyto(rgb[a:b, c:e], colour[n], where=inside[:, :, None])
            np.copyto(cover[a:b, c:e], 1.0, where=inside)
            np.copyto(height[a:b, c:e], 0.0 if sunk[n] else z, where=inside)

    def _flood(self, lo, hi, sup, top, kinds, ground, surface, kind):
        """Raise the liquid surface wherever a body of it covers the ground."""
        d = ground.shape[0]
        n_all = np.arange(len(top))
        sup, x0, y0, x1, y1, px, py = self._boxes(n_all, lo, hi, sup, d)
        for n in n_all:
            a, b, c, e = y0[n], y1[n], x0[n], x1[n]
            if a >= b or c >= e:
                continue
            inside = ((py[a:b, None, :] + px[None, c:e, :]) <= sup[n]).all(axis=2)
            inside &= top[n] - ground[a:b, c:e] > _WET
            inside &= surface[a:b, c:e] < top[n]
            np.copyto(surface[a:b, c:e], top[n], where=inside)
            np.copyto(kind[a:b, c:e], kinds[n], where=inside)
