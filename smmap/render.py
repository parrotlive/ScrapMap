"""Compose the world map image from the cell grid and the game's tile data."""

import numpy as np

from . import palette
from .detail import NO_LIQUID, DetailBaker
from .smlua import Uuid


def _orient(block, rotation):
    """Put a tile's sample block into image orientation.

    Tile rows run south to north while image rows run north to south, so the
    block is flipped first; the cell's stored rotation is then applied as
    90-degree steps (RotateLocal in the game's terrain_util2.lua).

    Verified by scoring colour continuity across cell seams over a whole world:
    this orientation scores 1.4, every other flip/transpose/direction 6.1 or
    worse, against an in-cell reference of 3.3.

    Written out as slices rather than as np.rot90 because it runs six times per
    cell of the map and rot90's argument checking costs more than the work.
    """
    b = block[::-1]
    if rotation == 1:
        return b.swapaxes(0, 1)[::-1]
    if rotation == 2:
        return b[::-1, ::-1]
    if rotation == 3:
        return b[::-1].swapaxes(0, 1)
    return b


def _tally(counts, block):
    """Count up the liquid levels in one cell, to a centimetre."""
    vals, n = np.unique(block, return_counts=True)
    for v, c in zip(vals.tolist(), n.tolist()):
        if v > NO_LIQUID:
            key = round(v, 2)
            counts[key] = counts.get(key, 0) + c


def _smooth(a, passes=1):
    """Cheap separable box blur, used only to condition the hillshade input."""
    for _ in range(passes):
        a = (a
             + np.roll(a, 1, 0) + np.roll(a, -1, 0)
             + np.roll(a, 1, 1) + np.roll(a, -1, 1)) * 0.2
    return a


def _resample(grid, d):
    """Resample an (M, M[, C]) sample grid to (d, d[, C]).

    Samples sit on corners, so a 65-sample grid spans 64 intervals. When those
    intervals divide evenly by the target -- 65 samples into 32 pixels, which is
    the default -- the answer is an exact box average of the data; otherwise it
    falls back to bilinear.
    """
    m = grid.shape[0]
    if m == d:
        return grid
    f = (m - 1) // d
    if f >= 1 and f * d == m - 1:
        if f == 1:
            return grid[:d, :d]
        return grid[:f * d, :f * d].reshape(
            (d, f, d, f) + grid.shape[2:]).mean(axis=(1, 3))
    u = (np.arange(d, dtype=np.float32) + 0.5) * (m - 1) / d
    i0 = np.floor(u).astype(np.int32)
    i1 = np.minimum(i0 + 1, m - 1)
    t = u - i0
    tr = t.reshape((-1,) + (1,) * (grid.ndim - 1)).astype(np.float32)
    rows = grid[i0] * (1.0 - tr) + grid[i1] * tr
    tc = t.reshape((1, -1) + (1,) * (grid.ndim - 2)).astype(np.float32)
    return rows[:, i0] * (1.0 - tc) + rows[:, i1] * tc


class MapRenderer(object):
    def __init__(self, cell_data, tile_index, px=32, asset_db=None,
                 structures=True):
        self.cd = cell_data
        self.tiles = tile_index
        self.px = px
        b = cell_data["bounds"]
        self.x0, self.x1 = int(b["xMin"]), int(b["xMax"])
        self.y0, self.y1 = int(b["yMin"]), int(b["yMax"])
        self.w = self.x1 - self.x0 + 1
        self.h = self.y1 - self.y0 + 1
        self.baker = (DetailBaker(asset_db, px, structures)
                      if asset_db is not None else None)
        self.structures = structures and asset_db is not None
        self.missing = set()
        self.used = {}
        self.props = 0
        self.empty = []
        self.water_mask = None
        # Set by render(fields=True); see _keep.
        self.albedo = None
        self.top_map = None
        self.water_level = None
        self.water_kind = None
        self.elevation = None
        self.placements = None

    # -- per-tile data ----------------------------------------------------

    @staticmethod
    def _blend(mats):
        """Ground colour from a grid of material weights."""
        w = mats.astype(np.float32)                      # (S, S, 8)
        total = w.sum(axis=2, keepdims=True)             # (S, S, 1)
        mat_rgb = np.asarray(palette.MATERIAL_RGB, dtype=np.float32)   # (8, 3)
        layered = w.reshape(-1, 8).dot(mat_rgb).reshape(w.shape[0], w.shape[1], 3)
        layered /= np.where(total > 0, total, 1.0)
        # Weights are not normalised, so treat their sum as coverage of the base.
        cov = np.clip(total / 255.0, 0.0, 1.0)
        base = np.asarray(palette.BASE_RGB, dtype=np.float32)
        return base * (1.0 - cov) + layered * cov

    def _tile_arrays(self, tile):
        """Colour and ground height for a whole tile, at map resolution.

        Built from the tileson's per-cell grids where it has them -- a metre per
        material sample and two per height sample, however large the tile -- and
        from the tile's own single grid for the whole tile where it does not.
        """
        px = self.px
        d = tile.size * px
        colour = np.empty((d, d, 3), dtype=np.float32)
        ground = np.empty((d, d), dtype=np.float32)
        missing = []
        for oy in range(tile.size):
            for ox in range(tile.size):
                cell = tile.surface(ox, oy)
                if cell is None:
                    missing.append((ox, oy))
                    continue
                heights, mats = cell
                box = (slice(oy * px, (oy + 1) * px), slice(ox * px, (ox + 1) * px))
                colour[box] = _resample(self._blend(mats), px)
                ground[box] = _resample(np.nan_to_num(
                    heights.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0), px)
        tile.forget()
        if not missing:
            return colour, ground

        heights, mats = tile.lod(0)
        whole = _resample(self._blend(mats), d)
        whole_h = _resample(np.nan_to_num(heights.astype(np.float32), nan=0.0,
                                          posinf=0.0, neginf=0.0), d)
        for ox, oy in missing:
            box = (slice(oy * px, (oy + 1) * px), slice(ox * px, (ox + 1) * px))
            colour[box] = whole[box]
            ground[box] = whole_h[box]
        return colour, ground

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
        self.empty = []
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
                    self.empty.append((i, j))
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

    def render(self, hillshade=True, water=True, progress=None, fields=False):
        px = self.px
        img = np.empty((self.h * px, self.w * px, 3), dtype=np.float32)
        img[:] = palette.BASE_RGB
        # Cells with no tile are open sea; give them a depth so they shade like it.
        hmap = np.full(img.shape[:2], -12.0, dtype=np.float32)
        top = np.zeros(img.shape[:2], dtype=np.float32) if self.structures else None
        # Every drop of water in the world is a volume some tile places, so this
        # is the whole of it: the surface height wherever there is one at all.
        pools = np.full(img.shape[:2], NO_LIQUID, np.float32) if self.baker else None
        kinds = np.zeros(img.shape[:2], np.uint8) if self.baker else None

        elev = self._elevation_grid()
        groups = self._placements(img)
        if fields:
            # Which tile fills which cell, and how high the ground under it
            # sits. objects3d walks these again to put the props into the world
            # as real meshes rather than as a footprint and a height.
            self.elevation = elev
            self.placements = groups
        total = sum(len(v) for v in groups.values())
        done = 0
        # Only worth counting up the world's water levels if something is going
        # to ask what the sea level is.
        levels = {} if (self.empty and pools is not None) else None

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
                hmap[iy:iy + px, ix:ix + px] = _orient(ground[cut], r) + base

                if overlay is None:
                    continue
                if overlay.cover is not None:
                    a = _orient(overlay.cover[cut], r)[:, :, None]
                    dst = img[iy:iy + px, ix:ix + px]
                    dst *= 1.0 - a
                    dst += _orient(overlay.rgb[cut], r) * a
                    top[iy:iy + px, ix:ix + px] = _orient(overlay.top[cut], r)
                if overlay.surface is not None:
                    # The cell's elevation lifts the water it holds, but it must
                    # not touch the mark that says there is no water here: in
                    # float32 a sentinel of -1e9 survives a base of 32 m and
                    # does not survive one of 96 m, so on high ground a dry
                    # pixel would come out a hair above the sentinel and read as
                    # a lake a billion metres down. That is enough to stretch
                    # the world's height range to a billion metres and quantise
                    # every real hill in it to the same number.
                    here = _orient(overlay.surface[cut], r)
                    here = np.where(here > NO_LIQUID, here + base, NO_LIQUID)
                    pools[iy:iy + px, ix:ix + px] = here
                    kinds[iy:iy + px, ix:ix + px] = _orient(overlay.kind[cut], r)
                    if levels is not None:
                        _tally(levels, here)

            done += len(cells)
            if progress:
                progress(done, total)

        self.height_map = hmap
        # Water is only ever where a tile puts it. The one exception is a cell
        # the save has no tile for at all, which is open sea outside the world:
        # flood it to whatever level most of this world's water stands at.
        if pools is not None and self.empty and levels:
            sea = max(levels, key=levels.get)
            for i, j in self.empty:
                iy, ix = (self.h - 1 - j) * px, i * px
                pools[iy:iy + px, ix:ix + px] = sea
        level = 0.0 if pools is None else pools
        if fields:
            self._keep(img, hmap, top, level, kinds)

        if hillshade:
            # Cell elevation is interpolated per cell, so its slope steps at every
            # cell border. Smoothing before the gradient keeps the relief without
            # printing a 64 m grid over the whole map.
            shade = self._shade(_smooth(hmap, 2))
            # Underwater relief should not glint; flatten the shading there.
            shade = np.where(hmap < level, 1.0 + (shade - 1.0) * 0.25, shade)
            img *= shade[:, :, None]

        if top is not None:
            self._light_structures(img, top)

        if water:
            img = self._apply_water(img, hmap, top, level, kinds)

        return np.clip(img, 0, 255).astype(np.uint8)

    def _keep(self, img, hmap, top, level, kinds):
        """Hold on to the fields the map is made from, for the 3D view.

        Everything below this point in render() is a way of showing height on a
        flat picture -- the hillshade, the rim light on the props, the depth
        tint on the water. A viewer that has the height itself wants none of
        that: it wants the ground colour before anything was done to it, and the
        three heights that were folded away into shading.

        The colour is copied because the hillshade multiplies it in place. The
        rest is only ever read after this, so it is shared rather than copied --
        at two metres per pixel a full world is another 200 MB of float if each
        of these is duplicated.
        """
        self.albedo = np.clip(img, 0, 255).astype(np.uint8)
        self.height_map = hmap
        self.top_map = top
        # A world with no liquid at all reports its sea as a plane no ground can
        # reach, so the viewer can treat the two cases alike.
        self.water_level = (np.full(hmap.shape, NO_LIQUID, np.float32)
                            if np.isscalar(level) else level)
        self.water_kind = (np.zeros(hmap.shape, np.uint8) if kinds is None
                           else kinds)

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

    def _apply_water(self, img, hmap, top, level, kinds=None):
        """Everything a liquid surface covers is drawn as liquid, shaded by depth.

        ``level`` is that surface per pixel, and it is well below anything at all
        where no tile placed water, so dry land can never come out under it.
        """
        wet = hmap < level
        if top is not None:
            # A pier or a silo standing in a lake is not itself underwater.
            wet &= (hmap + top) < level
        self.water_mask = wet
        if not wet.any():
            return img
        depth = np.clip((level - hmap) / 14.0, 0.0, 1.0)[:, :, None]
        # Let the bed show through in the shallows only.
        alpha = np.clip(0.62 + 0.38 * np.sqrt(depth) * 1.6, 0.0, 1.0)

        def tint(rgb):
            shallow, deep = (np.asarray(c, dtype=np.float32) for c in rgb)
            return shallow * (1.0 - depth) + deep * depth

        out = np.where(wet[:, :, None],
                       img * (1.0 - alpha) + tint(palette.LIQUID_RGB[0]) * alpha, img)
        if kinds is None:
            return out
        # A chemical bath or an oil pool is not water and should not read as it.
        # There are only ever a handful, so they are mixed from the dry ground
        # again over the masked pixels rather than over the whole map.
        for n in range(1, len(palette.LIQUID_RGB)):
            here = wet & (kinds == n)
            if not here.any():
                continue
            a = alpha[here]
            out[here] = img[here] * (1.0 - a) + tint(palette.LIQUID_RGB[n])[here] * a
        return out

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
