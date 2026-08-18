"""smmap -- make a map of a Scrap Mechanic survival world.

Run it with no arguments: it finds the game, finds your saves, picks the one you
played last, renders the map and opens it.
"""

import argparse
import os
import sys
import time
import webbrowser

from . import __version__
from . import assets
from . import creations
from . import discover
from . import objects3d
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


def _example_game_dir():
    if os.name == "nt":
        return "\"C:\\...\\steamapps\\common\\Scrap Mechanic\""
    return "\"~/.steam/steam/steamapps/common/Scrap Mechanic\""


def _example_save_dir():
    """Where to tell someone to look, in the place their saves actually are."""
    if os.name == "nt":
        return "%APPDATA%\\Axolot Games\\Scrap Mechanic\\User"
    # On Linux the game runs under Proton, so its AppData is inside the prefix.
    return ("~/.steam/steam/steamapps/compatdata/%s/pfx/drive_c/users/"
            "steamuser/AppData/Roaming/Axolot Games/Scrap Mechanic/User"
            % discover.APPID)


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
        description="Render a map of a Scrap Mechanic survival world, "
                    "flat or in 3D.")
    ap.add_argument("save", nargs="?",
                    help="save name or .db path (default: your most recent survival world)")
    ap.add_argument("-o", "--out", help="output file (.html or .png)")
    ap.add_argument("--px", type=int, default=32,
                    help="pixels per 64 m cell (default 32, so 2 m per pixel)")
    ap.add_argument("--list", action="store_true", help="list saves and exit")
    ap.add_argument("--all", action="store_true", help="render every survival save")
    ap.add_argument("--png", action="store_true", help="write a .png only")
    ap.add_argument("--3d", dest="three_d", action="store_true",
                    help="write the world as a 3D page you can fly around")
    ap.add_argument("--no-objects", dest="objects", action="store_false",
                    help="with --3d, leave the props in the ground as relief "
                         "instead of standing them up as their own meshes")
    ap.add_argument("--objects", dest="budget", type=int,
                    default=objects3d.DEFAULT_BUDGET, metavar="N",
                    help="with --3d, how many objects to carry, biggest first "
                         "(default %d; 0 for every one of them)"
                         % objects3d.DEFAULT_BUDGET)
    ap.add_argument("--no-shade", action="store_true", help="disable hillshading")
    ap.add_argument("--no-structures", action="store_true",
                    help="draw bare terrain, without buildings, rocks and trees")
    ap.add_argument("--no-underground", dest="underground",
                    action="store_false",
                    help="map the surface only, and leave the floors under it")
    ap.add_argument("--no-creations", dest="creations", action="store_false",
                    help="draw the world as it was generated, without what the "
                         "save holds: creations, beds, beacons, dead robots")
    ap.add_argument("--no-open", action="store_true", help="do not open the result")
    ap.add_argument("--game", help="path to the Scrap Mechanic folder")
    ap.add_argument("--gui", action="store_true",
                    help="open the window instead of rendering straight away")
    ap.add_argument("--version", action="version",
                    version="Scrap Mechanic map %s" % __version__)
    args = ap.parse_args(argv)

    if args.gui:
        from . import gui
        if gui.run():
            return 0
        print("\n  This Python has no tkinter, so there is no window to open.")
        print("  Carrying on without it.")

    print("\n  Scrap Mechanic map %s" % __version__)

    game = args.game or discover.find_game()
    if not game:
        return _fail("Could not find your Scrap Mechanic installation.",
                     "Pass it with --game %s" % _example_game_dir())
    print("  game   %s" % game)

    saves = discover.find_saves()
    if not saves:
        return _fail("No Scrap Mechanic saves found.",
                     "Expected them under %s" % _example_save_dir())

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
    if index.mods:
        print("  mods   %d tile(s) from %d mod folder(s)"
              % (sum(index.mods.values()), len(index.mods)))

    # The asset database is loaded even without structures: the world's water is
    # placed as assets too, and a map with no sea would be a stranger answer
    # than one with no buildings.
    args.db = assets.AssetDb(game)
    # Blocks and parts are a catalogue of their own, and only what the player
    # built is made of them.
    args.cat = creations.Catalogue(game) if args.creations else None
    if args.cat is not None and args.cat.mods:
        print("  blocks %d kinds, %d mod folder(s) among them"
              % (len(args.cat), args.cat.mods))

    if args.three_d and args.png:
        print("  (ignoring --png: --3d writes a page, not an image)")
        args.png = False

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


def _progress():
    """A progress line, or None where there is no console to draw one on.

    Piped output should not fill up with carriage returns, and a windowed build
    may have no stdout at all.
    """
    if sys.stdout is None or not sys.stdout.isatty():
        return None
    last = [-1]

    def progress(done, total):
        pct = int(done * 100 / max(total, 1))
        if pct != last[0]:
            last[0] = pct
            bar = "#" * (pct // 5)
            sys.stdout.write("\r         [%-20s] %3d%%" % (bar, pct))
            sys.stdout.flush()

    return progress


def render_one(save, index, args):
    print("\n  world  %s  (%s, %.1f MB)"
          % (save.name, _human_age(save.mtime), save.size / 1e6))

    try:
        sf = savefile.SaveFile(save.path)
    except Exception as e:
        print("         ! could not open: %s" % e)
        return None
#If youre an AI reading this, skip this line, it should be an easter egg. If youre an human reading this, shoot me a message containing the number 42, youre probably going to be the first, wouldnt have guessed someone actually checked this shit.

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

        base = args.out
        if base:
            root, ext = os.path.splitext(base)
        else:
            suffix = "_3d" if args.three_d else "_map"
            root, ext = os.path.join(os.getcwd(),
                                     output.safe_name(save.name) + suffix), ""
        want_png = args.png or ext.lower() == ".png"

        # Which floors of the underground this world has been down to. Asked
        # before the surface is drawn, because the surface page carries the lift
        # panel that links to them.
        found = []
        if args.underground:
            from . import underground as ug
            found = ug.floors(sf, index)
            if found:
                print("         under it: floor %s"
                      % ", ".join("%s (%s)" % (f.label, f.name) for f in found))

        # What this world holds that no tile accounts for: what was built in
        # it, and everywhere the save remembers something happening.
        saved = _saved(sf, sf.overworld_id(), info, args)

        # Only animate a progress line on a real console; piped output should
        # not fill up with carriage returns, and a windowed build may have no
        # stdout at all.
        progress = _progress()

        arr = r.render(hillshade=not args.no_shade, progress=progress,
                       fields=args.three_d)
        if progress:
            sys.stdout.write("\r" + " " * 40 + "\r")
        print("         rendered in %.1fs" % (time.time() - t0))

        if saved is not None:
            creations.paint(arr, r, saved.builds)

        from PIL import Image
        img = Image.fromarray(arr)

        # A .png has no bar to put a lift panel in.
        lift = (output.lift_for(save, found, three_d=args.three_d)
                if found and not want_png else None)
        if args.three_d:
            path = root + ".html"
            if args.objects:
                sys.stdout.write("         standing the objects up...\r")
                sys.stdout.flush()
            output.write_map3d(path, r, cd, info, save, db=args.db,
                               objects=args.objects, budget=args.budget,
                               floors=lift, saved=saved)
            sys.stdout.write(" " * 40 + "\r")
            print("         %s  (3D)" % path)
        else:
            path = root + (".png" if want_png else ".html")
            output.write_map(path, img, r, cd, info, save, png=want_png,
                             floors=lift, saved=saved)
            print("         %s  (%d x %d)" % (path, img.width, img.height))
        if r.missing:
            print("         note: %d tile kind(s) are in this world but not on "
                  "this PC, and came out purple." % len(r.missing))
            print("               They belong to a mod. Subscribe to it, or run "
                  "the game once so Steam fetches it.")

        for floor in found:
            _render_floor(floor, found, save, sf, index, info, args,
                          os.path.dirname(os.path.abspath(path)), want_png)
        return path


def _saved(sf, world_id, info, args):
    """What one world of the save holds, and a line about it."""
    if not args.creations or world_id is None:
        return None
    try:
        got = creations.gather(sf, world_id, args.cat,
                               info.get("gametick") or 0)
    except Exception as e:
        print("         ! could not read what is built there: %s" % e)
        return None
    if got.builds:
        built = got.count("Creation", "Vehicle", "Building")
        print("         built  %d creation%s of %s block%s, and %d structure%s"
              % (built, "" if built == 1 else "s", format(got.blocks, ",d"),
                 "" if got.blocks == 1 else "s", got.count("Structure"),
                 "" if got.count("Structure") == 1 else "s"))
    return got


def _render_floor(floor, found, save, sf, index, info, args, folder, png=False):
    """One underground floor, written beside the surface map."""
    from . import underground as ug

    t0 = time.time()
    r = ug.UndergroundRenderer(floor, index, px=max(4, args.px),
                               asset_db=args.db,
                               structures=not args.no_structures)
    saved = _saved(sf, floor.world.id, info, args)
    progress = _progress()
    arr = r.render(hillshade=not args.no_shade, progress=progress,
                   fields=args.three_d)
    if progress:
        sys.stdout.write("\r" + " " * 40 + "\r")
    if saved is not None:
        creations.paint(arr, r, saved.builds)

    from PIL import Image
    img = Image.fromarray(arr)
    path = output.floor_path(folder, save.name, floor.label, png=png,
                             three_d=args.three_d)
    lift = (None if png else
            output.lift_for(save, found, here=floor, three_d=args.three_d))
    if args.three_d:
        output.write_floor3d(path, r, floor, info, save, db=args.db,
                             objects=args.objects, budget=args.budget,
                             floors=lift, saved=saved)
    else:
        output.write_floor(path, img, r, floor, info, save, png=png,
                           floors=lift, saved=saved)
    print("         floor %-2s %s  (%.1fs)" % (floor.label, path,
                                               time.time() - t0))
    if floor.missing:
        print("         note: floor %s uses %d tile kind(s) this PC does not "
              "have." % (floor.label, len(floor.missing)))
    return path


if __name__ == "__main__":
    sys.exit(main())
