"""Everything standing in the world that the terrain generator did not put there.

The rest of this tool renders a world out of its recipe: the save says which
tile went in which cell, the game's own tiles say what is on them, and the map
follows. That is the world as it was handed to you. It is not the world you left
behind. Your car is not in any tile, and neither is the base you welded together
at the end of the road, or the wreck you dragged home, or the hole in the
warehouse where a structure used to be.

All of that is in the save, in four tables the rest of the tool never opens:

    RigidBody    one rigid thing: where it is, how it is turned, whether it can
                 move at all, and which world it is in
    ChildShape   one block or one part of one body: which asset, where in the
                 body, how big, what colour it was painted
    Joint        a bearing or a piston, joining two bodies into one machine
    RigidBodyBounds   an r-tree of the same bodies, which the game keeps so it
                 can ask what is nearby

None of those are compressed and none are script data: they are plain records,
written big-endian, one per row.

    RigidBody, 60 or 80 bytes
        0   tag 0, and 1 for a body welded to the world or 2 for one that can
            move; 80 bytes rather than 60 is the velocity a moving body carries
        3   body id, u32
        7   world id, u16
        9   the bounding box, 4 floats, written back to front
       27   the rotation, 4 floats -- back to front, w, z, y, x, if the body is
            welded to the world, and forward, x, y, z, w, if it can move
       43   where the body is, 3 floats, east, north and up in metres

    ChildShape, 42 to 48 bytes
        0   tag 1, and 31 for a block or 32 for a part
        3   shape id, u32, then the body it belongs to
       11   the asset uuid, 16 bytes
       31   where in the body it sits, 3 signed shorts, in blocks
       38   the colour it was painted, 3 bytes of RGB
       41   a block's size in blocks, 3 signed shorts; a part has one byte here
            instead, which is the way it is turned

A block is a quarter of a metre, so a body's shapes come back to metres by way
of that quarter and the body's own rotation. Which is worth saying plainly: the
positions below are not an approximation of where your creation is. They are
where it is, to the quarter metre, because that is how the game wrote it down.

Two versions of the save format are in the wild and they differ by one trailing
byte per shape, so shapes are read by the tag at the front rather than by how
long the row is.
"""

import glob
import os
import struct

import numpy as np

from . import assets
from . import discover

CELL = 64.0
BLOCK = 0.25              # metres to a block

_BODY, _SHAPE = 0, 1
_WELDED, _MOVING = 1, 2
_BLOCK, _PART = 31, 32

# A part with no shape of its own in the catalogue is drawn as one block, which
# is what most of them are and never far wrong for the rest.
_UNIT = (1, 1, 1)

# The eight corners of a unit box, one row each.
_CORNERS = np.array([(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)],
                    np.float32)

# Which way a part is turned. A block is a box that runs from where it sits to
# where it sits plus its size, and that is the whole of it. A part is not: it
# carries its size in the catalogue, in its own frame, and the byte at 41 says
# which way that frame is pointing. So a wall panel one block wide and twelve
# high is twelve blocks of wall standing up, or twelve blocks of wall lying
# flat, and only the byte says which.
#
# It packs the two axes the game keeps for a shape -- where the part's own x and
# z point in the body -- as 1, 2 or 3 for east, north and up, signed, and offset
# by four so that neither nibble needs a sign of its own:
#
#     (zaxis + 4) << 4 | (xaxis + 4)
#
# The third axis follows from the other two, so a whole rotation fits in a byte.
# Read off the game's own blueprints, which are the same shapes in the same
# places as the bodies spawned from them and which say in words what the byte
# says in bits.
_AXIS = {1: (1, 0, 0), 2: (0, 1, 0), 3: (0, 0, 1),
         -1: (-1, 0, 0), -2: (0, -1, 0), -3: (0, 0, -1)}


def _turns():
    """Every axis byte as a 3x3, so turning a part is a lookup, not a branch.

    Two of the axes are in the byte and the third is what is left. Byte zero is
    not a turn at all, which is what a block carries, so a block falls through
    the same lookup unturned.
    """
    out = np.repeat(np.eye(3, dtype=np.float32)[None], 256, axis=0)
    for byte in range(256):
        xa = _AXIS.get((byte & 15) - 4)
        za = _AXIS.get((byte >> 4) - 4)
        if xa is None or za is None or abs(sum(a * b for a, b in zip(xa, za))):
            continue                      # the same axis twice is not a turn
        xa, za = np.array(xa, np.float32), np.array(za, np.float32)
        out[byte] = np.stack([xa, np.cross(za, xa), za], axis=1)
    return out


_TURN = _turns()

# What makes a creation a vehicle rather than a building. Read off the names in
# the game's own shape sets, so a part added tomorrow lands in the right place
# without this list being touched.
_DRIVE = ("wheel", "thruster", "engine", "propeller", "piston", "bearing")
_SEAT = ("seat", "driver")

# What each kind of creation is called and how it is grouped. Nothing here comes
# off a tile, so none of it belongs among the generator's categories: a world's
# buildings are the generator's and a creation is yours, and a legend that files
# them together cannot be used to look at one without the other. Every kind gets
# a category of its own so the solid view can draw them apart, and so ticking
# your cars off leaves your house standing. The colours are in
# assets.CATEGORY_RGB beside the generator's, so that one legend holds both.
KINDS = {
    "Creation":  ("made",   "Creations"),
    "Vehicle":   ("driven", "Vehicles"),
    "Building":  ("built",  "Your builds"),
    "Structure": ("welded", "Welded down"),
}


class Shape(object):
    """One block or one part, in its body's own frame."""

    __slots__ = ("uuid", "pos", "size", "rgb", "part", "turn")

    def __init__(self, uuid, pos, size, rgb, part, turn=0):
        self.uuid = uuid
        self.pos = pos            # (x, y, z) in blocks, where it sits
        self.size = size          # (x, y, z) in blocks, in its own frame
        self.rgb = rgb            # (r, g, b)
        self.part = part          # a part rather than a block
        self.turn = turn          # which way a part is faced; 0 is square on

    @property
    def blocks(self):
        return self.size[0] * self.size[1] * self.size[2]


class Body(object):
    """One rigid thing: a frame, and the shapes standing in it."""

    __slots__ = ("id", "moving", "rot", "pos", "shapes")

    def __init__(self, body_id, moving, rot, pos):
        self.id = body_id
        self.moving = moving
        self.rot = rot            # 3x3, block frame -> world
        self.pos = pos            # (east, north, up) in metres
        self.shapes = []

    def extent(self):
        """Every shape's low and high corner, in the body's own blocks.

        Where a part is turned its box is read along the axes it is turned to,
        so it can run back from where the part sits rather than forward from it.
        Those axes are always a quarter turn of the grid, never a fraction of
        one, so the turned box is still square with the body and these two
        corners are the whole of it.
        """
        at = np.array([s.pos for s in self.shapes], np.float32)
        size = np.array([s.size for s in self.shapes], np.float32)
        turn = np.array([s.turn for s in self.shapes], np.uint8)
        box = np.einsum("nij,nj->ni", _TURN[turn], size)
        return at + np.minimum(box, 0), at + np.maximum(box, 0)

    def corners(self):
        """Every shape's eight corners in world metres, as an (N, 8, 3) array."""
        if not self.shapes:
            return np.zeros((0, 8, 3), np.float32)
        lo, hi = self.extent()
        pts = (lo[:, None, :] + _CORNERS[None] * (hi - lo)[:, None, :]) * BLOCK
        return pts @ self.rot.T + self.pos


class Creation(object):
    """One or more bodies that are the same thing.

    A car is not one body: the chassis is a body, and every wheel behind a
    bearing is another. The game joins them with a Joint, so following the
    joints is what turns eleven bodies back into one car.
    """

    __slots__ = ("bodies", "kind", "what", "rgb", "blocks", "parts",
                 "x0", "x1", "y0", "y1", "z0", "z1")

    def __init__(self, bodies):
        self.bodies = bodies
        self.kind = "Creation"
        self.what = ""
        self.rgb = (200, 200, 200)
        self.blocks = self.parts = 0
        self.x0 = self.y0 = self.z0 = 0.0
        self.x1 = self.y1 = self.z1 = 0.0

    @property
    def moving(self):
        return any(b.moving for b in self.bodies)

    @property
    def shapes(self):
        for b in self.bodies:
            for s in b.shapes:
                yield b, s

    @property
    def cx(self):
        return (self.x0 + self.x1) * 0.5

    @property
    def cy(self):
        return (self.y0 + self.y1) * 0.5

    @property
    def area(self):
        """Footprint in square metres."""
        return (self.x1 - self.x0) * (self.y1 - self.y0)


# -- the game's own catalogue of blocks and parts --------------------------


class Catalogue(object):
    """What every block and part is called and how big it is.

    The asset database the rest of the tool uses is the terrain catalogue --
    trees, rocks, buildings, the things a tile stands up. Blocks and parts are a
    different catalogue in a different folder, and a save is full of them, so
    they are read here.

    Everything about it is optional: a block carries its own size and its own
    colour in the save, so without the game installed a creation still comes out
    the right shape in the right colours, only with its parts unnamed.
    """

    def __init__(self, game_dir, mod_dirs=None):
        self.game_dir = game_dir
        self.by_uuid = {}
        self.mods = 0
        self._titles = {}
        self._scan()
        if mod_dirs is None:
            mod_dirs = discover.find_mod_dirs()
        self._scan_mods(mod_dirs)

    def _scan(self):
        for rel in ("Data/Objects/Database/ShapeSets",
                    "Survival/Objects/Database/ShapeSets",
                    "ChallengeData/Objects/Database/ShapeSets"):
            root = discover.resolve(os.path.join(self.game_dir,
                                                 rel.replace("/", os.sep)))
            for path in glob.glob(os.path.join(root, "*.shapeset")):
                self._file(path)

    def _scan_mods(self, mod_dirs):
        """Blocks and parts a mod brought with it.

        A modded world is full of shapes the game has never heard of, and one
        with no name and no size is drawn as a plain block in whatever colour it
        was painted. A mod folder is small, so the whole thing is walked rather
        than guessing at the layout its author chose.
        """
        for d in mod_dirs or ():
            before = len(self.by_uuid)
            for root, _dirs, names in os.walk(d):
                for n in names:
                    if n.lower().endswith(".shapeset"):
                        self._file(os.path.join(root, n))
            if len(self.by_uuid) > before:
                self.mods += 1

    def _file(self, path):
        doc = assets.load_json(path)
        if not isinstance(doc, dict):
            return
        for entries in doc.values():
            if not isinstance(entries, list):
                continue
            for a in entries:
                if isinstance(a, dict) and "uuid" in a:
                    self.by_uuid.setdefault(_key(a["uuid"]), a)
        # The shape sets name a part the way the code refers to it --
        # jnt_bearing, obj_scrap_driverseat -- and the language file is where
        # the name a player would recognise lives.
        doc = assets.load_json(discover.resolve(os.path.join(
            self.game_dir, "Data", "Gui", "Language", "English",
            "InventoryItemDescriptions.json")))
        for uuid, entry in (doc or {}).items():
            if isinstance(entry, dict) and entry.get("title"):
                self._titles[_key(uuid)] = entry["title"]

    def __len__(self):
        return len(self.by_uuid)

    def name(self, key):
        hit = self._titles.get(key)
        if hit:
            return hit
        a = self.by_uuid.get(key)
        return _pretty(a.get("name", "")) if a else ""

    def size(self, key):
        """How many blocks across a part is, from whatever shape it is given."""
        a = self.by_uuid.get(key)
        if not a:
            return _UNIT
        box = a.get("box")
        if isinstance(box, dict):
            return (max(1, int(box.get("x", 1))), max(1, int(box.get("y", 1))),
                    max(1, int(box.get("z", 1))))
        cyl = a.get("cylinder")
        if isinstance(cyl, dict):
            d = max(1, int(cyl.get("diameter", 1)))
            deep = max(1, int(cyl.get("depth", 1)))
            axis = str(cyl.get("axis", "Z")).upper()
            return {"X": (deep, d, d), "Y": (d, deep, d)}.get(axis, (d, d, deep))
        ball = a.get("sphere")
        if isinstance(ball, dict):
            d = max(1, int(ball.get("diameter", 1)))
            return (d, d, d)
        return _UNIT

    def colour(self, key):
        a = self.by_uuid.get(key)
        if not a:
            return None
        return assets._parse_hex_colour(a.get("color", ""))


def _pretty(name):
    """`obj_scrap_driverseat` -> `Scrap Driverseat`."""
    parts = [p for p in str(name).split("_")
             if p and p.lower() not in ("obj", "jnt", "blk", "part", "tool")]
    return " ".join(p[:1].upper() + p[1:] for p in parts)


def _key(uuid):
    """A uuid in the form the save writes it: sixteen bytes, back to front.

    The same reversal the script serialiser uses, and the same one the bounding
    box and the rotation of a body are written with. It is the whole record
    turned round, not the fields.
    """
    if isinstance(uuid, bytes):
        return uuid
    try:
        return bytes(bytearray.fromhex(str(uuid).replace("-", "")))[::-1]
    except ValueError:
        return b""


# The bag you drop when you go down. It is the only thing in a save that says
# where you died, and it is a shape like any other, so it is found here where
# the shapes are read rather than guessed at from a manager's bookkeeping.
KO_BAG = _key("de7eea5b-9262-476b-a5bb-238d0e91f81f")


def bags(builds):
    """Where the lost-items bags are standing, in world metres.

    Read off the bodies rather than off the respawn manager's channel. That
    channel lists only the bags whose cell is not loaded -- LostItems unmarks a
    bag the moment its cell comes in and marks it again when the cell goes out
    -- so a bag lying where you were standing when you saved is not on it at
    all. The bag itself is in the world for as long as it exists.
    """
    out = []
    for made in builds:
        for body, s in made.shapes:
            if s.uuid != KO_BAG:
                continue
            at = (np.array(s.pos, np.float32) + 0.5) * BLOCK
            out.append(tuple(float(v) for v in at @ body.rot.T + body.pos))
    return out


# -- reading the save ------------------------------------------------------


def _rotation(raw):
    """The body's rotation as a 3x3, from the four floats it is written as.

    The two kinds of body do not agree on the order. A body welded to the world
    writes it back to front -- w, z, y, x -- the same way the bounding box
    beside it and the uuid below it are written, which is what the game's own
    serialiser does to everything it touches. A body that can move writes it
    forward, x, y, z, w, which is how a physics engine holds a quaternion.

    Two things wrote the same field and neither knew about the other, and the
    difference is not academic: read a moving body the static way and a car
    parked in a clearing comes out flipped on its back several metres from
    where it stands. Which is exactly what "not connected to the world" costs
    -- a body welded to the terrain was never read wrong, so the fault showed
    only on the things that can move, which is to say on everything anybody
    built.

    Two measurements say so, and neither leans on the other. The game's own
    r-tree, across four saves: welded bodies land within a centimetre 61-73% of
    the time read back to front and 17-41% read forward, and the bodies that
    move land within a centimetre 29-64% of the time read forward and 0-32%
    read back to front. And the joints, which need no r-tree at all -- the two
    child shapes a joint holds are neighbours in the world, so the distance
    between them measures the transform with nothing else in it. Read each kind
    its own way and they touch: a median gap of a few centimetres, half to four
    fifths of them inside a block. Force the static reading on the bodies that
    move and the same joints come apart by a median of 8-10 m and as much as
    44.
    """
    if raw[1] == _MOVING:
        x, y, z, w = struct.unpack_from(">4f", raw, 27)
    else:
        w, z, y, x = struct.unpack_from(">4f", raw, 27)
    n = x * x + y * y + z * z + w * w
    if n < 1e-9:
        return np.eye(3, dtype=np.float32)
    s = 2.0 / n
    xx, yy, zz = x * x * s, y * y * s, z * z * s
    xy, xz, yz = x * y * s, x * z * s, y * z * s
    wx, wy, wz = w * x * s, w * y * s, w * z * s
    return np.array([[1 - (yy + zz), xy - wz, xz + wy],
                     [xy + wz, 1 - (xx + zz), yz - wx],
                     [xz - wy, yz + wx, 1 - (xx + yy)]], np.float32)


def _bodies(rows):
    """The RigidBody rows of one world, decoded."""
    out = {}
    for body_id, blob in rows:
        raw = bytes(blob)
        if len(raw) < 55 or raw[0] != _BODY:
            continue
        rot = _rotation(raw)
        pos = np.array(struct.unpack_from(">3f", raw, 43), np.float32)
        # A body the game's own physics lost. One old world in twenty carries
        # one: every float of its place and its angle written as NaN, something
        # that fell through the world with the solver never coming back for it.
        # There is nowhere to draw such a thing, and letting it through costs
        # more than dropping it -- a NaN corner clips to the edge of the image
        # and leaves a mark there, and a NaN vertex can take a whole draw down
        # with it.
        if not (np.isfinite(pos).all() and np.isfinite(rot).all()):
            continue
        out[body_id] = Body(body_id, raw[1] == _MOVING, rot, pos)
    return out


def _shapes(rows, cat, bodies):
    """The ChildShape rows, hung on the bodies they belong to.

    Read as columns rather than one row at a time: a well-played world has half
    a million of these, and the difference between reading them as a matrix and
    reading them as records is the difference between a moment and a minute.
    """
    groups = {}
    for body_id, blob in rows:
        raw = bytes(blob)
        group = groups.get(len(raw))
        if group is None:
            group = groups[len(raw)] = ([], [])
        group[0].append(body_id)
        group[1].append(raw)

    sizes = {}
    for width, (ids, blobs) in groups.items():
        if width < 42:
            continue
        raw = np.frombuffer(b"".join(blobs), np.uint8).reshape(len(blobs), width)
        keep = raw[:, 0] == _SHAPE
        if not keep.all():
            raw, ids = raw[keep], [i for i, k in zip(ids, keep.tolist()) if k]
        if not len(raw):
            continue
        part = raw[:, 1] != _BLOCK
        pos = raw[:, 31:37].copy().view(">i2").reshape(-1, 3)
        # Written back to front, like the uuid above it and the rotation of the
        # body it belongs to: what is in the file is blue, green, red.
        rgb = raw[:, 38:41][:, ::-1]
        size = np.ones((len(raw), 3), np.int32)
        solid = np.flatnonzero(~part)
        if len(solid):
            size[solid] = (raw[solid, 41:47].copy().view(">i2")
                           .reshape(-1, 3).astype(np.int32))
        # Where a block keeps its size a part keeps the way it is turned, in the
        # one byte at the front of the same six.
        turn = np.where(part, raw[:, 41], 0)
        uuids = raw[:, 11:27]

        pos = pos.astype(np.int32)
        rgb = rgb.astype(np.uint8)
        for n, body_id in enumerate(ids):
            body = bodies.get(body_id)
            if body is None:
                continue
            uuid = uuids[n].tobytes()
            if part[n]:
                got = sizes.get(uuid)
                if got is None:
                    got = cat.size(uuid) if cat is not None else _UNIT
                    sizes[uuid] = got
                shape_size = got
            else:
                shape_size = tuple(int(v) for v in size[n])
            body.shapes.append(Shape(uuid, tuple(int(v) for v in pos[n]),
                                     shape_size, tuple(int(v) for v in rgb[n]),
                                     bool(part[n]), int(turn[n])))


def _group(bodies, joints):
    """Bodies joined to one another are one creation.

    Union by joint, which is the only thing that says a wheel belongs to the car
    it is under rather than to the one parked beside it.
    """
    owner = {}

    def find(a):
        while owner.get(a, a) != a:
            owner[a] = owner.get(owner[a], owner[a])
            a = owner[a]
        return a

    for a, b in joints:
        ra, rb = find(a), find(b)
        if ra != rb:
            owner[ra] = rb

    out = {}
    for body_id, body in bodies.items():
        out.setdefault(find(body_id), []).append(body)
    return list(out.values())


def read(save, world_id, cat=None):
    """Every creation and structure in one world of a save.

    ``world_id`` is which world to read -- the overworld, or one floor of the
    underground -- since a save holds several and a body belongs to exactly one.
    """
    bodies = _bodies(save.rigid_bodies(world_id))
    if not bodies:
        return []
    _shapes(save.child_shapes(world_id), cat, bodies)
    joints = [(a, b) for a, b in save.joint_bodies()
              if a in bodies and b in bodies]

    out = []
    for group in _group(bodies, joints):
        made = _describe(Creation(group), cat)
        if made is not None:
            out.append(made)
    out.sort(key=lambda c: (-c.blocks, c.kind))
    return out


class Saved(object):
    """Everything one world of a save holds that no tile accounts for."""

    __slots__ = ("builds", "marks")

    def __init__(self, builds, marks):
        self.builds = builds
        self.marks = marks

    def __len__(self):
        return len(self.builds) + len(self.marks)

    @property
    def blocks(self):
        return sum(c.blocks for c in self.builds)

    def count(self, *kinds):
        return sum(1 for c in self.builds if c.kind in kinds)


def gather(save, world_id, cat=None, tick=0):
    """Read one world of a save for everything standing or marked in it."""
    from . import poi

    builds = read(save, world_id, cat)
    return Saved(builds, poi.marks(save, world_id, tick, bags(builds)))


def _describe(made, cat):
    """Measure a creation and work out what to call it, or None if it is empty."""
    pts = [b.corners() for b in made.bodies if b.shapes]
    if not pts:
        return None
    pts = np.concatenate(pts).reshape(-1, 3)
    made.x0, made.y0, made.z0 = (float(v) for v in pts.min(axis=0))
    made.x1, made.y1, made.z1 = (float(v) for v in pts.max(axis=0))

    tally = {}
    names = []
    for _body, s in made.shapes:
        if s.part:
            made.parts += 1
            if cat is not None:
                names.append(cat.name(s.uuid).lower())
        else:
            made.blocks += s.blocks
        tally[s.rgb] = tally.get(s.rgb, 0) + (1 if s.part else s.blocks)
    made.rgb = max(tally.items(), key=lambda kv: kv[1])[0] if tally else made.rgb

    drive = any(any(w in n for w in _DRIVE) for n in names)
    seat = any(any(w in n for w in _SEAT) for n in names)
    if not made.moving:
        made.kind = "Structure"
    elif drive and seat:
        made.kind = "Vehicle"
    elif made.blocks >= 200 and not drive:
        made.kind = "Building"
    made.what = _size_of(made)
    return made


def _size_of(made):
    bits = []
    if made.blocks:
        bits.append("%s block%s" % (format(made.blocks, ",d"),
                                    "" if made.blocks == 1 else "s"))
    if made.parts:
        bits.append("%d part%s" % (made.parts, "" if made.parts == 1 else "s"))
    return " and ".join(bits)


# -- onto the flat map -----------------------------------------------------


def paint(img, r, builds, structures=True):
    """Draw the creations onto the finished map, in the colours they are made of.

    Looking down, the block on top is the one you see, so the shapes go in
    height order and the last one to land on a pixel wins. That is a z-buffer
    with the sort done once instead of a comparison done per pixel, which for a
    world of half a million blocks is the difference worth having.
    """
    rows, cols = img.shape[0], img.shape[1]
    boxes, tops, rgb = [], [], []
    for made in builds:
        if not structures and made.kind == "Structure":
            continue
        for body in made.bodies:
            if not body.shapes:
                continue
            pts = body.corners()
            lo = pts.min(axis=1)
            hi = pts.max(axis=1)
            boxes.append(np.concatenate([lo[:, :2], hi[:, :2]], axis=1))
            tops.append(hi[:, 2])
            rgb.append(np.array([s.rgb for s in body.shapes], np.uint8))
    if not boxes:
        return 0

    box = np.concatenate(boxes)
    top = np.concatenate(tops)
    rgb = np.concatenate(rgb)

    # Metres to pixels of the map image, which runs north at the top.
    px = float(r.px)
    c0 = np.floor((box[:, 0] / CELL - r.x0) * px).astype(np.int64)
    c1 = np.ceil((box[:, 2] / CELL - r.x0) * px).astype(np.int64)
    r1 = np.ceil(((r.y1 + 1) - box[:, 1] / CELL) * px).astype(np.int64)
    r0 = np.floor(((r.y1 + 1) - box[:, 3] / CELL) * px).astype(np.int64)
    # Anything under a pixel across still gets the one pixel it stands on.
    c1 = np.maximum(c1, c0 + 1)
    r1 = np.maximum(r1, r0 + 1)
    c0 = np.clip(c0, 0, cols)
    c1 = np.clip(c1, 0, cols)
    r0 = np.clip(r0, 0, rows)
    r1 = np.clip(r1, 0, rows)

    w, h = c1 - c0, r1 - r0
    live = np.flatnonzero((w > 0) & (h > 0))
    if not len(live):
        return 0
    order = live[np.argsort(top[live], kind="stable")]
    n = (w[order] * h[order]).astype(np.int64)
    total = int(n.sum())
    if not total:
        return 0

    # Every covered pixel of every shape, lowest shape first, written straight
    # into the image: later writes are higher up and cover what came before.
    which = np.repeat(np.arange(len(order)), n)
    step = np.arange(total) - np.repeat(np.cumsum(n) - n, n)
    ww = np.repeat(w[order], n)
    py = np.repeat(r0[order], n) + step // ww
    pxs = np.repeat(c0[order], n) + step % ww
    img[py, pxs] = rgb[order][which]
    return len(live)


def boxes(builds, structures=True, cap=None):
    """Every block and part as a box in world metres.

    A block is a box and that is all it is, so this is exact. A part is drawn as
    the box the game gives it in its shape set -- which for a seat or a battery
    is exactly right, and for a wheel is a wheel-sized box. Standing a creation
    up out of the real art would mean reading the engine's mesh format for a
    thousand parts to add detail at the scale of a hand; the boxes are where the
    thing is, how big it is and what colour it was painted.

    Comes back as the corner of each box, the matrix that turns a unit cube into
    it, its colour, how big it is across, and which kind of creation it came off
    -- that last so the solid view can draw a car apart from a house instead of
    pouring every block into one anonymous heap.
    """
    pos, mat, rgb, rad, kind = [], [], [], [], []
    taken = 0
    for made in builds:
        if not structures and made.kind == "Structure":
            continue
        # Over the cap, whole creations are left out rather than some of the
        # blocks of one: half a house is not a smaller house, it is a wrong one.
        # They arrive biggest first, so what goes is the smallest of them.
        if cap and taken + made.blocks + made.parts > cap:
            continue
        taken += made.blocks + made.parts
        for body in made.bodies:
            if not body.shapes:
                continue
            low, high = body.extent()
            lo = low * BLOCK
            size = (high - low) * BLOCK
            pos.append(lo @ body.rot.T + body.pos)
            # One matrix per shape: the body's rotation, with the shape's own
            # size along the diagonal so the unit cube comes out that big.
            m = np.repeat(body.rot[None, :, :], len(size), axis=0).copy()
            m *= size[:, None, :]
            mat.append(m)
            rgb.append(np.array([s.rgb for s in body.shapes], np.float32))
            rad.append(np.sqrt((size * size).sum(axis=1)) * 0.5)
            kind.append(np.full(len(size), made.kind, object))
    if not pos:
        return None
    return (np.concatenate(pos), np.concatenate(mat), np.concatenate(rgb),
            np.concatenate(rad), np.concatenate(kind))


# A dropped spudgun, a sack of soil, a single wheel rolled down a hill: all of
# them are bodies, all of them are drawn where they lie, and none of them is a
# landmark. Anything smaller than this is left off the list of places rather
# than off the map.
PIN_MIN = 8


def places(builds, r, structures=True):
    """The creations as landmarks, in the shape the viewer wants them."""
    out = []
    for made in builds:
        if not structures and made.kind == "Structure":
            continue
        if made.blocks + made.parts < PIN_MIN:
            continue
        out.append({
            "kind": made.kind,
            "what": made.what,
            "tile": "",
            "cells": max(1, int(round(made.area / (CELL * CELL)))),
            "cx": round(made.cx / CELL - 0.5, 1),
            "cy": round(made.cy / CELL - 0.5, 1),
            "h": round(made.z1, 1),
        })
    out.sort(key=lambda p: (-p["cells"], p["kind"]))
    return out
