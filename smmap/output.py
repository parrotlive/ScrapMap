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


def floor_path(folder, save_name, label, png=False, three_d=False):
    """Where one underground floor's page goes, beside the surface map."""
    stem = "%s_floor_%s" % (safe_name(save_name), safe_name(str(label)))
    if three_d:
        return os.path.join(folder, stem + "_3d.html")
    return os.path.join(folder, stem + (".png" if png else ".html"))


def write_map(path, img, r, cd, info, save, png=False, floors=None, saved=None):
    """Write the map to ``path``. Returns the path actually written."""
    if png:
        img.save(path, optimize=True)
        return path
    _write_viewer(path, img, r, cd, info, save, floors=floors, saved=saved)
    return path


def write_map3d(path, r, cd, info, save, db=None, objects=False,
                budget=None, progress=None, floors=None, saved=None):
    """Write the solid view of the same render. Needs render(fields=True).

    Nothing here re-reads the save or the game: the 3D page is made out of the
    very same render the flat map is made of, only taken a step earlier, before
    the height was flattened into shading.

    With ``objects`` the props go in as their own collision meshes rather than
    as bumps in the ground, which needs the asset database the render used.
    """
    from . import objects3d
    from . import poi
    from . import terrain3d
    from . import viewer3d

    if r.albedo is None:
        raise RuntimeError("the render did not keep its fields; "
                           "call MapRenderer.render(fields=True)")

    span = terrain3d.extent(r)
    solid = None
    if objects and db is not None and r.baker is not None:
        kw = {} if budget is None else {"budget": budget}
        solid = objects3d.collect(r, db, r.baker.loader, span,
                                  progress=progress,
                                  builds=saved.builds if saved else None, **kw)

    mine = _saved_places(saved, r)
    places = _placed(poi.collect(r) + mine, r, span)
    height, meta = terrain3d.payload(r, cd, objects=solid is not None)
    meta["yours"] = _yours(mine)
    prop = terrain3d.prop_texture(r) if solid is not None else None
    colour = terrain3d.colour_texture(r)

    stats = _world_stats(r, cd, info, save, saved)
    stats.insert(4, ("relief", "%.0f to %.0f m" % (meta["lo"], meta["hi"])))
    if solid is not None:
        s = solid["stats"]
        stats.append(("objects", "%s of %s placed"
                      % (format(s["drawn"], ",d"), format(s["placed"], ",d"))))
        stats.append(("geometry", "%s kinds, %s triangles"
                      % (format(s["meshes"], ",d"),
                         format(s["triangles"], ",d"))))
    if places:
        stats.append(("places", "%d in %d kinds"
                      % (len(places), len(poi.summary(places)))))
    viewer3d.write_html(path, colour, height, meta, stats, save.name,
                        "Scrap Mechanic survival world  ·  1 cell = 64 m",
                        prop=prop, objects=solid, places=places, floors=floors)
    return path


def _placed(places, r, span):
    """Put each place where the solid viewer lays the world out.

    The same frame objects3d puts the props in: east stays east, height stays
    height, and north becomes negative south, all measured from the middle of
    the padded extent the terrain mesh covers.
    """
    left = r.x0 * 64.0 + span[0] * 0.5
    top = (r.y1 + 1) * 64.0 - span[1] * 0.5
    for p in places:
        p["x"] = round((p["cx"] + 0.5) * 64.0 - left, 2)
        p["z"] = round(top - (p["cy"] + 0.5) * 64.0, 2)
        p["y"] = p.pop("h")
    return places


def _saved_places(saved, r):
    """The creations and the marks, as landmarks like any other."""
    from . import creations

    if saved is None:
        return []
    return creations.places(saved.builds, r) + saved.marks


def _yours(places):
    """Which kinds among ``places`` came out of the save rather than off a tile.

    Worked out from the list itself rather than kept as a table to be edited in
    step with poi.marks: what the save holds is whatever it holds, and a kind
    that appears there and nowhere else is one of yours by definition. The
    viewers group the legend on it, so that a world with one bag in it and four
    thousand tiles does not bury the bag.
    """
    return sorted({p["kind"] for p in places})


def _saved_stats(saved):
    """A line about what the save itself holds, if it holds anything."""
    if saved is None or not saved.builds:
        return []
    built = saved.count("Creation", "Vehicle", "Building")
    out = [("built", "%d creation%s, %s block%s"
            % (built, "" if built == 1 else "s",
               format(saved.blocks, ",d"), "" if saved.blocks == 1 else "s"))]
    welded = saved.count("Structure")
    if welded:
        out.append(("welded", "%d structure%s the world put up"
                    % (welded, "" if welded == 1 else "s")))
    return out


def _world_stats(r, cd, info, save, saved=None):
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
    stats += _saved_stats(saved)
    tick = info.get("gametick")
    if isinstance(tick, int) and tick > 0:
        stats.insert(5, ("played", "%.1f h" % (tick / 40.0 / 3600.0)))  # 40 ticks/s
    return stats


def _write_viewer(path, img, r, cd, info, save, floors=None, saved=None):
    from . import poi

    top = sorted(r.used.items(), key=lambda kv: -kv[1])[:1]
    stats = _world_stats(r, cd, info, save, saved)
    if top:
        stats.append(("most common", top[0][0]))
    mine = _saved_places(saved, r)
    places = poi.collect(r) + mine
    if places:
        stats.append(("places", "%d in %d kinds"
                      % (len(places), len(poi.summary(places)))))

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
    # Nothing goes in for the creations: the flat map paints every block in the
    # colour it was painted in the game, so there is no one swatch that means
    # "yours". The places legend is where they are picked apart.
    meta = {"w": img.width, "h": img.height, "px": r.px,
            "x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1,
            "yours": _yours(mine)}
    subtitle = "Scrap Mechanic survival world  ·  1 cell = 64 m"
    viewer.write_html(path, img, meta, stats, legend, save.name, subtitle,
                      places=places, floors=floors)


# -- the underground ------------------------------------------------------


def lift_for(save, found, here=None, three_d=False):
    """The lift panel for a world: the surface, then every floor under it.

    All eight floors are listed whether or not this save has been down to them,
    the way the lift itself shows all of its buttons. A floor the save has never
    generated gets no link and no name -- there is nothing to link to, and
    naming it would say what is down there.
    """
    from . import underground

    rows = [{
        "label": "S",
        "name": "Surface",
        "href": os.path.basename(default_path("", save.name, three_d=three_d)),
        "here": here is None,
    }]
    by_depth = {f.depth: f for f in found}
    for depth, (_cls, name, label) in enumerate(underground.FLOORS, 1):
        f = by_depth.get(depth)
        rows.append({
            "label": label,
            "name": name if f is not None else "",
            "href": ("" if f is None else os.path.basename(
                floor_path("", save.name, label, three_d=three_d))),
            "here": here is not None and f is not None and f.depth == here.depth,
        })
    return rows


def floor_title(save, floor):
    return "%s  ·  %s" % (save.name, floor.name)


def _floor_subtitle(floor):
    return ("Scrap Mechanic underground  ·  floor %s  ·  1 cell = 64 m"
            % floor.label)


def _floor_stats(r, floor, info, save, saved=None):
    """The facts about one floor, which are not the facts about a world."""
    from . import underground

    dug = getattr(r, "dug", None)
    share = float(dug.mean()) if dug is not None and dug.size else 0.0
    km2 = r.w * r.h * (64 * 64) / 1e6
    stats = [
        ("floor", "%s of %d  (%s)" % (floor.label, len(underground.FLOORS),
                                      floor.name)),
        ("size", "%d x %d cells" % (r.w, r.h)),
        ("area", "%.1f x %.1f km" % (r.w * 64 / 1000.0, r.h * 64 / 1000.0)),
        ("dug out", "%.0f%% (%.2f km²)" % (100.0 * share, km2 * share)),
        ("relief", "%.0f to %.0f m" % (r.floor_lo, r.floor_hi)),
        ("headroom", "%.0f m to the roof" % r.ceiling),
        ("tiles", "%d laid, %d kinds" % (sum(r.used.values()), len(r.used))),
        ("saved", human_age(save.mtime)),
    ]
    if floor.tunnels:
        metres = sum(t.length for t in floor.tunnels)
        stats.insert(6, ("tunnels", "%d, %.1f km of them"
                         % (len(floor.tunnels), metres / 1000.0)))
    if floor.spawners:
        stats.insert(6, ("spawners", "%d" % floor.spawners))
    if r.props:
        stats.append(("structures", "%s drawn" % format(r.props, ",d")))
    stats += _saved_stats(saved)
    return stats


def _floor_legend(r, floor):
    from . import underground

    legend = [("Bedrock", palette.BEDROCK_RGB),
              ("Cave floor", palette.CAVE_FLOOR_RGB)]
    seen = []
    for t in floor.tunnels:
        if t.kind not in [k for k, _ in seen]:
            seen.append((t.kind, t.colour))
    legend += seen[:5]
    if r.water_mask is not None and r.water_mask.any():
        legend.append(("Water", palette.WATER_SHALLOW_RGB))
    if r.props:
        legend += [("Buildings", assets.CATEGORY_RGB["build"]),
                   ("Rocks", assets.CATEGORY_RGB["rock"]),
                   ("Wrecks", assets.CATEGORY_RGB["wreck"]),
                   ("Plants", assets.CATEGORY_RGB["plant"])]
    return legend


def write_floor(path, img, r, floor, info, save, png=False, floors=None,
                saved=None):
    """One underground floor as a flat page, laid out like the surface map."""
    from . import poi

    if png:
        img.save(path, optimize=True)
        return path
    mine = _saved_places(saved, r)
    places = poi.underground(floor, r) + mine
    stats = _floor_stats(r, floor, info, save, saved)
    if places:
        stats.append(("places", "%d in %d kinds"
                      % (len(places), len(poi.summary(places)))))
    meta = {"w": img.width, "h": img.height, "px": r.px,
            "x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1,
            "yours": _yours(mine),
            # This is a floor, so a place is worth listing by how deep it is.
            "floors": True}
    viewer.write_html(path, img, meta, stats, _floor_legend(r, floor),
                      floor_title(save, floor), _floor_subtitle(floor),
                      places=places, floors=floors)
    return path


def write_floor3d(path, r, floor, info, save, db=None, objects=False,
                  budget=None, progress=None, floors=None, saved=None):
    """The same floor standing up, which is the only way to see it stacked."""
    from . import objects3d
    from . import poi
    from . import terrain3d
    from . import viewer3d

    if r.albedo is None:
        raise RuntimeError("the render did not keep its fields; "
                           "call render(fields=True)")

    span = terrain3d.extent(r)
    solid = None
    if objects and db is not None and r.baker is not None:
        kw = {} if budget is None else {"budget": budget}
        solid = objects3d.collect(r, db, r.baker.loader, span,
                                  progress=progress,
                                  builds=saved.builds if saved else None, **kw)

    mine = _saved_places(saved, r)
    places = _placed(poi.underground(floor, r) + mine, r, span)
    # Rock nobody dug has no surface to draw, so it is cut out rather than
    # laid over the top of everything as a lid.
    height, meta = terrain3d.payload(r, r.cd, objects=solid is not None,
                                     void=~r.dug)
    meta["yours"] = _yours(mine)
    prop = terrain3d.prop_texture(r) if solid is not None else None
    colour = terrain3d.colour_texture(r)

    stats = _floor_stats(r, floor, info, save, saved)
    if solid is not None:
        s = solid["stats"]
        stats.append(("objects", "%s of %s placed"
                      % (format(s["drawn"], ",d"), format(s["placed"], ",d"))))
    if places:
        stats.append(("places", "%d in %d kinds"
                      % (len(places), len(poi.summary(places)))))
    viewer3d.write_html(path, colour, height, meta, stats,
                        floor_title(save, floor), _floor_subtitle(floor),
                        prop=prop, objects=solid, places=places, floors=floors,
                        sky=viewer3d.CAVERN_SKY)
    return path
