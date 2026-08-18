"""Read what we need out of a Scrap Mechanic survival save (.db).

A save is a SQLite database whose BLOB columns share one envelope:

    uid[16] | keyLen[u16 BE] | key | worldId[u16 BE] | flags[u8]
           | compressedSize[u32 BE] | LZ4 block

The overworld cell grid is a 'LUA' value blob stored in ScriptData -- it is what
sm.terrainData.save() writes, and it holds the tile UUID, rotation, tile offset,
elevation and flags of every cell in the world.

A save holds more than one world. Every world the player has been to -- the
overworld, each warehouse, each floor of the underground -- gets its own id, its
own rows in every table, and its own cell grid, because they are all written by
the same sm.terrainData.save(). Which world is which is not guessed: GenericData
carries a descriptor per world naming the script that generates it and the JSON
it was created with, so the overworld says so and an underground floor says
which floor it is.
"""

import json
import os
import sqlite3
import struct

from . import lz4
from . import smlua

CELL_SIZE = 64.0          # metres per cell
CHUNK_SIZE = 16.0         # metres per chunk; four of them to a cell
CHUNKS_PER_CELL = int(CELL_SIZE // CHUNK_SIZE)

# Script data that belongs to the game rather than to any one world is filed
# under a world id of its own, which is what sm.storage's global channels are.
GLOBAL_WORLD = 65534


def _parse_envelope(blob):
    if len(blob) < 25:
        return None
    klen = struct.unpack_from(">H", blob, 16)[0]
    o = 18 + klen
    if o + 7 > len(blob):
        return None
    world_id, flags = struct.unpack_from(">HB", blob, o)
    csize = struct.unpack_from(">I", blob, o + 3)[0]
    o += 7
    if csize > len(blob) - o:
        return None
    return {
        "uid": blob[0:16],
        "key": blob[18:18 + klen],
        "worldId": world_id,
        "flags": flags,
        "payload": blob[o:o + csize],
    }


def unpack_blob(blob):
    """Envelope + LZ4 -> raw bytes, or None if this blob is not one of ours."""
    env = _parse_envelope(blob)
    if env is None:
        return None
    try:
        return lz4.decompress(env["payload"])
    except Exception:
        return None


def _strings(raw, n=3):
    """The length-prefixed strings out of a world descriptor.

    The descriptor is one 32-bit word, then n strings each written as a
    big-endian 16-bit length and that many bytes. The last of them is JSON with
    a newline on the end, which json.loads does not mind.
    """
    out = []
    o = 4
    for _ in range(n):
        if o + 2 > len(raw):
            return None
        ln = struct.unpack_from(">H", raw, o)[0]
        o += 2
        if o + ln > len(raw):
            return None
        out.append(raw[o:o + ln].decode("utf-8", "replace"))
        o += ln
    return out


class World(object):
    """One world inside a save: what generates it and what it was created with."""

    __slots__ = ("id", "script", "cls", "data")

    def __init__(self, world_id, script, cls, data):
        self.id = world_id
        self.script = script
        self.cls = cls
        self.data = data

    @property
    def script_name(self):
        return os.path.splitext(os.path.basename(
            self.script.replace("/", os.sep)))[0]

    def __repr__(self):
        return "<World %d %s %r>" % (self.id, self.cls, self.data)


class SaveFile(object):
    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        # Open read-only and immutable so an open game session cannot block us
        # and we can never write to the player's save.
        uri = "file:%s?mode=ro&immutable=1" % path.replace("?", "%3f").replace("#", "%23")
        self.con = sqlite3.connect(uri, uri=True)
        self._tables = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    def close(self):
        try:
            self.con.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -- metadata ---------------------------------------------------------

    def game_info(self):
        if "Game" not in self._tables:
            return {}
        cols = [r[1] for r in self.con.execute("PRAGMA table_info(Game)")]
        row = self.con.execute("SELECT * FROM Game").fetchone()
        return dict(zip(cols, row)) if row else {}

    # -- the worlds in the save -------------------------------------------

    _worlds = None

    def worlds(self):
        """world id -> World, for every world this save has ever created.

        Read from the descriptor GenericData keeps beside each world rather than
        from any convention about which id means what: a save numbers its worlds
        in the order the player happened to open them.
        """
        if self._worlds is not None:
            return self._worlds
        out = {}
        if "GenericData" in self._tables:
            for wid, blob in self.con.execute(
                    "SELECT worldId, data FROM GenericData "
                    "WHERE data IS NOT NULL"):
                raw = unpack_blob(blob)
                if not raw or len(raw) < 8:
                    continue
                parts = _strings(raw)
                if not parts or not parts[0].lower().endswith(".lua"):
                    continue
                try:
                    data = json.loads(parts[2])
                except ValueError:
                    data = None
                out[wid] = World(wid, parts[0], parts[1],
                                 data if isinstance(data, dict) else {})
        self._worlds = out
        return out

    def overworld_id(self):
        """The id of the world the save itself calls the overworld, or None."""
        for wid, w in sorted(self.worlds().items()):
            if w.script_name.lower() == "overworld":
                return wid
        return None

    # -- cell grids --------------------------------------------------------

    def _scan_grids(self, world_id=None):
        """The first blob that decodes to a cell grid, biggest first.

        A grid is only recognised by decoding it and finding the keys
        sm.terrainData.save writes, so the search stops at the first hit rather
        than decoding every blob in a save that may hold tens of thousands.
        """
        where = "" if world_id is None else " WHERE worldId = %d" % world_id
        for table in ("ScriptData", "GenericData"):
            if table not in self._tables:
                continue
            rows = self.con.execute(
                "SELECT data FROM %s%s ORDER BY length(data) DESC"
                % (table, where))
            for (blob,) in rows:
                if blob is None or len(blob) < 512:
                    continue
                raw = unpack_blob(blob)
                if not raw or raw[:3] != smlua.MAGIC:
                    continue
                try:
                    val = smlua.loads(raw)
                except Exception:
                    continue
                if isinstance(val, dict) and "uid" in val and "bounds" in val:
                    return val
        return None

    _cell_cache = False

    def cell_data(self):
        """The decoded g_cellData table, or None if this save has no overworld."""
        if self._cell_cache is not False:
            return self._cell_cache
        wid = self.overworld_id()
        val = None if wid is None else self._scan_grids(wid)
        # A save from before the descriptors existed, or one whose overworld was
        # never written: fall back to the biggest grid in the file, which is what
        # the overworld's has always been.
        self._cell_cache = val if val is not None else self._scan_grids()
        return self._cell_cache

    _grid_cache = None

    def terrain_data(self, world_id):
        """The decoded g_cellData for one world, or None."""
        if self._grid_cache is None:
            self._grid_cache = {}
        if world_id not in self._grid_cache:
            self._grid_cache[world_id] = self._scan_grids(world_id)
        return self._grid_cache[world_id]

    # -- what is standing in the world -------------------------------------

    def rigid_bodies(self, world_id):
        """(id, blob) for every rigid body in one world."""
        if "RigidBody" not in self._tables:
            return []
        return self.con.execute(
            "SELECT id, data FROM RigidBody WHERE worldId = ? "
            "AND data IS NOT NULL", (world_id,)).fetchall()

    def child_shapes(self, world_id):
        """(bodyId, blob) for every block and part of that world's bodies.

        Joined against the bodies rather than filtered on its own: a child shape
        row says which body it belongs to and nothing about which world that is.
        """
        if "ChildShape" not in self._tables:
            return []
        return self.con.execute(
            "SELECT c.bodyId, c.data FROM ChildShape c "
            "JOIN RigidBody b ON b.id = c.bodyId "
            "WHERE b.worldId = ? AND c.data IS NOT NULL", (world_id,)).fetchall()

    def joint_bodies(self):
        """(bodyA, bodyB) for every joint, as the bodies it holds together."""
        if "Joint" not in self._tables or "ChildShape" not in self._tables:
            return []
        return self.con.execute(
            "SELECT a.bodyId, b.bodyId FROM Joint j "
            "JOIN ChildShape a ON a.id = j.childShapeIdA "
            "JOIN ChildShape b ON b.id = j.childShapeIdB").fetchall()

    # -- script storage -----------------------------------------------------

    def storage(self, world_id, channels=None):
        """channel -> the values saved on it, for one world of the save.

        This is what sm.storage writes: the game's own managers keep their state
        on numbered channels, and survival_constants.lua is where the numbers
        are named. A channel key is itself a serialised value rather than a
        string, so which channel a row belongs to is read by decoding its key.
        """
        out = {}
        if "ScriptData" not in self._tables:
            return out
        for key, blob in self.con.execute(
                "SELECT key, data FROM ScriptData "
                "WHERE worldId = ? AND data IS NOT NULL", (world_id,)):
            key = bytes(key)
            if key[:3] != smlua.MAGIC:
                continue
            try:
                channel = smlua.loads(key)
            except Exception:
                continue
            if not isinstance(channel, int) or (
                    channels is not None and channel not in channels):
                continue
            raw = unpack_blob(blob)
            if not raw:
                continue
            try:
                out.setdefault(channel, []).append(smlua.loads(raw))
            except Exception:
                continue
        return out
