"""Pure-python LZ4 block decompressor.

Scrap Mechanic compresses both save-database blobs and .tile payloads with raw
LZ4 block format (no frame header). Keeping our own decoder means the tool has
no third-party dependency for reading game data.
"""


def decompress(src, expected=None):
    dst = bytearray()
    i = 0
    n = len(src)
    while i < n:
        token = src[i]
        i += 1

        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        if lit:
            dst += src[i:i + lit]
            i += lit

        # A block ends on a literal run, so running out here is normal.
        if i >= n:
            break

        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            raise ValueError("LZ4: zero match offset")

        length = token & 0xF
        if length == 15:
            while True:
                b = src[i]
                i += 1
                length += b
                if b != 255:
                    break
        length += 4

        start = len(dst) - offset
        if start < 0:
            raise ValueError("LZ4: match offset before start of output")
        if offset >= length:
            # Non-overlapping: one slice copy. This is the common case and the
            # difference between decoding hundreds of tiles in a second vs a minute.
            dst += dst[start:start + length]
        else:
            # Overlapping: the match reads bytes it is still writing, which is
            # simply the last `offset` bytes repeated. Terrain is full of these
            # -- a run of flat ground encodes as one byte at offset 1 repeated a
            # few thousand times -- so doing it a byte at a time costs more than
            # everything else in the tool put together.
            unit = bytes(dst[start:])
            dst += (unit * (length // offset + 1))[:length]

    if expected is not None and len(dst) != expected:
        raise ValueError("LZ4: expected %d bytes, produced %d" % (expected, len(dst)))
    return bytes(dst)
