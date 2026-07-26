"""Bake the props standing on a tile into a top-down layer.

A tile's terrain is only ever 65x65 samples however big the tile is, so a 512 m
point of interest gets one ground sample every 8 metres. Everything that makes
such a place recognisable -- silos, warehouses, roads, ruins, trees -- is not in
the terrain at all but in the tile's prop list, which is why a map drawn from
the height field alone shows an empty field where a factory stands.

Each prop is reduced to the footprint of its collision mesh: the extreme points
of the mesh are rotated and scaled into world space, and the resulting outline
is filled with the prop's colour, deepest first, so a wall drawn over a bush
wins. The outline is approximated by its support function in twelve directions,
which is a convex polygon slightly larger than the true hull -- at two metres
per pixel the difference is invisible and it keeps the fill to a handful of
array operations per prop.

Standing liquid goes into its own layer. A pond, a chemical bath or a flooded
sump is a stretched box with a material rather than a shape, and it belongs with
the water rather than with the buildings.
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
# buried collision volumes do not print through the mountain above them. The
# same test gives a pond its shoreline: it is water only where its surface is
# above the ground it sits in.
_CLEARANCE = 0.25

# Where no liquid is placed the world's own plane at z = 0 is the only water.
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
    def __init__(self, db, px):
        self.db = db
        self.px = px
        self.mpp = 64.0 / px          # metres per pixel
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
        """Outlines in tile metres: (lo, hi, support values, top height)."""
        w = np.einsum("nij,nkj->nki", mat, local) + pos[:, None, :]
        xy = w[:, :, :2]
        sup = (xy.reshape(-1, 2) @ _DIRS.T
               ).reshape(len(local), -1, _NDIR).max(axis=1)
        return xy.min(axis=1), xy.max(axis=1), sup, w[:, :, 2].max(axis=1)

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
                solid.append(n)
            elif self.db.liquid(uuid):
                liquid.append(n)
        if not solid and not liquid:
            return None
        self.drawn += len(solid)
        self.liquids += len(liquid)

        rgb = np.zeros(ground.shape + (3,), dtype=np.float32)
        cover = np.zeros(ground.shape, dtype=np.float32)
        height = np.zeros(ground.shape, dtype=np.float32)
        surface = kind = None

        if solid:
            idx = np.asarray(solid)
            lo, hi, sup, top = self._footprints(
                np.stack([self._pad[props.uuid[i]] for i in solid]),
                props.pos[idx], props.mat[idx])
            colour = self._colours(props.uuid, props.tint, solid)
            # Props narrower than a pixel would fill nothing at all, and dropping
            # them would empty out every forest, so they are stamped as single
            # pixels in one pass instead of being rasterised.
            tiny = (hi - lo).max(axis=1) < 1.2 * self.mpp
            self._stamp(np.flatnonzero(tiny), (lo + hi) * 0.5, top, colour,
                        ground, rgb, cover, height)
            self._fill(np.flatnonzero(~tiny), lo, hi, sup, top, colour,
                       ground, rgb, cover, height)

        if liquid:
            idx = np.asarray(liquid)
            surface = np.full(ground.shape, NO_LIQUID, dtype=np.float32)
            kind = np.zeros(ground.shape, dtype=np.uint8)
            lo, hi, sup, top = self._footprints(
                np.broadcast_to(UNIT_BOX, (len(liquid),) + UNIT_BOX.shape),
                props.pos[idx], props.mat[idx])
            kinds = np.array([_kind_of(self.db.name(props.uuid[i])) for i in liquid],
                             dtype=np.uint8)
            self._flood(lo, hi, sup, top, kinds, ground, surface, kind)

        return Overlay(rgb, cover, height, surface, kind)

    # -- rasterising ------------------------------------------------------

    def _stamp(self, idx, mid, top, colour, ground, rgb, cover, height):
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
        height[cy, cx] = z

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

    def _fill(self, idx, lo, hi, sup, top, colour, ground, rgb, cover, height):
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
            np.copyto(height[a:b, c:e], z, where=inside)

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
            inside &= top[n] - ground[a:b, c:e] > _CLEARANCE
            inside &= surface[a:b, c:e] < top[n]
            np.copyto(surface[a:b, c:e], top[n], where=inside)
            np.copyto(kind[a:b, c:e], kinds[n], where=inside)
