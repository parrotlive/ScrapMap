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
    """Every Steam installation on this PC, best first."""
    roots = _windows_steam_roots() if os.name == "nt" else _unix_steam_roots()
    out = []
    for r in roots:
        r = os.path.normpath(r)
        if r not in out and os.path.isdir(r):
            out.append(r)
    return out


def _windows_steam_roots():
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
    return roots


def _unix_steam_roots():
    """Where Steam puts itself on Linux, and on macOS.

    There is no registry to ask, but there is not much spread either: the real
    directory, the two symlinks Steam keeps pointing at it, the path the Debian
    package uses, and the Flatpak sandbox's own copy.
    """
    home = os.path.expanduser("~")
    rel = [".local/share/Steam",
           ".steam/steam",
           ".steam/root",
           ".steam/debian-installation",
           ".var/app/com.valvesoftware.Steam/.local/share/Steam",
           "Library/Application Support/Steam"]        # macOS
    roots = [os.path.join(home, p.replace("/", os.sep)) for p in rel]
    env = os.environ.get("STEAM_BASE_FOLDER") or os.environ.get("STEAM_ROOT")
    if env:
        roots.insert(0, env)
    # Two of those are usually symlinks to a third, so collapse them or every
    # library gets walked several times over.
    seen, out = set(), []
    for r in roots:
        try:
            key = os.path.realpath(r)
        except OSError:
            key = r
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _library_folders(steam_root):
    """Every Steam library path, read from libraryfolders.vdf."""
    libs = [steam_root]
    vdf = resolve(os.path.join(steam_root, "steamapps", "libraryfolders.vdf"))
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
            p = resolve(os.path.join(lib, "steamapps", "common", GAME_DIR_NAME))
            if os.path.isfile(resolve(os.path.join(
                    p, "Survival", "Scripts", "terrain", "overworld",
                    "tile_database.lua"))):
                return p
    # Last resort: a plain scan of fixed drives is too slow, so try common spots.
    if os.name == "nt":
        for drive in "CDEFGH":
            p = "%s:\\SteamLibrary\\steamapps\\common\\%s" % (drive, GAME_DIR_NAME)
            if os.path.isdir(p):
                return p
    return None


def find_mod_dirs():
    """Every folder that might carry modded terrain, workshop and local alike.

    A modded world places tiles whose uuids are nowhere in the game's own
    folder, and a tile the index has never seen is drawn as a purple hole --
    which is a whole map of purple holes if the world is built on a terrain
    mod. The tiles ship with the mod, so the mod folders have to be read too.
    """
    out = []
    for root in _steam_roots():
        for lib in _library_folders(root):
            content = os.path.join(lib, "steamapps", "workshop", "content", APPID)
            if os.path.isdir(content):
                out.extend(sorted(glob.glob(os.path.join(content, "*"))))
    for u in user_dirs():
        mods = os.path.join(u, "Mods")
        if os.path.isdir(mods):
            out.extend(sorted(glob.glob(os.path.join(mods, "*"))))
    seen, keep = set(), []
    for p in out:
        p = os.path.normpath(p)
        if p not in seen and os.path.isdir(p):
            seen.add(p)
            keep.append(p)
    return keep


def user_dirs():
    """Every 'User_<steamid>' folder under the game's AppData directory."""
    if os.name != "nt":
        return _proton_user_dirs()
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    base = os.path.join(appdata, "Axolot Games", "Scrap Mechanic", "User")
    if not os.path.isdir(base):
        return []
    return sorted(glob.glob(os.path.join(base, "User_*")))


# Where AppData sits inside a Wine prefix. Proton keeps the modern path and the
# XP-era alias for it side by side, and which of the two is the real directory
# rather than a link to it depends on the Proton version.
_PREFIX_APPDATA = (os.path.join("AppData", "Roaming"), "Application Data")


def _proton_user_dirs():
    """The save folders inside the game's Proton prefix.

    Scrap Mechanic is a Windows game, so on Linux Steam runs it under Proton,
    which hands it a Wine prefix with a whole Windows drive inside. The saves
    are exactly where they always were -- under AppData, in the same folders,
    in the same format -- only that AppData is inside the prefix rather than in
    the user's home. Nothing about reading them changes.
    """
    seen, out = set(), []
    for root in _steam_roots():
        for lib in _library_folders(root):
            users = os.path.join(lib, "steamapps", "compatdata", APPID, "pfx",
                                 "drive_c", "users")
            if not os.path.isdir(users):
                continue
            # Modern prefixes call the account 'steamuser'; older ones used the
            # login name, so take whichever accounts are actually there.
            for who in sorted(glob.glob(os.path.join(users, "*"))):
                for appdata in _PREFIX_APPDATA:
                    base = os.path.join(who, appdata, "Axolot Games",
                                        "Scrap Mechanic", "User")
                    if not os.path.isdir(base):
                        continue
                    for u in sorted(glob.glob(os.path.join(base, "User_*"))):
                        # The two AppData spellings usually lead to the same
                        # place, so the same save must not be listed twice.
                        try:
                            key = os.path.realpath(u)
                        except OSError:
                            key = u
                        if key not in seen:
                            seen.add(key)
                            out.append(u)
    return out


_CASE_DIRS = {}


def resolve(path):
    """A path as it actually appears on disk, for a filesystem that cares.

    The game's data files refer to each other in whatever case its exporter
    wrote -- `$GAME_DATA/Terrain/...` -- which need not be the case the depot
    put on disk. Windows does not care, and under Proton the game does not
    either, because Wine matches names case-insensitively on the game's behalf.
    A tool reading those files straight off a Linux filesystem gets no such
    help: one wrong letter and a mesh, a texture or a whole asset set is simply
    not there, and the map comes out missing things for no visible reason.

    Returns the path untouched on Windows, and untouched wherever it already
    resolves, so the case that matters to almost everyone costs nothing.
    """
    if os.name == "nt" or os.path.exists(path):
        return path

    parts = []
    head, tail = os.path.split(path)
    while tail:
        parts.append(tail)
        head, tail = os.path.split(head)
    if not parts:
        return path
    parts.reverse()

    cur = head
    for part in parts:
        step = os.path.join(cur, part)
        if os.path.exists(step):
            cur = step
            continue
        names = _CASE_DIRS.get(cur)
        if names is None:
            try:
                # A relative path starts at the working directory, which has to
                # be named to be listed but must not be glued onto the answer.
                names = {n.lower(): n for n in os.listdir(cur or os.curdir)}
            except OSError:
                return path
            _CASE_DIRS[cur] = names
        hit = names.get(part.lower())
        if hit is None:
            # Genuinely missing rather than merely miscased. Hand back what was
            # asked for so the caller reports the name it was looking for.
            return path
        cur = os.path.join(cur, hit)
    return cur


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
