"""Locate the Scrap Mechanic install and the player's save files automatically.

The whole point of this tool is that the user should not have to tell it where
anything lives, so everything here is best-effort with several fallbacks and
never prompts.
"""

import os
import re
import glob

APPID = "387990"
GAME_DIR_NAME = "Scrap Mechanic"


def _steam_roots():
    roots = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            roots.append(os.path.join(base, "Steam"))
    roots.append(r"C:\Program Files (x86)\Steam")
    # Registry is authoritative when Steam lives somewhere unusual.
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ("SteamPath", "InstallPath"):
                        try:
                            roots.insert(0, winreg.QueryValueEx(k, name)[0])
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass
    out = []
    for r in roots:
        r = os.path.normpath(r)
        if r not in out and os.path.isdir(r):
            out.append(r)
    return out


def _library_folders(steam_root):
    """Every Steam library path, read from libraryfolders.vdf."""
    libs = [steam_root]
    vdf = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return libs
    for m in re.finditer(r'"path"\s*"([^"]+)"', text):
        p = os.path.normpath(m.group(1).replace("\\\\", "\\"))
        if p not in libs:
            libs.append(p)
    return libs


def find_game():
    """Absolute path of the Scrap Mechanic install, or None."""
    override = os.environ.get("SM_GAME_DIR")
    if override and os.path.isdir(override):
        return override
    for root in _steam_roots():
        for lib in _library_folders(root):
            p = os.path.join(lib, "steamapps", "common", GAME_DIR_NAME)
            if os.path.isfile(os.path.join(p, "Survival", "Scripts", "terrain",
                                           "overworld", "tile_database.lua")):
                return p
    # Last resort: a plain scan of fixed drives is too slow, so try common spots.
    for drive in "CDEFGH":
        p = "%s:\\SteamLibrary\\steamapps\\common\\%s" % (drive, GAME_DIR_NAME)
        if os.path.isdir(p):
            return p
    return None


def user_dirs():
    """Every 'User_<steamid>' folder under the game's AppData directory."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    base = os.path.join(appdata, "Axolot Games", "Scrap Mechanic", "User")
    if not os.path.isdir(base):
        return []
    return sorted(glob.glob(os.path.join(base, "User_*")))


class Save(object):
    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        try:
            st = os.stat(path)
            self.mtime = st.st_mtime
            self.size = st.st_size
        except OSError:
            self.mtime = 0
            self.size = 0
        parent = os.path.basename(os.path.dirname(path))
        self.folder = parent

    def __repr__(self):
        return "<Save %s (%.1f MB)>" % (self.name, self.size / 1e6)


def find_saves():
    """All .db saves for all local users, newest first."""
    out = []
    for u in user_dirs():
        for p in glob.glob(os.path.join(u, "Save", "**", "*.db"), recursive=True):
            out.append(Save(p))
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out
