"""Read the meshes the game actually draws.

Every asset in the catalogue points at a ``.rend``, and every ``.rend`` points at
a mesh in Autodesk's binary FBX. That is the art -- the warehouse with its
panelling, the tree with its leaves -- as against the collision hull beside it,
which is the same building as a box. Drawing the world from the collision hulls
was the honest thing to do while this could not read FBX; it can now, so it
draws what the game draws.

The format is a tree of records. A record is its own end offset, a count of
properties, the byte length of those properties, a name, the properties, then
child records until the end offset, closed by a run of zeros the same width as a
record header. Properties are tagged one byte each: the scalars are obvious, and
the bulk ones -- vertices, indices, normals -- are arrays carrying a count, an
encoding flag and a byte length, raw or deflated.

Only the geometry is wanted here, which is a small part of what an FBX can hold:
no animation, no skinning, no cameras, no lights. What is needed is the
positions, the normals, the texture coordinates, and which material each polygon
was assigned, because a material is what names the texture to put on it.
"""

import os
import struct
import zlib

import numpy as np

_MAGIC = b"Kaydara FBX Binary  "

# Scalar property tags, as (struct format, byte width).
_SCALAR = {
    "Y": ("<h", 2), "C": ("<?", 1), "I": ("<i", 4),
    "F": ("<f", 4), "D": ("<d", 8), "L": ("<q", 8),
}
# Array property tags and the numpy type each decodes to.
_ARRAY = {"f": "<f4", "d": "<f8", "l": "<i8", "i": "<i4", "c": "u1", "b": "u1"}


class Node(object):
    __slots__ = ("name", "props", "kids")

    def __init__(self, name, props, kids):
        self.name = name
        self.props = props
        self.kids = kids

    def find(self, name):
        for k in self.kids:
            if k.name == name:
                return k
        return None

    def all(self, name):
        return [k for k in self.kids if k.name == name]

    def value(self, name, default=None):
        k = self.find(name)
        return k.props[0] if k is not None and k.props else default


def _read_prop(data, off):
    tag = chr(data[off])
    off += 1
    hit = _SCALAR.get(tag)
    if hit is not None:
        fmt, n = hit
        return off + n, struct.unpack_from(fmt, data, off)[0]
    dt = _ARRAY.get(tag)
    if dt is not None:
        n, enc, clen = struct.unpack_from("<III", data, off)
        off += 12
        raw = data[off:off + clen]
        off += clen
        if enc:
            raw = zlib.decompress(raw)
        return off, np.frombuffer(raw, dtype=dt, count=n)
    if tag in "SR":
        n = struct.unpack_from("<I", data, off)[0]
        off += 4
        raw = data[off:off + n]
        off += n
        # A name is stored "child\0\1parent"; callers want the child.
        return off, (raw.decode("utf-8", "replace") if tag == "S" else raw)
    raise ValueError("unknown FBX property tag %r" % tag)


def _split_props(text):
    """Split an ASCII property list on its commas, respecting quotes."""
    out, cur, quoted = [], [], False
    for ch in text:
        if ch == '"':
            quoted = not quoted
            continue
        if ch == "," and not quoted:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail or out:
        out.append(tail)
    return out


def _scalar(tok):
    if tok.startswith("*"):                 # an array's declared length
        tok = tok[1:]
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        return tok


def parse_ascii(path):
    """The text form of the same tree.

    A fifth of everything a world stands up -- every bush, shrub, fern and
    sprout -- ships as ASCII FBX rather than binary, so this is not a corner to
    be skipped: without it a world loses its undergrowth.

    The layout is ``Name: properties {`` with children until a closing brace,
    and a bulk array is a block of its own holding a single ``a:`` line whose
    numbers wrap freely across lines.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    root = Node("", [], [])
    stack = [root]
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].strip()
        i += 1
        if not line or line[0] == ";":
            continue
        if line[0] == "}":
            if len(stack) > 1:
                stack.pop()
            continue
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name, rest = name.strip(), rest.strip()

        if name == "a":
            # The payload of the array block we are already inside.
            parts = [rest]
            while i < n:
                s = lines[i].strip()
                if s.startswith("}"):
                    break
                parts.append(s)
                i += 1
            text = "".join(parts).rstrip(",")
            if text:
                stack[-1].props.append(
                    np.array([float(v) for v in text.split(",") if v], np.float64))
            continue

        opens = rest.endswith("{")
        if opens:
            rest = rest[:-1].strip()
        # An array block announces its length as ``*N`` before the brace. That
        # is a count, not a value: the data arrives on the ``a:`` line inside,
        # and it has to land in props[0] where every reader looks for it.
        props = ([] if rest.startswith("*")
                 else [_scalar(t) for t in _split_props(rest)] if rest else [])
        node = Node(name, props, [])
        stack[-1].kids.append(node)
        if opens:
            stack.append(node)
    return 7400, root.kids


def parse(path):
    """The whole file as a list of top-level Nodes, plus its version."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:len(_MAGIC)] != _MAGIC:
        # Not binary; the same tree is also written as text.
        if b"FBXHeaderExtension" in data[:4096]:
            return parse_ascii(path)
        raise ValueError("not an FBX")
    version = struct.unpack_from("<I", data, 23)[0]
    # From 7500 the three record offsets widen from 32 to 64 bits.
    wide = version >= 7500
    head = "<QQQ" if wide else "<III"
    hsize = 24 if wide else 12
    end = len(data)

    def node(off):
        if off + hsize + 1 > end:
            return None, end
        stop, nprops, plen = struct.unpack_from(head, data, off)
        off += hsize
        nlen = data[off]
        off += 1
        # The sentinel that closes a list of children is an all-zero header.
        if stop == 0:
            return None, off
        name = data[off:off + nlen].decode("utf-8", "replace")
        off += nlen
        props = []
        stop_props = off + plen
        for _ in range(nprops):
            off, v = _read_prop(data, off)
            props.append(v)
        off = stop_props
        kids = []
        while off < stop:
            kid, off = node(off)
            if kid is None:
                break
            kids.append(kid)
        return Node(name, props, kids), stop

    out = []
    off = 27
    while off < end:
        n, off = node(off)
        if n is None:
            break
        out.append(n)
    return version, out


# -- the object graph -----------------------------------------------------

def _name(prop):
    """The bare name out of either spelling of a qualified one.

    Binary writes ``thing\\0\\1Kind`` and text writes ``Kind::thing``, so the
    name is on opposite sides of the separator in the two forms.
    """
    s = prop if isinstance(prop, str) else ""
    if "\x00" in s:
        return s.split("\x00", 1)[0]
    if "::" in s:
        return s.split("::", 1)[1]
    return s


def _properties(node):
    """Properties70 as a plain dict of name -> the values after the type tags."""
    out = {}
    p70 = node.find("Properties70")
    if p70 is None:
        return out
    for p in p70.all("P"):
        if p.props:
            out[p.props[0]] = p.props[4:]
    return out


def _euler(deg, order=0):
    """FBX Lcl Rotation -> matrix. Order 0 is XYZ, which is the default."""
    x, y, z = (np.radians(float(v)) for v in deg)
    cx, sx, cy, sy, cz, sz = (np.cos(x), np.sin(x), np.cos(y),
                              np.sin(y), np.cos(z), np.sin(z))
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], np.float64)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], np.float64)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], np.float64)
    # FBX composes its default euler as R = Rz * Ry * Rx applied to a column.
    return rz @ ry @ rx


def _model_matrix(props):
    """A model's own placement as a 4x4, from the parts the game's files use.

    The format allows pivots, offsets and pre/post rotations on top of this;
    none of the terrain art carries them, so composing them would be code that
    is never exercised. Geometric transforms do occur and are folded in, as
    they belong to the mesh rather than to the node.
    """
    t = np.array([float(v) for v in props.get("Lcl Translation", (0, 0, 0))][:3]
                 or [0, 0, 0], np.float64)
    r = [float(v) for v in props.get("Lcl Rotation", (0, 0, 0))][:3] or [0, 0, 0]
    s = [float(v) for v in props.get("Lcl Scaling", (1, 1, 1))][:3] or [1, 1, 1]
    m = np.eye(4)
    m[:3, :3] = _euler(r) * np.asarray(s, np.float64)
    m[:3, 3] = t
    gt = [float(v) for v in props.get("GeometricTranslation", (0, 0, 0))][:3]
    gr = [float(v) for v in props.get("GeometricRotation", (0, 0, 0))][:3]
    gs = [float(v) for v in props.get("GeometricScaling", (1, 1, 1))][:3]
    if any(gt) or any(gr) or gs != [1.0, 1.0, 1.0]:
        g = np.eye(4)
        g[:3, :3] = _euler(gr) * np.asarray(gs, np.float64)
        g[:3, 3] = gt
        m = m @ g
    return m


# -- layer elements -------------------------------------------------------

def _layer(geo, kind, data_name, index_name):
    """One layer element as (values, indices, mapping) or None.

    ``mapping`` is one of the strings FBX uses to say what a value belongs to:
    a polygon vertex, a vertex, a polygon, or the whole mesh.
    """
    node = geo.find(kind)
    if node is None:
        return None
    vals = node.value(data_name)
    if vals is None:
        return None
    mapping = node.value("MappingInformationType", "")
    ref = node.value("ReferenceInformationType", "")
    idx = None
    if "IndexToDirect" in str(ref):
        idx = node.value(index_name)
    return vals, idx, str(mapping)


def _expand(layer, width, corner_of_vertex, npoly_corners, poly_of_corner):
    """A layer element resolved to one value per polygon corner.

    Every mapping the game's exporter emits is handled: values held per corner,
    per vertex, per polygon, or one for the whole mesh.
    """
    vals, idx, mapping = layer
    vals = np.asarray(vals).reshape(-1, width)
    if mapping == "ByPolygonVertex":
        key = np.arange(npoly_corners)
    elif mapping in ("ByVertice", "ByVertex"):
        key = corner_of_vertex
    elif mapping == "ByPolygon":
        key = poly_of_corner
    elif mapping in ("AllSame", "ByModel"):
        key = np.zeros(npoly_corners, np.int64)
    else:
        return None
    if idx is not None:
        # The text form writes every array as numbers, so an index arrives as
        # a float and has to be made one again before it can index anything.
        idx = np.asarray(idx).astype(np.int64)
        if key.max(initial=-1) >= len(idx):
            return None
        key = idx[key]
    key = np.clip(key, 0, len(vals) - 1)
    return vals[key]


class SubMesh(object):
    """One material's worth of a mesh, ready to hand to a GPU."""

    __slots__ = ("material", "pos", "nrm", "uv", "tris")

    def __init__(self, material, pos, nrm, uv, tris):
        self.material = material
        self.pos = pos
        self.nrm = nrm
        self.uv = uv
        self.tris = tris

    def __repr__(self):
        return "<SubMesh %r %d verts %d tris>" % (self.material, len(self.pos),
                                                  len(self.tris))


def _triangulate(pvi):
    """Polygon-vertex indices -> corner triples, and which polygon each is in.

    FBX marks the last corner of a polygon by storing it negative, ones
    complemented. Polygons are convex, so fanning from the first corner is
    exact rather than an approximation.
    """
    endings = pvi < 0
    starts = np.empty(len(pvi), np.int64)
    starts[0] = 0
    starts[1:] = np.cumsum(endings)[:-1]
    poly_start = np.flatnonzero(np.concatenate([[True], endings[:-1]]))
    sizes = np.diff(np.concatenate([poly_start, [len(pvi)]]))

    tri = []
    poly = []
    for p, (s, n) in enumerate(zip(poly_start.tolist(), sizes.tolist())):
        for k in range(1, n - 1):
            tri.append((s, s + k, s + k + 1))
            poly.append(p)
    if not tri:
        return (np.zeros((0, 3), np.int64), np.zeros(0, np.int64),
                np.zeros(len(pvi), np.int64))
    return (np.asarray(tri, np.int64), np.asarray(poly, np.int64), starts)


def read(path, want_uv=True):
    """Every submesh in an FBX, in the file's own units and axes (Y up).

    Returns a list of SubMesh. Geometry that a Model places is transformed by
    that Model, so a mesh assembled from several nodes comes back assembled.
    """
    version, roots = parse(path)
    top = Node("", [], roots)
    objects = top.find("Objects")
    connections = top.find("Connections")
    if objects is None:
        return []

    geos, models, materials = {}, {}, {}
    for k in objects.kids:
        if not k.props:
            continue
        pid = k.props[0]
        if k.name == "Geometry":
            geos[pid] = k
        elif k.name == "Model":
            models[pid] = k
        elif k.name == "Material":
            materials[pid] = _name(k.props[1] if len(k.props) > 1 else "")

    # child -> parent, in the order the file states them: the order materials
    # arrive in is the index space LayerElementMaterial indexes into.
    parent = {}
    mats_of = {}
    if connections is not None:
        for c in connections.all("C"):
            if len(c.props) < 3:
                continue
            kind, child, par = c.props[0], c.props[1], c.props[2]
            if kind != "OO":
                continue
            if child in materials:
                mats_of.setdefault(par, []).append(materials[child])
            else:
                parent[child] = par

    def world(model_id):
        """A model's matrix with every ancestor's applied over it."""
        m = np.eye(4)
        chain = []
        at = model_id
        seen = set()
        while at in models and at not in seen:
            seen.add(at)
            chain.append(at)
            at = parent.get(at)
        for mid in reversed(chain):
            m = m @ _model_matrix(_properties(models[mid]))
        return m

    out = []
    for gid, geo in geos.items():
        verts = geo.value("Vertices")
        pvi = geo.value("PolygonVertexIndex")
        if verts is None or pvi is None or not len(pvi):
            continue
        verts = np.asarray(verts, np.float64).reshape(-1, 3)
        pvi = np.asarray(pvi, np.int64)
        # The negative marker is one's complement, not a negation.
        corner_vertex = np.where(pvi < 0, ~pvi, pvi)
        if corner_vertex.max(initial=-1) >= len(verts):
            continue

        tri, tri_poly, _ = _triangulate(pvi)
        if not len(tri):
            continue
        npoly = int(tri_poly.max()) + 1 if len(tri_poly) else 0
        poly_of_corner = np.zeros(len(pvi), np.int64)
        ends = np.flatnonzero(pvi < 0)
        if len(ends):
            poly_of_corner[ends[:-1] + 1] = 1
            poly_of_corner = np.cumsum(poly_of_corner)

        mid = parent.get(gid)
        m = world(mid) if mid is not None else np.eye(4)
        pos_v = verts @ m[:3, :3].T + m[:3, 3]

        nl = _layer(geo, "LayerElementNormal", "Normals", "NormalsIndex")
        ul = (_layer(geo, "LayerElementUV", "UV", "UVIndex")
              if want_uv else None)
        ml = _layer(geo, "LayerElementMaterial", "Materials", "Materials")

        corners = pos_v[corner_vertex]
        nrm = _expand(nl, 3, corner_vertex, len(pvi), poly_of_corner) if nl else None
        if nrm is not None:
            # A normal is a direction: it takes the inverse transpose, and the
            # game's exporter writes non-uniform scales often enough to matter.
            try:
                nm = np.linalg.inv(m[:3, :3]).T
            except np.linalg.LinAlgError:
                nm = m[:3, :3]
            nrm = nrm @ nm.T
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            nrm = nrm / np.where(ln > 1e-12, ln, 1.0)
        uv = _expand(ul, 2, corner_vertex, len(pvi), poly_of_corner) if ul else None

        # Which material each triangle belongs to.
        if ml is not None:
            vals, idx, mapping = ml
            vals = np.asarray(vals).reshape(-1)
            if mapping == "AllSame" or len(vals) == 1:
                tri_mat = np.zeros(len(tri), np.int64)
                tri_mat[:] = int(vals[0]) if len(vals) else 0
            else:
                tri_mat = vals[np.clip(tri_poly, 0, len(vals) - 1)].astype(np.int64)
        else:
            tri_mat = np.zeros(len(tri), np.int64)

        names = mats_of.get(mid, []) if mid is not None else []
        for slot in np.unique(tri_mat):
            keep = tri[tri_mat == slot]
            if not len(keep):
                continue
            flat = keep.reshape(-1)
            p = corners[flat].astype(np.float32)
            n = (nrm[flat].astype(np.float32) if nrm is not None
                 else np.zeros((len(flat), 3), np.float32))
            t = (uv[flat].astype(np.float32) if uv is not None
                 else np.zeros((len(flat), 2), np.float32))
            p, n, t, faces = _weld(p, n, t)
            mat = names[slot] if 0 <= slot < len(names) else ""
            out.append(SubMesh(mat, p, n, t, faces))
    return out


def _weld(pos, nrm, uv):
    """Fuse identical corners so a cube is eight vertices and not thirty-six.

    Corners are compared on everything they carry, so a hard edge -- two
    corners at one place with different normals -- stays two vertices, which is
    what keeps the shading crisp.
    """
    n = len(pos)
    key = np.empty((n, 8), np.float32)
    key[:, 0:3] = pos
    key[:, 3:6] = nrm
    key[:, 6:8] = uv
    # A void view turns each row into one opaque value that np.unique can sort.
    view = np.ascontiguousarray(key).view(
        np.dtype((np.void, key.dtype.itemsize * key.shape[1])))
    _, first, inverse = np.unique(view.ravel(), return_index=True,
                                  return_inverse=True)
    order = np.argsort(first)
    remap = np.empty(len(first), np.int64)
    remap[order] = np.arange(len(first))
    tris = remap[inverse].reshape(-1, 3).astype(np.uint32)
    pick = first[order]
    return pos[pick], nrm[pick], uv[pick], tris


def triangle_count(path):
    """Triangles in a file without building any arrays for them."""
    try:
        _, roots = parse(path)
    except (OSError, ValueError, zlib.error, struct.error):
        return 0
    objects = Node("", [], roots).find("Objects")
    if objects is None:
        return 0
    total = 0
    for k in objects.kids:
        if k.name != "Geometry":
            continue
        pvi = k.value("PolygonVertexIndex")
        if pvi is None:
            continue
        pvi = np.asarray(pvi)
        # Each polygon of n corners fans to n - 2 triangles.
        total += len(pvi) - 2 * int((pvi < 0).sum())
    return total
