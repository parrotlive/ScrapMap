"""smmap -- make a map of a Scrap Mechanic survival world.

Run it with no arguments: it finds the game, finds your saves, picks the one you
played last, renders the map and opens it.
"""

import argparse
import os
import sys
import time
import webbrowser

from . import assets
from . import discover
from . import output
from . import savefile
from . import tiles
from .output import human_age as _human_age
from .render import MapRenderer


def _fail(msg, hint=None):
    print("\n  %s" % msg)
    if hint:
        print("  %s" % hint)
    return 2


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
    ap.add_argument("--px", type=int, default=32,
                    help="pixels per 64 m cell (default 32, so 2 m per pixel)")
    ap.add_argument("--list", action="store_true", help="list saves and exit")
    ap.add_argument("--all", action="store_true", help="render every survival save")
    ap.add_argument("--png", action="store_true", help="write a .png only")
    ap.add_argument("--no-shade", action="store_true", help="disable hillshading")
    ap.add_argument("--no-structures", action="store_true",
                    help="draw bare terrain, without buildings, rocks and trees")
    ap.add_argument("--no-open", action="store_true", help="do not open the result")
    ap.add_argument("--game", help="path to the Scrap Mechanic folder")
    ap.add_argument("--gui", action="store_true",
                    help="open the window instead of rendering straight away")
    args = ap.parse_args(argv)

    if args.gui:
        from . import gui
        if gui.run():
            return 0
        print("\n  This Python has no tkinter, so there is no window to open.")
        print("  Carrying on without it.")

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

    # The asset database is loaded even without structures: the world's water is
    # placed as assets too, and a map with no sea would be a stranger answer
    # than one with no buildings.
    args.db = assets.AssetDb(game)

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

        r = MapRenderer(cd, index, px=max(1, args.px), asset_db=args.db,
                        structures=not args.no_structures)

        # Only animate a progress line on a real console; piped output should
        # not fill up with carriage returns, and a windowed build may have no
        # stdout at all.
        progress = None
        if sys.stdout is not None and sys.stdout.isatty():
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
            root, ext = os.path.join(os.getcwd(),
                                     output.safe_name(save.name) + "_map"), ""
        want_png = args.png or ext.lower() == ".png"
        path = root + (".png" if want_png else ".html")
        output.write_map(path, img, r, cd, info, save, png=want_png)

        print("         %s  (%d x %d)" % (path, img.width, img.height))
        if r.missing:
            print("         note: %d tile id(s) not in this install were drawn purple"
                  % len(r.missing))
        return path


if __name__ == "__main__":
    sys.exit(main())
