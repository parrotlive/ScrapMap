"""Turn a finished render into the file the user keeps.

Both the command line and the window come through here, so the page they write
is the same page.
"""

import os
import sys
import time

from . import assets
from . import palette
from . import viewer


def default_folder():
    """Where a map lands if nobody says otherwise: next to the tool.

    Packaged, "the tool" is the executable. It is emphatically not the folder
    this file is in, which in a one-file build is a temporary directory that is
    deleted the moment the program exits, taking the map with it.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def human_age(ts):
    if not ts:
        return "unknown"
    d = time.time() - ts
    for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if d >= n:
            v = int(d // n)
            return "%d %s%s ago" % (v, unit, "s" if v != 1 else "")
    return "just now"


def safe_name(name):
    return "".join(c if c.isalnum() or c in " ._-" else "_"
                   for c in name).strip() or "world"


def default_path(folder, save_name, png=False, three_d=False):
    if three_d:
        return os.path.join(folder, safe_name(save_name) + "_3d.html")
    return os.path.join(folder, safe_name(save_name) + "_map"
                        + (".png" if png else ".html"))


def write_map(path, img, r, cd, info, save, png=False):
    """Write the map to ``path``. Returns the path actually written."""
    if png:
        img.save(path, optimize=True)
        return path
    _write_viewer(path, img, r, cd, info, save)
    return path


def write_map3d(path, r, cd, info, save):
    """Write the solid view of the same render. Needs render(fields=True).

    Nothing here re-reads the save or the game: the 3D page is made out of the
    very same render the flat map is made of, only taken a step earlier, before
    the height was flattened into shading.
    """
    from . import terrain3d
    from . import viewer3d

    if r.albedo is None:
        raise RuntimeError("the render did not keep its fields; "
                           "call MapRenderer.render(fields=True)")
    height, meta = terrain3d.payload(r, cd)
    colour = terrain3d.colour_texture(r)
    stats = _world_stats(r, cd, info, save)
    stats.insert(4, ("relief", "%.0f to %.0f m" % (meta["lo"], meta["hi"])))
    viewer3d.write_html(path, colour, height, meta, stats, save.name,
                        "Scrap Mechanic survival world  ·  1 cell = 64 m")
    return path


def _world_stats(r, cd, info, save):
    """The facts about a world, for whichever viewer is being written."""
    # "Land" is everything the world's water does not cover; counting placed
    # tiles would just say 100%, since the ocean is made of tiles too.
    wet = r.water_mask
    dry = 1.0 - (float(wet.mean()) if wet is not None else 0.0)
    km2 = r.w * r.h * (64 * 64) / 1e6
    stats = [
        ("size", "%d x %d cells" % (r.w, r.h)),
        ("area", "%.1f x %.1f km" % (r.w * 64 / 1000.0, r.h * 64 / 1000.0)),
        ("seed", cd.get("seed", "?")),
        ("land", "%.0f%% (%.1f km²)" % (100.0 * dry, km2 * dry)),
        ("tiles", "%d cells, %d kinds" % (sum(r.used.values()), len(r.used))),
        ("saved", human_age(save.mtime)),
    ]
    if r.props:
        stats.append(("structures", "%s drawn" % format(r.props, ",d")))
    tick = info.get("gametick")
    if isinstance(tick, int) and tick > 0:
        stats.insert(5, ("played", "%.1f h" % (tick / 40.0 / 3600.0)))  # 40 ticks/s
    return stats


def _write_viewer(path, img, r, cd, info, save):
    top = sorted(r.used.items(), key=lambda kv: -kv[1])[:1]
    stats = _world_stats(r, cd, info, save)
    if top:
        stats.append(("most common", top[0][0]))

    legend = [
        ("Grass", palette.BASE_RGB),
        ("Sand", palette.MATERIAL_RGB[1]),
        ("Dirt", palette.MATERIAL_RGB[3]),
        ("Water", palette.WATER_SHALLOW_RGB),
        ("Deep water", palette.WATER_DEEP_RGB),
    ]
    if r.props:
        legend += [("Buildings", assets.CATEGORY_RGB["build"]),
                   ("Roads", assets.CATEGORY_RGB["road"]),
                   ("Rocks", assets.CATEGORY_RGB["rock"]),
                   ("Trees", assets.CATEGORY_RGB["plant"])]
    meta = {"w": img.width, "h": img.height, "px": r.px,
            "x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
    subtitle = "Scrap Mechanic survival world  ·  1 cell = 64 m"
    viewer.write_html(path, img, meta, stats, legend, save.name, subtitle)
