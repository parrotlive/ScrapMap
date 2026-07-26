"""MSB-first bit reader used by the Scrap Mechanic 'LUA' value serializer.

Reads are O(1) rather than bit-by-bit: a value never spans more than a handful
of bytes, so we slice those out and shift once. The cell grids contain well over
a hundred thousand entries, and the naive loop is far too slow for a tool that
should feel instant.
"""

import struct


class BitReader:
    __slots__ = ("data", "pos")

    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos  # absolute bit position

    def bits(self, n):
        p = self.pos
        start = p >> 3
        end = (p + n + 7) >> 3
        chunk = int.from_bytes(self.data[start:end], "big")
        shift = (end - start) * 8 - (p - (start << 3)) - n
        self.pos = p + n
        return (chunk >> shift) & ((1 << n) - 1)

    def bit(self):
        return self.bits(1)

    def u8(self):
        return self.bits(8)

    def u32(self):
        return self.bits(32)

    def i8(self):
        v = self.bits(8)
        return v - 0x100 if v & 0x80 else v

    def i16(self):
        v = self.bits(16)
        return v - 0x10000 if v & 0x8000 else v

    def i32(self):
        v = self.bits(32)
        return v - 0x100000000 if v & 0x80000000 else v

    def f32(self):
        return struct.unpack(">f", self.bits(32).to_bytes(4, "big"))[0]

    def f64(self):
        return struct.unpack(">d", self.bits(64).to_bytes(8, "big"))[0]

    def align(self):
        """Strings are byte aligned even though tags and lengths are not."""
        if self.pos & 7:
            self.pos = (self.pos + 7) & ~7

    def bytes(self, n):
        if self.pos & 7 == 0:
            o = self.pos >> 3
            self.pos += n << 3
            return self.data[o:o + n]
        return bytes(self.bits(8) for _ in range(n))
