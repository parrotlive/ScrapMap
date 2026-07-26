"""Compose the world map image from the cell grid and the game's tile data."""

import numpy as np

from . import palette
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


def _resample(block, px):
    """Scale an (n, n, C) block to (px, px, C). n and px are both powers of two."""
    n = block.shape[0]
    if n == px:
        return block
    if n > px:
        f = n // px
        return block.reshape(px, f, px, f, block.shape[2]).mean(axis=(1, 3))
    f = px // n
    return np.repeat(np.repeat(block, f, axis=0), f, axis=1)


class MapRenderer(object):
    def __init__(self, cell_data, tile_index, px=16):
        self.cd = cell_data
        self.tiles = tile_index
        self.px = px
        b = cell_data["bounds"]
        self.x0, self.x1 = int(b["xMin"]), int(b["xMax"])
        self.y0, self.y1 = int(b["yMin"]), int(b["yMax"])
        self.w = self.x1 - self.x0 + 1
        self.h = self.y1 - self.y0 + 1
        self._colour_cache = {}
        self._height_cache = {}
        self.missing = set()
        self.used = {}

    # -- per-tile caches --------------------------------------------------

    def _tile_colour(self, tile):
        """(S, S, 3) float32 colours for a whole tile, blended from material weights."""
        key = tile.uuid
        c = self._colour_cache.get(key)
        if c is not None:
            return c
        _, mats = tile.lod(0)
        w = mats.astype(np.float32)                      # (S, S, 8)
        total = w.sum(axis=2, keepdims=True)             # (S, S, 1)

        mat_rgb = np.asarray(palette.MATERIAL_RGB, dtype=np.float32)   # (8, 3)
        layered = w.reshape(-1, 8).dot(mat_rgb).reshape(w.shape[0], w.shape[1], 3)
        safe = np.where(total > 0, total, 1.0)
        layered /= safe

        # Weights are not normalised, so treat their sum as coverage of the base.
        cov = np.clip(total / 255.0, 0.0, 1.0)
        base = np.asarray(palette.BASE_RGB, dtype=np.float32)
        c = base * (1.0 - cov) + layered * cov
        self._colour_cache[key] = c
        return c

    def _tile_height(self, tile):
        """(S, S) float32 heights resampled to the material grid resolution."""
        key = tile.uuid
        hh = self._height_cache.get(key)
        if hh is not None:
            return hh
        heights, mats = tile.lod(0)
        s = mats.shape[0]
        h = np.nan_to_num(heights.astype(np.float32), nan=0.0,
                          posinf=0.0, neginf=0.0)
        # heights sit on a (S//2+1) corner grid; stretch it onto the S cell grid.
        idx = np.clip((np.arange(s) * (h.shape[0] - 1) // max(s - 1, 1)), 0,
                      h.shape[0] - 1)
        hh = h[np.ix_(idx, idx)]
        self._height_cache[key] = hh
        return hh

    # -- elevation from the save -----------------------------------------

    def _elevation_grid(self):
        """Per-corner elevation as a (h+1, w+1) array, in metres."""
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
        return out

    # -- main -------------------------------------------------------------

    def render(self, hillshade=True, water=True, progress=None):
        px = self.px
        H, W = self.h * px, self.w * px
        img = np.empty((H, W, 3), dtype=np.float32)
        img[:] = palette.BASE_RGB
        hmap = np.zeros((H, W), dtype=np.float32)
        # Cells with no tile are open sea; give them a depth so they shade like it.
        hmap[:] = -12.0

        uid = self.cd["uid"]
        rot = self.cd.get("rotation") or {}
        xoff = self.cd.get("xOffset") or {}
        yoff = self.cd.get("yOffset") or {}
        cliff = self.cd.get("cliffLevel") or {}
        elev = self._elevation_grid()

        total_cells = self.w * self.h
        done = 0
        for j in range(self.h):
            cy = self.y0 + j
            urow = uid.get(cy) or {}
            rrow = rot.get(cy) or {}
            xrow = xoff.get(cy) or {}
            yrow = yoff.get(cy) or {}
            crow = cliff.get(cy) or {}
            # image row 0 is the north edge, world +y is north
            iy = (self.h - 1 - j) * px
            for i in range(self.w):
                done += 1
                cx = self.x0 + i
                u = urow.get(cx)
                if not isinstance(u, Uuid) or u.is_nil():
                    continue
                tile = self.tiles.get(u)
                if tile is None:
                    self.missing.add(str(u))
                    img[iy:iy + px, i * px:(i + 1) * px] = palette.UNKNOWN_RGB
                    continue
                self.used[tile.name] = self.used.get(tile.name, 0) + 1

                colours = self._tile_colour(tile)
                s = colours.shape[0]
                size = tile.size
                n = max((s - 1) // size, 1)          # samples per cell
                ox = int(xrow.get(cx, 0) or 0)
                oy = int(yrow.get(cx, 0) or 0)
                y_lo, x_lo = oy * n, ox * n
                block = colours[y_lo:y_lo + n, x_lo:x_lo + n]
                if block.shape[0] != n or block.shape[1] != n:
                    continue
                r = int(rrow.get(cx, 0) or 0) & 3
                block = _orient(block, r)
                img[iy:iy + px, i * px:(i + 1) * px] = _resample(block, px)

                th = self._tile_height(tile)[y_lo:y_lo + n, x_lo:x_lo + n]
                th = _orient(th[:, :, None], r)
                cell_h = _resample(th, px)[:, :, 0]
                # bilinear corner elevation across the cell + cliff steps
                e00, e10 = elev[j, i], elev[j, i + 1]
                e01, e11 = elev[j + 1, i], elev[j + 1, i + 1]
                t = np.linspace(0.0, 1.0, px, dtype=np.float32)
                ty = t[::-1][:, None]
                tx = t[None, :]
                base = (e00 * (1 - tx) * (1 - ty) + e10 * tx * (1 - ty) +
                        e01 * (1 - tx) * ty + e11 * tx * ty)
                cl = float(crow.get(cx, 0) or 0) * 8.0
                hmap[iy:iy + px, i * px:(i + 1) * px] = cell_h + base + cl

            if progress and (j % 8 == 0 or j == self.h - 1):
                progress(done, total_cells)

        self.height_map = hmap

        if hillshade:
            # Cell elevation is interpolated per cell, so its slope steps at every
            # cell border. Smoothing before the gradient keeps the relief without
            # printing a 64 m grid over the whole map.
            shade = self._shade(_smooth(hmap, 2))
            # Underwater relief should not glint; flatten the shading there.
            shade = np.where(hmap < 0, 1.0 + (shade - 1.0) * 0.25, shade)
            img *= shade[:, :, None]

        if water:
            img = self._apply_water(img, hmap)

        return np.clip(img, 0, 255).astype(np.uint8)

    def _apply_water(self, img, hmap):
        """Everything below z=0 is under the world's water plane."""
        wet = hmap < 0.0
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
