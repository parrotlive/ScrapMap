"""Pack the tool into one ScrapMap.exe that needs nothing installed.

    python build_exe.py

PyInstaller bundles the interpreter, numpy, Pillow and tkinter alongside the
package, so the result runs on a PC with no Python at all. It is built windowed:
double-clicked it opens the window, and run from a terminal with arguments it
borrows that terminal (see smmap/app.py) and behaves like the command line.

The build happens inside a throwaway virtual environment under build/venv. That
is not fussiness: an executable is only as small and as predictable as the
interpreter it is cut from, and whatever else happens to be installed on the
machine would otherwise be weighed and sometimes swept in. It also leaves the
Python you actually use alone.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "build")
VENV = os.path.join(WORK, "venv")
NAME = "ScrapMap"
NEEDS = ["pyinstaller", "numpy", "pillow"]

# Nothing here uses any of this. The clean environment keeps most of it out on
# its own; these are the ones that still get pulled in by something else's
# optional import, or that come with the standard library.
UNWANTED = ["scipy", "matplotlib", "pandas", "IPython", "pytest", "setuptools",
            "pip", "sqlalchemy", "numpy.f2py", "PIL.ImageQt", "PyQt5", "PySide2",
            "PyQt6", "PySide6", "wx", "test", "unittest", "pydoc_data",
            "lib2to3", "distutils"]


def icon(path, size=512):
    """A map in a rounded square: deep water, one green island, a sand shore."""
    from PIL import Image, ImageDraw, ImageFilter

    from smmap import palette

    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22),
                        fill=palette.WATER_DEEP_RGB + (255,))
    # A shallower ring just inside the water, so the coast reads as a coast.
    coast = [(0.16, 0.34), (0.28, 0.17), (0.50, 0.13), (0.72, 0.20),
             (0.86, 0.38), (0.80, 0.60), (0.86, 0.78), (0.66, 0.89),
             (0.44, 0.85), (0.24, 0.88), (0.13, 0.66), (0.20, 0.50)]
    poly = [(x * s, y * s) for x, y in coast]
    shore = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shore).polygon(poly, fill=palette.WATER_SHALLOW_RGB + (255,))
    img.alpha_composite(shore.filter(ImageFilter.GaussianBlur(s * 0.035)))
    d.polygon(poly, fill=tuple(palette.MATERIAL_RGB[1]) + (255,))          # sand
    inner = [(x * s + (s * 0.5 - x * s) * 0.13,
              y * s + (s * 0.5 - y * s) * 0.13) for x, y in coast]
    d.polygon(inner, fill=palette.BASE_RGB + (255,))                       # grass
    # An inland lake, which is the thing this tool now gets right.
    d.ellipse((s * 0.52, s * 0.30, s * 0.72, s * 0.46),
              fill=palette.WATER_SHALLOW_RGB + (255,))
    # And a road across it, because that is what a map of this world looks like.
    d.line([(s * 0.20, s * 0.66), (s * 0.42, s * 0.58), (s * 0.55, s * 0.66),
            (s * 0.78, s * 0.60)], fill=(150, 150, 154, 255), width=int(s * 0.035),
           joint="curve")

    # Round the corners properly: the mask, not the fill, decides the shape.
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1),
                                           radius=int(s * 0.22), fill=255)
    img.putalpha(mask)
    img.save(path, sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    return path


def venv_python():
    """Path to the build environment's interpreter, making it if it is missing."""
    py = os.path.join(VENV, "Scripts" if os.name == "nt" else "bin",
                      "python.exe" if os.name == "nt" else "python")
    if not os.path.isfile(py):
        print("making a build environment in %s ..." % VENV)
        subprocess.check_call([sys.executable, "-m", "venv", VENV])
    have = subprocess.run([py, "-c", "import PyInstaller, numpy, PIL"],
                          capture_output=True).returncode == 0
    if not have:
        print("installing %s ..." % ", ".join(NEEDS))
        subprocess.check_call([py, "-m", "pip", "install", "--quiet",
                               "--disable-pip-version-check"] + NEEDS)
    return py


def build():
    sys.path.insert(0, HERE)
    import PyInstaller.__main__ as pyi

    os.makedirs(WORK, exist_ok=True)
    ico = icon(os.path.join(WORK, "icon.ico"))
    entry = os.path.join(WORK, "scrapmap_entry.py")
    with open(entry, "w", encoding="utf-8") as f:
        f.write("import multiprocessing, sys\n"
                "from smmap.app import main\n"
                "if __name__ == '__main__':\n"
                "    multiprocessing.freeze_support()\n"
                "    sys.exit(main())\n")

    args = ["--onefile", "--windowed", "--clean", "--noconfirm",
            "--name", NAME, "--icon", ico,
            "--distpath", HERE,
            "--workpath", os.path.join(WORK, "work"),
            "--specpath", WORK,
            # Every module in the package: gui.py imports several of them
            # inside functions, on the worker thread.
            "--collect-submodules", "smmap",
            "--paths", HERE]
    for mod in UNWANTED:
        args += ["--exclude-module", mod]
    args.append(entry)

    print("building %s.exe ..." % NAME)
    pyi.run(args)

    out = os.path.join(HERE, NAME + ".exe")
    if not os.path.isfile(out):
        print("PyInstaller did not produce %s" % out)
        return 1
    print("\n  %s  (%.1f MB)" % (out, os.path.getsize(out) / 1e6))
    print("  Double-click it for the window, or run it with --help.")
    return 0


def main():
    if "--in-venv" in sys.argv:
        return build()
    try:
        py = venv_python()
    except (OSError, subprocess.CalledProcessError) as e:
        print("Could not prepare the build environment: %s" % e)
        return 2
    return subprocess.call([py, os.path.abspath(__file__), "--in-venv"])


if __name__ == "__main__":
    sys.exit(main())
