"""smmap -- make a map of a Scrap Mechanic survival world.

Run it with no arguments: it finds the game, finds your saves, picks the one you
played last, renders the map and opens it.
"""

import argparse
import os
import sys
import time
import webbrowser

from . import discover
from . import palette
from . import savefile
from . import tiles
from .render import MapRenderer


def _fail(msg, hint=None):
    print("\n  %s" % msg)
    if hint:
        print("  %s" % hint)
    return 2


def _human_age(ts):
    if not ts:
        return "unknown"
    d = time.time() - ts
    for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if d >= n:
            v = int(d // n)
            return "%d %s%s ago" % (v, unit, "s" if v != 1 else "")
    return "just now"


def pick_save(saves, wanted=None):
    """Choose a save. Newest survival world unless the user named one."""
    if wanted:
        low = wanted.lower()
        exact = [s for s in saves if s.name.lower() == low]
        part = [s for s in saves if low in s.name.lower()]
        if exact:
            return exact[0]
        if part:
            return part[0]
        if os.path.isfile(wanted):
            return discover.Save(wanted)
        return None
    # Survival worlds live in the Survival subfolder; prefer the most recent.
    surv = [s for s in saves if s.folder.lower() == "survival"]
    return (surv or saves or [None])[0]


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="smmap",
        description="Render a top-down map of a Scrap Mechanic survival world.")
    ap.add_argument("save", nargs="?",
                    help="save name or .db path (default: your most recent survival world)")
    ap.add_argument("-o", "--out", help="output file (.html or .png)")
    ap.add_argument("--px", type=int, default=16,
                    help="pixels per 64 m cell (default 16)")
    ap.add_argument("--list", action="store_true", help="list saves and exit")
    ap.add_argument("--all", action="store_true", help="render every survival save")
    ap.add_argument("--png", action="store_true", help="write a .png only")
    ap.add_argument("--no-shade", action="store_true", help="disable hillshading")
    ap.add_argument("--no-open", action="store_true", help="do not open the result")
    ap.add_argument("--game", help="path to the Scrap Mechanic folder")
    args = ap.parse_args(argv)

    print("\n  Scrap Mechanic map")

    game = args.game or discover.find_game()
    if not game:
        return _fail("Could not find your Scrap Mechanic installation.",
                     "Pass it with --game \"C:\\...\\steamapps\\common\\Scrap Mechanic\"")
    print("  game   %s" % game)

    saves = discover.find_saves()
    if not saves:
        return _fail("No Scrap Mechanic saves found.",
                     "Expected them under %%APPDATA%%\\Axolot Games\\Scrap Mechanic\\User")

    if args.list:
        print("\n  %-28s %-10s %8s  %s" % ("SAVE", "FOLDER", "SIZE", "LAST PLAYED"))
        for s in saves:
            print("  %-28s %-10s %7.1fM  %s"
                  % (s.name[:28], s.folder[:10], s.size / 1e6, _human_age(s.mtime)))
        return 0

    index = tiles.TileIndex(game)
    if not index:
        return _fail("Found the game but no terrain tiles in it.",
                     "Is %s a complete install?" % game)

    if args.all:
        targets = [s for s in saves if s.folder.lower() == "survival"]
        if args.out:
            print("  (ignoring --out: --all writes one file per world)")
            args.out = None
    else:
        chosen = pick_save(saves, args.save)
        if chosen is None:
            return _fail("No save matching %r." % args.save,
                         "Run with --list to see what is available.")
        targets = [chosen]

    written = []
    for save in targets:
        out = render_one(save, index, args)
        if out:
            written.append(out)

    if not written:
        return _fail("Nothing was rendered.",
                     "Creative worlds have no overworld terrain; try a survival save.")

    if not args.no_open:
        webbrowser.open("file:///" + written[0].replace("\\", "/"))
    print("")
    return 0


def render_one(save, index, args):
    print("\n  world  %s  (%s, %.1f MB)"
          % (save.name, _human_age(save.mtime), save.size / 1e6))

    try:
        sf = savefile.SaveFile(save.path)
    except Exception as e:
        print("         ! could not open: %s" % e)
        return None

    with sf:
        t0 = time.time()
        cd = sf.cell_data()
        if cd is None:
            print("         ! no overworld terrain in this save (creative world?)")
            return None

        info = sf.game_info()
        b = cd["bounds"]
        w = int(b["xMax"]) - int(b["xMin"]) + 1
        h = int(b["yMax"]) - int(b["yMin"]) + 1
        print("         %d x %d cells  (%.1f x %.1f km)  seed %s"
              % (w, h, w * 64 / 1000.0, h * 64 / 1000.0, cd.get("seed", "?")))

        r = MapRenderer(cd, index, px=max(1, args.px))

        # Only animate a progress line on a real console; piped output should
        # not fill up with carriage returns.
        progress = None
        if sys.stdout.isatty():
            last = [-1]

            def progress(done, total):
                pct = int(done * 100 / total)
                if pct != last[0]:
                    last[0] = pct
                    bar = "#" * (pct // 5)
                    sys.stdout.write("\r         [%-20s] %3d%%" % (bar, pct))
                    sys.stdout.flush()

        arr = r.render(hillshade=not args.no_shade, progress=progress)
        if progress:
            sys.stdout.write("\r" + " " * 40 + "\r")
        print("         rendered in %.1fs" % (time.time() - t0))

        from PIL import Image
        img = Image.fromarray(arr)

        base = args.out
        if base:
            root, ext = os.path.splitext(base)
        else:
            root, ext = os.path.join(os.getcwd(), _safe(save.name) + "_map"), ""
        want_png = args.png or ext.lower() == ".png"

        if want_png:
            path = root + ".png"
            img.save(path, optimize=True)
        else:
            path = root + ".html"
            _write_viewer(path, img, r, cd, info, save)

        print("         %s  (%d x %d)" % (path, img.width, img.height))
        if r.missing:
            print("         note: %d tile id(s) not in this install were drawn purple"
                  % len(r.missing))
        return path


def _safe(name):
    return "".join(c if c.isalnum() or c in " ._-" else "_" for c in name).strip() or "world"


def _write_viewer(path, img, r, cd, info, save):
    from . import viewer

    biomes = {}
    for tname, n in r.used.items():
        biomes[tname] = n
    top = sorted(r.used.items(), key=lambda kv: -kv[1])[:1]

    # "Land" means above the water plane -- ocean cells do carry lake tiles, so
    # counting placed tiles would just say 100%.
    dry = float((r.height_map >= 0).mean())
    km2 = r.w * r.h * (64 * 64) / 1e6
    stats = [
        ("size", "%d x %d cells" % (r.w, r.h)),
        ("area", "%.1f x %.1f km" % (r.w * 64 / 1000.0, r.h * 64 / 1000.0)),
        ("seed", cd.get("seed", "?")),
        ("land", "%.0f%% (%.1f km²)" % (100.0 * dry, km2 * dry)),
        ("tiles", "%d cells, %d kinds" % (sum(r.used.values()), len(r.used))),
        ("saved", _human_age(save.mtime)),
    ]
    tick = info.get("gametick")
    if isinstance(tick, int) and tick > 0:
        stats.insert(5, ("played", "%.1f h" % (tick / 40.0 / 3600.0)))  # 40 ticks/s
    if top:
        stats.append(("most common", top[0][0]))

    legend = [
        ("Grass", palette.BASE_RGB),
        ("Sand", palette.MATERIAL_RGB[1]),
        ("Dirt", palette.MATERIAL_RGB[3]),
        ("Rock", palette.MATERIAL_RGB[5]),
        ("Water", palette.WATER_SHALLOW_RGB),
        ("Deep water", palette.WATER_DEEP_RGB),
    ]
    meta = {"w": img.width, "h": img.height, "px": r.px,
            "x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
    subtitle = "Scrap Mechanic survival world  ·  1 cell = 64 m"
    viewer.write_html(path, img, meta, stats, legend, save.name, subtitle)


if __name__ == "__main__":
    sys.exit(main())
