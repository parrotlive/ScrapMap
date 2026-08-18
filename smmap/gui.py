"""A window for the map tool, for people who would rather not open a terminal.

It does exactly what the command line does and writes exactly the same file; all
it adds is somewhere to see the worlds you have, somewhere to put the options,
and a progress bar, since a full world is ten seconds at the usual detail and
forty at the finest.

The render runs on a worker thread and talks back through a queue, because
tkinter may only be touched from the thread that made the window, and a frozen
window is the surest way to look broken.
"""

import os
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser

from . import discover
from . import output

# Pixels per 64 m cell, and what that means on the ground.
DETAILS = [("Quick", 16, "4 m per pixel"),
           ("Normal", 32, "2 m per pixel"),
           ("Fine", 64, "1 m per pixel")]
DEFAULT_DETAIL = 1


class Cancelled(Exception):
    """Raised out of the progress callback to unwind a render."""


def _reveal(path):
    """Show a finished file in the file manager."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except OSError:
        webbrowser.open("file:///" + os.path.dirname(path).replace("\\", "/"))


class App(object):
    def __init__(self, root, tk, ttk, filedialog, messagebox):
        self.root, self.tk, self.ttk = root, tk, ttk
        self.filedialog, self.messagebox = filedialog, messagebox
        self.q = queue.Queue()
        self.worker = None
        self.stop = threading.Event()
        self.written = []
        self.saves = []
        self._cache = {}

        root.title("Scrap Mechanic map")
        root.minsize(660, 520)
        self._build()
        self._load_saves()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(80, self._pump)

    # -- layout -----------------------------------------------------------

    def _build(self):
        tk, ttk = self.tk, self.ttk
        pad = dict(padx=14, pady=(0, 10))

        head = ttk.Frame(self.root)
        head.pack(fill="x", padx=14, pady=(12, 8))
        ttk.Label(head, text="Scrap Mechanic map",
                  font=("Segoe UI", 15, "bold")).pack(anchor="w")
        self.sub = ttk.Label(head, foreground="#5a6472",
                             text="Pick a world and press Make map.")
        self.sub.pack(anchor="w")

        box = ttk.Frame(self.root)
        box.pack(fill="both", expand=True, **pad)
        cols = ("kind", "size", "played")
        self.tree = ttk.Treeview(box, columns=cols, selectmode="extended",
                                 height=8)
        self.tree.heading("#0", text="World", anchor="w")
        self.tree.heading("kind", text="Kind", anchor="w")
        self.tree.heading("size", text="Size", anchor="e")
        self.tree.heading("played", text="Last played", anchor="w")
        self.tree.column("#0", width=250, minwidth=140)
        self.tree.column("kind", width=90, anchor="w", stretch=False)
        self.tree.column("size", width=80, anchor="e", stretch=False)
        self.tree.column("played", width=140, anchor="w", stretch=False)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self._start())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._retime())

        opts = ttk.LabelFrame(self.root, text="Options")
        opts.pack(fill="x", **pad)
        row = ttk.Frame(opts)
        row.pack(fill="x", padx=10, pady=(8, 2))
        ttk.Label(row, text="Detail").pack(side="left")
        self.detail = tk.IntVar(value=DEFAULT_DETAIL)
        for i, (label, px, note) in enumerate(DETAILS):
            ttk.Radiobutton(row, text="%s  (%s)" % (label, note),
                            variable=self.detail, value=i,
                            command=self._retime).pack(side="left", padx=(12, 0))

        row = ttk.Frame(opts)
        row.pack(fill="x", padx=10, pady=2)
        self.structures = tk.BooleanVar(value=True)
        self.shade = tk.BooleanVar(value=True)
        self.png = tk.BooleanVar(value=False)
        self.three_d = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Buildings, rocks and trees",
                        variable=self.structures,
                        command=self._retime).pack(side="left")
        ttk.Checkbutton(row, text="Shaded relief",
                        variable=self.shade).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(row, text="Plain image instead of a page",
                        variable=self.png,
                        command=self._exclusive).pack(side="left", padx=(16, 0))

        row = ttk.Frame(opts)
        row.pack(fill="x", padx=10, pady=2)
        self.objects = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="In 3D, to fly around", variable=self.three_d,
                        command=self._exclusive).pack(side="left")
        ttk.Checkbutton(row, text="with every object as itself",
                        variable=self.objects).pack(side="left", padx=(16, 0))

        row = ttk.Frame(opts)
        row.pack(fill="x", padx=10, pady=2)
        self.underground = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="The floors under it too, where the world has "
                                  "been down there", variable=self.underground
                        ).pack(side="left")

        row = ttk.Frame(opts)
        row.pack(fill="x", padx=10, pady=2)
        self.creations = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="What is built in it: creations, beds, "
                                  "beacons, where robots died",
                        variable=self.creations).pack(side="left")

        row = ttk.Frame(opts)
        row.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Label(row, text="Save in").pack(side="left")
        self.folder = tk.StringVar(value=output.default_folder())
        ttk.Entry(row, textvariable=self.folder).pack(
            side="left", fill="x", expand=True, padx=8)
        ttk.Button(row, text="Browse", width=9,
                   command=self._browse).pack(side="left")

        foot = ttk.Frame(self.root)
        foot.pack(fill="x", padx=14, pady=(0, 14))
        self.progress = ttk.Progressbar(foot, mode="determinate", maximum=1000)
        self.progress.pack(fill="x")
        line = ttk.Frame(foot)
        line.pack(fill="x", pady=(8, 0))
        self.status = ttk.Label(line, text="", foreground="#5a6472")
        self.status.pack(side="left")
        self.go = ttk.Button(line, text="Make map", command=self._start,
                             default="active")
        self.go.pack(side="right")
        self.reveal = ttk.Button(line, text="Show the file", state="disabled",
                                 command=self._reveal_last)
        self.reveal.pack(side="right", padx=(0, 8))
        self.root.bind("<Return>", lambda e: self._start())

    # -- saves ------------------------------------------------------------

    def _load_saves(self):
        self.saves = discover.find_saves()
        for s in self.saves:
            kind = "Survival" if s.folder.lower() == "survival" else "Creative"
            self.tree.insert("", "end", text=s.name, values=(
                kind, "%.1f MB" % (s.size / 1e6), output.human_age(s.mtime)))
        if not self.saves:
            self._set_status("No Scrap Mechanic saves found on this PC.")
            self.go.state(["disabled"])
            return
        first = [i for i, s in enumerate(self.saves)
                 if s.folder.lower() == "survival"]
        pick = first[0] if first else 0
        item = self.tree.get_children()[pick]
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.tree.see(item)
        self._retime()

    def _chosen(self):
        idx = [self.tree.index(i) for i in self.tree.selection()]
        return [self.saves[i] for i in idx] or self.saves[:1]

    def _retime(self):
        """A rough idea of the wait, so a Fine render is not a surprise."""
        if self.worker and self.worker.is_alive():
            return
        n = max(len(self._chosen()), 1)
        # Fitted to a full 9.2 x 7.2 km world, which is what a survival world
        # is: 5, 10 and 40 seconds for the three levels of detail. The constant
        # is the prefab trees, which cost the same whatever the pixel; the
        # square term is packing the image into the page, which does not.
        f = (DETAILS[self.detail.get()][1] / 32.0) ** 2
        secs = n * ((3 + 6 * f if self.structures.get() else 2 + 3.5 * f)
                    + 0.85 * f * f)
        how_long = ("%d seconds" % max(5, 5 * round(secs / 5.0)) if secs < 75
                    else "%g minutes" % (round(secs / 30.0) / 2.0))
        self._set_status("%s about %s" % (
            "%d worlds take" % n if n > 1 else "one world takes", how_long))

    def _exclusive(self):
        """A flat image and a page you fly around are two different answers."""
        if self.three_d.get() and self.png.get():
            self.png.set(False)
        self._retime()

    def _browse(self):
        d = self.filedialog.askdirectory(initialdir=self.folder.get(),
                                         title="Where should the map go?")
        if d:
            self.folder.set(os.path.normpath(d))

    # -- running ----------------------------------------------------------

    def _start(self):
        if self.worker and self.worker.is_alive():
            self.stop.set()
            self._set_status("stopping...")
            return
        saves = self._chosen()
        if not saves:
            return
        folder = self.folder.get().strip()
        if not os.path.isdir(folder):
            self.messagebox.showerror(
                "Scrap Mechanic map",
                "There is no folder called\n\n%s" % folder)
            return
        self.written = []
        self.stop.clear()
        self.progress["value"] = 0
        self.go.configure(text="Stop")
        self.reveal.state(["disabled"])
        opts = dict(px=DETAILS[self.detail.get()][1],
                    structures=self.structures.get(),
                    shade=self.shade.get(), png=self.png.get(),
                    three_d=self.three_d.get(), objects=self.objects.get(),
                    underground=self.underground.get(),
                    creations=self.creations.get(), folder=folder)
        self.worker = threading.Thread(target=self._work, args=(saves, opts),
                                       daemon=True)
        self.worker.start()

    def _work(self, saves, opts):
        try:
            from PIL import Image
            from . import assets, savefile, tiles
            from .render import MapRenderer

            self._post("say", "finding the game")
            game = discover.find_game()
            if not game:
                raise RuntimeError(
                    "Could not find your Scrap Mechanic installation.\n"
                    "Is the game installed through Steam on this PC?")
            index = self._cache.get("index")
            if index is None:
                self._post("say", "reading the terrain tiles")
                index = tiles.TileIndex(game)
                if not index:
                    raise RuntimeError(
                        "Found the game at\n%s\nbut no terrain tiles in it." % game)
                self._cache["index"] = index
            db = self._cache.get("db")
            if db is None:
                self._post("say", "reading the asset catalogue")
                db = assets.AssetDb(game)
                self._cache["db"] = db
            cat = self._cache.get("cat")
            if cat is None and opts["creations"]:
                from . import creations
                self._post("say", "reading the blocks and parts")
                cat = self._cache["cat"] = creations.Catalogue(game)

            done = []
            for n, save in enumerate(saves):
                self._check()
                base = float(n) / len(saves)
                span = 1.0 / len(saves)
                self._post("say", "reading %s" % save.name)
                with savefile.SaveFile(save.path) as sf:
                    cd = sf.cell_data()
                    if cd is None:
                        self._post("skip", (save.name, "no overworld terrain"))
                        continue
                    info = sf.game_info()
                    # Asked before the surface is drawn: its page carries the
                    # lift panel that links to the floors under it.
                    found = []
                    if opts["underground"]:
                        from . import underground as ug
                        self._post("say", "looking under %s" % save.name)
                        found = ug.floors(sf, index)
                    saved = self._saved(sf, sf.overworld_id(), info, cat, opts)
                    r = MapRenderer(cd, index, px=opts["px"], asset_db=db,
                                    structures=opts["structures"])

                    def step(a, b, base=base, span=span, name=save.name):
                        self._check()
                        self._post("progress",
                                   (base + span * (0.9 * a / max(b, 1)),
                                    "drawing %s  %d%%" % (name, 100 * a // max(b, 1))))

                    arr = r.render(hillshade=opts["shade"], progress=step,
                                   fields=opts["three_d"])
                    if saved is not None:
                        from . import creations
                        creations.paint(arr, r, saved.builds)
                    self._post("progress", (base + span * 0.92,
                                            "writing the file"))
                    path = output.default_path(opts["folder"], save.name,
                                               opts["png"], opts["three_d"])
                    # A plain image has no bar to put a lift panel in.
                    lift = (output.lift_for(save, found,
                                            three_d=opts["three_d"])
                            if found and not opts["png"] else None)
                    if opts["three_d"]:
                        def stand(a, b, base=base, span=span, name=save.name):
                            self._check()
                            self._post("progress",
                                       (base + span * (0.92 + 0.06 * a / max(b, 1)),
                                        "standing up %s  %d%%"
                                        % (name, 100 * a // max(b, 1))))

                        output.write_map3d(path, r, cd, info, save, db=db,
                                           objects=opts["objects"],
                                           progress=stand, floors=lift,
                                           saved=saved)
                    else:
                        img = Image.fromarray(arr)
                        output.write_map(path, img, r, cd, info, save,
                                         png=opts["png"], floors=lift,
                                         saved=saved)
                    done.append(path)
                    self._post("wrote", (save.name, path, len(r.missing)))
                    for floor in found:
                        self._check()
                        self._post("say", "drawing floor %s of %s"
                                   % (floor.label, save.name))
                        done.append(self._floor(floor, found, save, sf, index,
                                                db, cat, info, opts))
            self._post("finished", done)
        except Cancelled:
            self._post("cancelled", None)
        except Exception:
            self._post("failed", traceback.format_exc())

    def _saved(self, sf, world_id, info, cat, opts):
        """What one world of the save holds, or None if it was not asked for."""
        if not opts["creations"] or world_id is None:
            return None
        from . import creations
        try:
            return creations.gather(sf, world_id, cat,
                                    info.get("gametick") or 0)
        except Exception:
            # A world whose bodies will not read is still a world worth
            # drawing; the terrain is the map and this is what is on it.
            return None

    def _floor(self, floor, found, save, sf, index, db, cat, info, opts):
        """One underground floor, written beside the surface map."""
        from PIL import Image
        from . import underground as ug

        r = ug.UndergroundRenderer(floor, index, px=max(4, opts["px"]),
                                   asset_db=db,
                                   structures=opts["structures"])
        saved = self._saved(sf, floor.world.id, info, cat, opts)
        arr = r.render(hillshade=opts["shade"], progress=lambda a, b: self._check(),
                       fields=opts["three_d"])
        if saved is not None:
            from . import creations
            creations.paint(arr, r, saved.builds)
        path = output.floor_path(opts["folder"], save.name, floor.label,
                                 png=opts["png"], three_d=opts["three_d"])
        lift = (None if opts["png"] else
                output.lift_for(save, found, here=floor,
                                three_d=opts["three_d"]))
        if opts["three_d"]:
            output.write_floor3d(path, r, floor, info, save, db=db,
                                 objects=opts["objects"], floors=lift,
                                 saved=saved)
        else:
            output.write_floor(path, Image.fromarray(arr), r, floor, info, save,
                               png=opts["png"], floors=lift, saved=saved)
        return path

    def _check(self):
        if self.stop.is_set():
            raise Cancelled()

    def _post(self, kind, payload):
        self.q.put((kind, payload))

    # -- the ui side of the conversation ----------------------------------

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _handle(self, kind, payload):
        if kind == "say":
            self._set_status(payload)
        elif kind == "progress":
            frac, text = payload
            self.progress["value"] = int(1000 * frac)
            self._set_status(text)
        elif kind == "skip":
            name, why = payload
            self._set_status("%s: %s" % (name, why))
        elif kind == "wrote":
            name, path, missing = payload
            self.written.append(path)
            if missing:
                self._set_status("%s: %d tile kind(s) are not on this PC and "
                                 "came out purple. They belong to a mod — "
                                 "subscribe to it and run the game once."
                                 % (name, missing))
        elif kind == "finished":
            self._end()
            self.progress["value"] = 1000
            if not self.written:
                self._set_status("Nothing to draw. Creative worlds with no "
                                 "terrain have no map.")
                return
            self._set_status("done: %s" % os.path.basename(self.written[0])
                             + ("" if len(self.written) == 1
                                else " and %d more" % (len(self.written) - 1)))
            self.reveal.state(["!disabled"])
            self._open(self.written[0])
        elif kind == "cancelled":
            self._end()
            self.progress["value"] = 0
            self._set_status("stopped")
        elif kind == "failed":
            self._end()
            self.progress["value"] = 0
            self._set_status("that did not work")
            lines = [l for l in payload.strip().splitlines() if l.strip()]
            self.messagebox.showerror("Scrap Mechanic map", lines[-1]
                                      if lines else "Unknown error")

    def _end(self):
        self.go.configure(text="Make map")

    def _open(self, path):
        if not path.lower().endswith(".html") and hasattr(os, "startfile"):
            try:
                os.startfile(path)        # the machine's own image viewer
                return
            except OSError:
                pass
        webbrowser.open("file:///" + path.replace("\\", "/"))

    def _reveal_last(self):
        if self.written:
            _reveal(self.written[0])

    def _set_status(self, text):
        self.status.configure(text=text)

    def _close(self):
        self.stop.set()
        self.root.destroy()


def run():
    """Open the window. Returns False if this Python has no tkinter."""
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        return False

    if sys.platform == "win32":
        # Without this the whole window is drawn blurry on a scaled display.
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root, tk, ttk, filedialog, messagebox)
    root.mainloop()
    return True
