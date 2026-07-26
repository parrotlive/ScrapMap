"""Compose the world map image from the cell grid and the game's tile data."""

import numpy as np

from . import palette
from .detail import DetailBaker
from .smlua import Uuid


def _orient(block, rotation):
    """Put a tile's sample block into image orientation.

    Tile rows run south to north while image rows run north to south, so the
    block is flipped first; the cell's stored rotation is then applied as
    90-degree steps (RotateLocal in the game's terrain_util2.lua).

    Verified by scoring colour continuity across cell seams over a whole world:
    this orientation scores 1.4, every other flip/transpose/direction 6.1 or
    worse, against an in-cell reference of 3.3.
    """
    b = block[::-1]
    if rotation:
        b = np.rot90(b, rotation)
    return b


def _smooth(a, passes=1):
    """Cheap separable box blur, used only to condition the hillshade input."""
    for _ in range(passes):
        a = (a
             + np.roll(a, 1, 0) + np.roll(a, -1, 0)
             + np.roll(a, 1, 1) + np.roll(a, -1, 1)) * 0.2
    return a


def _resample(grid, d):
    """Bilinear resample an (M, M[, C]) tile grid to (d, d[, C]).

    The samples sit on the tile's corners, so a 65-sample grid spans 64
    intervals whatever the tile measures on the ground. Resampling the whole
    tile in one go rather than cell by cell is what keeps a large point of
    interest from breaking into 64 m squares.
    """
    m = grid.shape[0]
    if m == d:
        return grid
    u = (np.arange(d, dtype=np.float32) + 0.5) * (m - 1) / d
    i0 = np.floor(u).astype(np.int32)
    i1 = np.minimum(i0 + 1, m - 1)
    t = u - i0
    tr = t.reshape((-1,) + (1,) * (grid.ndim - 1)).astype(np.float32)
    rows = grid[i0] * (1.0 - tr) + grid[i1] * tr
    tc = t.reshape((1, -1) + (1,) * (grid.ndim - 2)).astype(np.float32)
    return rows[:, i0] * (1.0 - tc) + rows[:, i1] * tc


class MapRenderer(object):
    def __init__(self, cell_data, tile_index, px=32, asset_db=None):
        self.cd = cell_data
        self.tiles = tile_index
        self.px = px
        b = cell_data["bounds"]
        self.x0, self.x1 = int(b["xMin"]), int(b["xMax"])
        self.y0, self.y1 = int(b["yMin"]), int(b["yMax"])
        self.w = self.x1 - self.x0 + 1
        self.h = self.y1 - self.y0 + 1
        self.baker = DetailBaker(asset_db, px) if asset_db is not None else None
        self.missing = set()
        self.used = {}
        self.props = 0

    # -- per-tile data ----------------------------------------------------

    def _tile_arrays(self, tile):
        """Colour and ground height for a whole tile, at map resolution."""
        heights, mats = tile.lod(0)
        w = mats.astype(np.float32)                      # (S, S, 8)
        total = w.sum(axis=2, keepdims=True)             # (S, S, 1)

        mat_rgb = np.asarray(palette.MATERIAL_RGB, dtype=np.float32)   # (8, 3)
        layered = w.reshape(-1, 8).dot(mat_rgb).reshape(w.shape[0], w.shape[1], 3)
        layered /= np.where(total > 0, total, 1.0)

        # Weights are not normalised, so treat their sum as coverage of the base.
        cov = np.clip(total / 255.0, 0.0, 1.0)
        base = np.asarray(palette.BASE_RGB, dtype=np.float32)
        colour = base * (1.0 - cov) + layered * cov

        h = np.nan_to_num(heights.astype(np.float32), nan=0.0,
                          posinf=0.0, neginf=0.0)
        d = tile.size * self.px
        return _resample(colour, d), _resample(h, d)

    # -- elevation from the save -----------------------------------------

    def _elevation_grid(self):
        """Per-corner base height as a (h+1, w+1) array, in metres.

        Two things lift a cell off the datum: the save's own corner elevations,
        and its cliff level, which counts eight metre steps. The cliff level is
        stored per cell, so it is averaged onto the corners: a plateau then
        keeps its full height inside and ramps down across the ring of cells at
        its edge, which is where the game puts its cliff tiles. Added as a hard
        per-cell step instead it draws a bright and dark rectangle around every
        plateau in the world.
        """
        elev = self.cd.get("elevation") or {}
        out = np.zeros((self.h + 1, self.w + 1), dtype=np.float32)
        for j in range(self.h + 1):
            row = elev.get(self.y0 + j)
            if not row:
                continue
            for i in range(self.w + 1):
                v = row.get(self.x0 + i)
                if isinstance(v, (int, float)):
                    out[j, i] = float(v)

        cliff = self.cd.get("cliffLevel") or {}
        cells = np.zeros((self.h, self.w), dtype=np.float32)
        for j in range(self.h):
            row = cliff.get(self.y0 + j) or {}
            for i in range(self.w):
                cells[j, i] = float(row.get(self.x0 + i, 0) or 0)
        if cells.any():
            pad = np.pad(cells, 1, mode="edge")
            out += 2.0 * (pad[:-1, :-1] + pad[:-1, 1:] + pad[1:, :-1] + pad[1:, 1:])
        return out

    def _placements(self, img):
        """Group the world's cells by the tile that fills them.

        Working one tile at a time rather than one cell at a time means each
        tile is decoded, resampled and its props baked exactly once, however
        many cells it covers, and only one tile's worth of detail is ever held
        in memory.
        """
        uid = self.cd["uid"]
        rot = self.cd.get("rotation") or {}
        xoff = self.cd.get("xOffset") or {}
        yoff = self.cd.get("yOffset") or {}
        px = self.px
        groups = {}
        for j in range(self.h):
            cy = self.y0 + j
            urow = uid.get(cy) or {}
            rrow = rot.get(cy) or {}
            xrow = xoff.get(cy) or {}
            yrow = yoff.get(cy) or {}
            for i in range(self.w):
                cx = self.x0 + i
                u = urow.get(cx)
                if not isinstance(u, Uuid) or u.is_nil():
                    continue
                tile = self.tiles.get(u)
                if tile is None:
                    self.missing.add(str(u))
                    iy = (self.h - 1 - j) * px
                    img[iy:iy + px, i * px:(i + 1) * px] = palette.UNKNOWN_RGB
                    continue
                groups.setdefault(tile, []).append(
                    (i, j, int(xrow.get(cx, 0) or 0), int(yrow.get(cx, 0) or 0),
                     int(rrow.get(cx, 0) or 0) & 3))
        return groups

    # -- main -------------------------------------------------------------

    def render(self, hillshade=True, water=True, progress=None):
        px = self.px
        img = np.empty((self.h * px, self.w * px, 3), dtype=np.float32)
        img[:] = palette.BASE_RGB
        # Cells with no tile are open sea; give them a depth so they shade like it.
        hmap = np.full(img.shape[:2], -12.0, dtype=np.float32)
        top = np.zeros(img.shape[:2], dtype=np.float32) if self.baker else None

        elev = self._elevation_grid()
        groups = self._placements(img)
        total = sum(len(v) for v in groups.values())
        done = 0

        t = np.linspace(0.0, 1.0, px, dtype=np.float32)
        ty, tx = t[::-1][:, None], t[None, :]

        for tile, cells in groups.items():
            self.used[tile.name] = self.used.get(tile.name, 0) + len(cells)
            colour, ground = self._tile_arrays(tile)
            overlay = None
            if self.baker:
                seen = self.baker.drawn
                overlay = self.baker.bake(tile, ground)
                self.props += (self.baker.drawn - seen) * len(cells)

            for i, j, ox, oy, r in cells:
                iy, ix = (self.h - 1 - j) * px, i * px
                sy, sx = oy * px, ox * px
                if sy + px > colour.shape[0] or sx + px > colour.shape[1]:
                    continue
                cut = (slice(sy, sy + px), slice(sx, sx + px))
                img[iy:iy + px, ix:ix + px] = _orient(colour[cut], r)

                # The save's corner heights carry the world's large scale relief
                # and the tile's own field carries everything inside a cell.
                e00, e10 = elev[j, i], elev[j, i + 1]
                e01, e11 = elev[j + 1, i], elev[j + 1, i + 1]
                base = (e00 * (1 - tx) * (1 - ty) + e10 * tx * (1 - ty) +
                        e01 * (1 - tx) * ty + e11 * tx * ty)
                hmap[iy:iy + px, ix:ix + px] = (
                    _orient(ground[cut][:, :, None], r)[:, :, 0] + base)

                if overlay is not None:
                    orgb, ocov, oh = overlay
                    a = _orient(ocov[cut][:, :, None], r)
                    dst = img[iy:iy + px, ix:ix + px]
                    dst *= 1.0 - a
                    dst += _orient(orgb[cut], r) * a
                    top[iy:iy + px, ix:ix + px] = _orient(oh[cut][:, :, None], r)[:, :, 0]

            done += len(cells)
            if progress:
                progress(done, total)

        self.height_map = hmap

        if hillshade:
            # Cell elevation is interpolated per cell, so its slope steps at every
            # cell border. Smoothing before the gradient keeps the relief without
            # printing a 64 m grid over the whole map.
            shade = self._shade(_smooth(hmap, 2))
            # Underwater relief should not glint; flatten the shading there.
            shade = np.where(hmap < 0, 1.0 + (shade - 1.0) * 0.25, shade)
            img *= shade[:, :, None]

        if top is not None:
            self._light_structures(img, top)

        if water:
            img = self._apply_water(img, hmap, top)

        return np.clip(img, 0, 255).astype(np.uint8)

    def _light_structures(self, img, top):
        """Stand the props up off the ground with a rim light and a shadow.

        The map's light comes from the north west, so a wall's north-west edge
        catches it, its south-east edge falls into its own shade, and its shadow
        runs south east across the ground for as far as it is tall.
        """
        mpp = 64.0 / self.px
        rim = np.zeros_like(top)
        rim[1:, 1:] = top[1:, 1:] - top[:-1, :-1]
        img *= (1.0 + np.clip(rim / 7.0, -0.5, 0.5) * 0.75)[:, :, None]

        reach = max(int(round(16.0 / mpp)), 2)
        shadow = np.zeros(top.shape, dtype=np.float32)
        for k in range(1, reach + 1):
            # A prop shades a point k pixels to its south east only if it stands
            # that much taller than whatever is standing there.
            cast = top[:-k, :-k] - top[k:, k:] > k * mpp * 1.4
            far = shadow[k:, k:]
            np.maximum(far, cast * (1.0 - 0.6 * (k - 1) / reach), out=far)
        img *= (1.0 - 0.3 * shadow)[:, :, None]

    def _apply_water(self, img, hmap, top=None):
        """Everything below z=0 is under the world's water plane."""
        wet = hmap < 0.0
        if top is not None:
            # A pier or a silo standing in a lake is not itself underwater.
            wet &= (hmap + top) < 0.0
        if not wet.any():
            return img
        depth = np.clip(-hmap / 14.0, 0.0, 1.0)[:, :, None]
        shallow = np.asarray(palette.WATER_SHALLOW_RGB, dtype=np.float32)
        deep = np.asarray(palette.WATER_DEEP_RGB, dtype=np.float32)
        water_rgb = shallow * (1.0 - depth) + deep * depth
        # Let the lake bed show through in the shallows only.
        alpha = np.clip(0.62 + 0.38 * np.sqrt(depth) * 1.6, 0.0, 1.0)
        blended = img * (1.0 - alpha) + water_rgb * alpha
        return np.where(wet[:, :, None], blended, img)

    def _shade(self, hmap, strength=0.55):
        """Classic hillshade with the light from the north-west."""
        metres_per_px = 64.0 / self.px
        gy, gx = np.gradient(hmap, metres_per_px)
        # light direction (north-west, 45 degrees up)
        nz = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
        nx = -gx * nz
        ny = -gy * nz
        lx, ly, lz = -0.5, 0.5, 0.707
        norm = (lx * lx + ly * ly + lz * lz) ** 0.5
        lam = (nx * lx + ny * ly + nz * lz) / norm
        return np.clip(1.0 + (lam - 0.72) * strength, 0.55, 1.45)
