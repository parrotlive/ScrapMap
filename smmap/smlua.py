"""Decoder for Scrap Mechanic's 'LUA' bit-packed value serializer.

The game persists Lua tables (notably the overworld cell grid written by
sm.terrainData.save) as a bit-packed value stream:

    "LUA" magic (3 bytes), u32 version, then one value.

A value is an 8-bit tag followed by a payload. Tables carry a count, a single
"is array" bit, and -- for arrays only -- a signed start index, which is how the
cell grids keep their negative world coordinates. Strings realign to a byte
boundary before their raw bytes; everything else stays packed, so values
routinely straddle byte boundaries.

Tag values were recovered by decoding a real survival save: the int32 tag was
confirmed against the seed stored separately in the Game table, and the UUID tag
against the 1070 .tile files shipped with the game.
"""

from .bitreader import BitReader

T_NIL = 0x00
T_BOOL = 0x02
T_FLOAT = 0x03
T_STRING = 0x04
T_TABLE = 0x05
T_INT32 = 0x06
T_INT16 = 0x07
T_INT8 = 0x08
T_DOUBLE = 0x0B
T_USERDATA = 0x64

UD_UUID = 10001

MAGIC = b"LUA"


class Uuid(bytes):
    """A 16-byte tile UUID. Stored byte-reversed on disk; kept canonical here."""

    def __str__(self):
        h = self.hex()
        return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])

    __repr__ = __str__

    def is_nil(self):
        return self == b"\0" * 16


class UnknownTag(Exception):
    def __init__(self, tag, bitpos):
        super().__init__("unknown LUA tag 0x%02x at bit %d" % (tag, bitpos))
        self.tag = tag
        self.bitpos = bitpos


def _value(r):
    at = r.pos
    tag = r.u8()

    if tag == T_TABLE:
        count = r.u32()
        is_array = r.bit()
        if is_array:
            start = r.i32()
            return {start + i: _value(r) for i in range(count)}
        out = {}
        for _ in range(count):
            k = _value(r)
            out[k] = _value(r)
        return out

    if tag == T_INT8:
        return r.i8()
    if tag == T_INT16:
        return r.i16()
    if tag == T_INT32:
        return r.i32()
    if tag == T_USERDATA:
        kind = r.u32()
        if kind == UD_UUID:
            return Uuid(r.bytes(16)[::-1])
        raise UnknownTag(0x64000000 | kind, at)
    if tag == T_STRING:
        n = r.u32()
        r.align()
        return r.bytes(n).decode("utf-8", "replace")
    if tag == T_BOOL:
        return bool(r.bit())
    if tag == T_NIL:
        return None
    if tag == T_FLOAT:
        return r.f32()
    if tag == T_DOUBLE:
        return r.f64()

    raise UnknownTag(tag, at)


def loads(data):
    """Decode a 'LUA' blob into plain Python values."""
    if data[:3] != MAGIC:
        raise ValueError("not a LUA blob (magic %r)" % (data[:3],))
    r = BitReader(data, 7 * 8)        # 3-byte magic + u32 version
    return _value(r)
