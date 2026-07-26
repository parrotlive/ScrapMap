"""Read what we need out of a Scrap Mechanic survival save (.db).

A save is a SQLite database whose BLOB columns share one envelope:

    uid[16] | keyLen[u16 BE] | key | worldId[u16 BE] | flags[u8]
           | compressedSize[u32 BE] | LZ4 block

The overworld cell grid is a 'LUA' value blob stored in ScriptData -- it is what
sm.terrainData.save() writes, and it holds the tile UUID, rotation, tile offset,
elevation and flags of every cell in the world.
"""

import os
import sqlite3
import struct

from . import lz4
from . import smlua

CELL_SIZE = 64.0          # metres per cell


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

    # -- overworld cell grid ---------------------------------------------

    _cell_cache = False

    def cell_data(self):
        """The decoded g_cellData table, or None if this save has no overworld."""
        if self._cell_cache is not False:
            return self._cell_cache
        self._cell_cache = None
        for table in ("ScriptData", "GenericData"):
            if table not in self._tables:
                continue
            rows = self.con.execute(
                "SELECT data FROM %s ORDER BY length(data) DESC" % table)
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
                    self._cell_cache = val
                    return val
        return None
