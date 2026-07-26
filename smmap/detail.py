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
"""

import numpy as np

from .assets import CATEGORY_RGB, euler_matrices

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

# A prop has to stand clear of the ground to be drawn, so mine shafts and buried
# collision volumes do not print through the mountain above them.
_CLEARANCE = 0.25


class DetailBaker(object):
    def __init__(self, db, px):
        self.db = db
        self.px = px
        self.mpp = 64.0 / px          # metres per pixel
        self._pad = {}
        self.drawn = 0
        self.skipped = 0

    def _points(self, uuid):
        """Collision extreme points padded to a fixed count, or None."""
        if uuid in self._pad:
            return self._pad[uuid]
        s = self.db.shape(uuid)
        # Repeating points cannot change a maximum, so padding is free here.
        p = None if s is None else np.resize(s, (_NPTS, 3)).astype(np.float32)
        self._pad[uuid] = p
        return p

    def _colours(self, props):
        """(N, 3) draw colour per prop, memoised by uuid within the tile."""
        cache = {}
        out = np.empty((len(props), 3), dtype=np.float32)
        for n, (uuid, _, _, _, cmap) in enumerate(props):
            c = cache.get(uuid)
            if c is None:
                cat = np.asarray(CATEGORY_RGB[self.db.category(uuid)], np.float32)
                own = self.db.colour(uuid, cmap)
                c = cat if own is None else cat * (1.0 - _TINT) + np.asarray(own) * _TINT
                cache[uuid] = c
            out[n] = c
        return out

    def bake(self, tile, ground):
        """Overlay for one tile as (rgb, cover, top), or None if it has no props.

        ``ground`` is the tile's terrain height in metres at the same resolution
        as the result. ``top`` is how far each prop stands above that ground,
        which is what gives the map its relief and its shadows.
        """
        props = [p for p in tile.props() if self._points(p[0]) is not None]
        self.skipped += len(tile.props()) - len(props)
        if not props:
            return None
        self.drawn += len(props)

        d = ground.shape[0]
        mpp = self.mpp
        pos = np.array([p[1] for p in props], dtype=np.float32)
        rot = np.array([p[2] for p in props], dtype=np.float32)
        scale = np.array([p[3] for p in props], dtype=np.float32)
        pts = np.stack([self._pad[p[0]] for p in props])

        # local mesh -> world, in tile metres
        pts = np.einsum("nij,nkj->nki", euler_matrices(rot), pts * scale[:, None, :])
        pts += pos[:, None, :]

        top = pts[:, :, 2].max(axis=1)
        xy = pts[:, :, :2]
        lo = xy.min(axis=1)
        hi = xy.max(axis=1)
        # Support value per direction: the polygon is {p : p . dir <= support}.
        sup = (xy.reshape(-1, 2) @ _DIRS.T).reshape(len(props), _NPTS, _NDIR).max(axis=1)

        rgb = np.zeros(ground.shape + (3,), dtype=np.float32)
        cover = np.zeros(ground.shape, dtype=np.float32)
        height = np.zeros(ground.shape, dtype=np.float32)
        colour = self._colours(props)

        # Props narrower than a pixel would fill nothing at all, and dropping
        # them would empty out every forest, so they are stamped as single
        # pixels in one pass instead of being rasterised.
        tiny = (hi - lo).max(axis=1) < 1.2 * mpp
        big = ~tiny
        self._stamp(np.flatnonzero(tiny), (lo + hi) * 0.5, top, colour,
                    ground, rgb, cover, height, d)
        self._fill(np.flatnonzero(big), lo, hi, sup, top, colour,
                   ground, rgb, cover, height, d)
        return rgb, cover, height

    def _stamp(self, idx, mid, top, colour, ground, rgb, cover, height, d):
        if not len(idx):
            return
        idx = idx[np.argsort(top[idx], kind="stable")]     # tallest wins
        cx = np.clip((mid[idx, 0] / self.mpp).astype(np.int32), 0, d - 1)
        cy = np.clip((mid[idx, 1] / self.mpp).astype(np.int32), 0, d - 1)
        z = top[idx] - ground[cy, cx]
        ok = z > _CLEARANCE
        cx, cy, z, idx = cx[ok], cy[ok], z[ok], idx[ok]
        rgb[cy, cx] = colour[idx]
        cover[cy, cx] = 1.0
        height[cy, cx] = z

    def _fill(self, idx, lo, hi, sup, top, colour, ground, rgb, cover, height, d):
        if not len(idx):
            return
        mpp = self.mpp
        # Half a pixel of slack keeps thin walls from falling between samples.
        sup = sup + 0.5 * mpp
        x0 = np.clip(np.floor(lo[:, 0] / mpp).astype(np.int32), 0, d)
        y0 = np.clip(np.floor(lo[:, 1] / mpp).astype(np.int32), 0, d)
        x1 = np.clip(np.ceil(hi[:, 0] / mpp).astype(np.int32) + 1, 0, d)
        y1 = np.clip(np.ceil(hi[:, 1] / mpp).astype(np.int32) + 1, 0, d)

        # Pre-projected pixel centres, so the polygon test is one add and one
        # compare however many props share a tile.
        centres = (np.arange(d, dtype=np.float32) + 0.5) * mpp
        px = centres[:, None] * _DIRS[:, 0]
        py = centres[:, None] * _DIRS[:, 1]

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
