"""Turn a finished render into the handful of textures a 3D view needs.

The flat map and the solid one are made of the same three things: what colour
the ground is, how high it is, and where the water stands. render() folds the
last two into the first as shading; this takes them before that happens and
packs them for a GPU instead.

Colour keeps the resolution it was drawn at, because it is what you actually
look at. Height does not: a 9 km world at two metres per pixel is a sixteen
megapixel grid, and no browser wants thirty million triangles. It is reduced by
a whole-number factor to something a card can chew through, which is enough for
the shape of the land -- the detail that reduction would lose is in the colour
image, still at full resolution, draped over the top.

Ground is averaged as it shrinks and props are maxed. Averaging a warehouse
with the field it stands in leaves a bump a metre high, and the buildings
standing up is most of the point of looking at the world in 3D.
"""

import numpy as np

from .detail import NO_LIQUID

# Height is packed into an 8-bit RGBA texture, which is the only kind every
# WebGL2 device is guaranteed to be able to sample: red and green are the solid
# surface as a 16-bit fraction of the world's height range, blue and alpha are
# the liquid surface in the same range.
#
# The liquid half gives up its bottom two bits to the kind of liquid it is,
# leaving fourteen for the level. Over the couple of hundred metres a world
# spans that is still a centimetre, and water is flat.
KIND_BITS = 2
LEVEL_MAX = (1 << (16 - KIND_BITS)) - 1        # 16383
# Level 0 is not a low level, it is no liquid at all, so levels start at 1.
LEVEL_SPAN = LEVEL_MAX - 1                     # 16382

# Cap for the two textures, in texels on the long side. The height cap is what
# the shape of the land is worth: at this size a full 9 km world gets a height
# every twelve metres, which is enough for a warehouse to still be a box rather
# than a bump, and it costs a couple of megabytes in the page. The colour cap
# only has to stay inside what a modest GPU will accept, and the page shrinks
# it further if the one it is running on says less.
HEIGHT_TEXELS = 1536
COLOUR_TEXELS = 4096

# What the detail setting is worth to the ground itself. A fixed cap makes the
# ground mesh the same at every setting -- asking for fine and asking for normal
# both land on 1536 and give the same land, and the extra time buys nothing you
# can see. The cap follows the resolution instead, so quick is cheaper, fine is
# finer, and the floor and ceiling keep either end sensible.
HEIGHT_TEXELS_MIN = 1024
HEIGHT_TEXELS_MAX = 3072
REFERENCE_PX = 32


def texel_target(r):
    """The height-texture cap for a render, from the resolution it was made at.

    Everything that lays the world out must agree on this -- the height, the
    prop heights, the colour's padding and the extent the objects are placed
    against -- or the ground and the things standing on it drift apart.
    """
    px = getattr(r, "px", REFERENCE_PX) or REFERENCE_PX
    scaled = int(round(HEIGHT_TEXELS * px / float(REFERENCE_PX)))
    return max(HEIGHT_TEXELS_MIN, min(HEIGHT_TEXELS_MAX, scaled))

# The tallest prop the shadow texture can describe. Nothing the world generator
# places comes near it -- the silo is the tallest thing in a survival world and
# it is well under forty metres.
PROP_CEILING = 64.0


def _blocks(h, w, target):
    """Reduction factor and result shape for shrinking (h, w) to <= target.

    The factor is whole and shared by both axes so that texels stay square, and
    it is rounded up rather than to nearest, so that the target is a cap the
    result cannot go over -- a world a little larger than the cap must come
    down to half of it, not sit above it. The shape is then rounded up in turn,
    so the reduced grid covers the map and a little over rather than cropping
    it.
    """
    f = max(1, -(-max(h, w) // target))
    return f, -(-h // f), -(-w // f)


def _pad(a, f, dh, dw):
    """Edge-extend an array so a f x f block reduction divides it exactly.

    At most f - 1 rows and columns are added -- three, on the world this is
    tuned for -- so the world the viewer lays out is a fraction of a percent
    wider than the one that was rendered. Both textures are padded the same
    way, so they still line up with each other exactly, which is what matters.
    """
    pad = [(0, dh * f - a.shape[0]), (0, dw * f - a.shape[1])]
    pad += [(0, 0)] * (a.ndim - 2)
    if not any(p[1] for p in pad):
        return a
    return np.pad(a, pad, mode="edge")


def _reduce(a, f, dh, dw, how):
    """Block-reduce a padded 2D array by f, taking the mean or the max."""
    b = _pad(a, f, dh, dw).reshape(dh, f, dw, f)
    return b.mean(axis=(1, 3)) if how == "mean" else b.max(axis=(1, 3))


def _sea_level(water):
    """The level most of the world's water stands at, or None if it has none.

    Only used to place the camera and to draw the sky's reflection, so a rough
    answer from rounded metres is plenty.
    """
    wet = water[water > NO_LIQUID]
    if not wet.size:
        return None
    vals, n = np.unique(np.round(wet, 1), return_counts=True)
    return float(vals[int(np.argmax(n))])


def extent(r, target=None):
    """What the textures will cover on the ground, in metres, padding included.

    The same answer height_texture gives, without packing a texture to get it,
    for callers that only need to know how big the world is laid out.
    """
    target = target or texel_target(r)
    f, dh, dw = _blocks(r.height_map.shape[0], r.height_map.shape[1], target)
    mpp = 64.0 / r.px
    return dw * f * mpp, dh * f * mpp


def _ground(r):
    return np.nan_to_num(r.height_map.astype(np.float32), nan=0.0,
                         posinf=0.0, neginf=0.0)


def _top(r):
    return (np.zeros(r.height_map.shape, np.float32) if r.top_map is None
            else np.maximum(r.top_map.astype(np.float32), 0.0))


def prop_texture(r, target=None):
    """How far the props stand above the ground, a byte a texel.

    Only the shadow pass wants this. When the props are drawn as real meshes
    they are no longer part of the ground, so the ground mesh must not have
    their bumps in it -- but their shadows should still fall across the land,
    and a shadow does not need a millimetre. A byte over sixty-four metres is a
    quarter of a metre, and over most of a world it is zero, which is why the
    whole thing costs a few tens of kilobytes.
    """
    target = target or texel_target(r)
    f, dh, dw = _blocks(r.height_map.shape[0], r.height_map.shape[1], target)
    top = _reduce(_top(r), f, dh, dw, "max")
    return np.round(np.clip(top / PROP_CEILING, 0.0, 1.0) * 255.0).astype(np.uint8)


def height_texture(r, target=None, props=True):
    """Pack the render's height fields into an (dh, dw, 4) uint8 array.

    Returns the array and the numbers needed to read it back: the world height
    the texture's 0 and 1 stand for, how many metres a texel covers, and how
    much of the map the padding added.

    ``props`` puts what stands on the ground into the ground, which is what the
    map does and what a viewer with no other way to show a building wants. Turn
    it off when the buildings are going in as real geometry, or the world gets
    each of them twice: once as a mesh and once as a lump under it.
    """
    target = target or texel_target(r)
    ground = _ground(r)
    top = _top(r) if props else np.zeros(ground.shape, np.float32)
    water = r.water_level.astype(np.float32)
    kind = r.water_kind

    h, w = ground.shape
    f, dh, dw = _blocks(h, w, target)

    # The land keeps its average height and whatever stands on it keeps its
    # full height: see the module docstring.
    solid = _reduce(ground, f, dh, dw, "mean") + _reduce(top, f, dh, dw, "max")
    # A block holding any water at all is water. That floods the shoreline by
    # up to one texel, and the shader takes it straight back off again by
    # refusing to draw water that the solid surface already stands above.
    surf = _reduce(water, f, dh, dw, "max")
    kinds = _reduce(kind.astype(np.float32), f, dh, dw, "max").astype(np.uint8)

    wet = surf > NO_LIQUID
    lo = float(solid.min())
    hi = float(solid.max())
    if wet.any():
        lo = min(lo, float(surf[wet].min()))
        hi = max(hi, float(surf[wet].max()))
    # A dead flat world would divide by zero, and a range of a few metres
    # quantises so finely that the packing is wasted; a metre either way costs
    # nothing and avoids both.
    lo, hi = lo - 1.0, hi + 1.0

    span = hi - lo
    q = np.clip((solid - lo) / span, 0.0, 1.0)
    q = np.round(q * 65535.0).astype(np.uint32)

    level = np.zeros((dh, dw), dtype=np.uint32)
    if wet.any():
        t = np.clip((surf[wet] - lo) / span, 0.0, 1.0)
        level[wet] = 1 + np.round(t * LEVEL_SPAN).astype(np.uint32)
    packed = (level << KIND_BITS) | np.minimum(kinds, (1 << KIND_BITS) - 1)

    out = np.empty((dh, dw, 4), dtype=np.uint8)
    out[:, :, 0] = (q >> 8).astype(np.uint8)
    out[:, :, 1] = (q & 255).astype(np.uint8)
    out[:, :, 2] = (packed >> 8).astype(np.uint8)
    out[:, :, 3] = (packed & 255).astype(np.uint8)

    mpp = 64.0 / r.px
    return out, {
        "lo": lo,
        "hi": hi,
        "metresPerTexel": f * mpp,
        # What the textures span on the ground, padding included. The mesh is
        # laid out over this, so it is the size the world is drawn at.
        "spanX": dw * f * mpp,
        "spanY": dh * f * mpp,
        "texW": dw,
        "texH": dh,
        "sea": _sea_level(water),
    }


def colour_texture(r, cap=COLOUR_TEXELS):
    """The unshaded ground colour as a PIL image, padded to match the height.

    Padded, and then reduced only if it is larger than a GPU is obliged to
    accept. The 3D view does its own lighting, so what goes up is the colour
    before render() shaded it.
    """
    from PIL import Image

    f, dh, dw = _blocks(r.albedo.shape[0], r.albedo.shape[1], texel_target(r))
    img = Image.fromarray(_pad(r.albedo, f, dh, dw))
    if max(img.size) > cap:
        k = cap / float(max(img.size))
        img = img.resize((max(1, int(round(img.width * k))),
                          max(1, int(round(img.height * k)))),
                         Image.LANCZOS)
    return img


def payload(r, cd, objects=False):
    """Everything the page needs about the world, ready to be JSON'd."""
    hx, meta = height_texture(r, props=not objects)
    meta.update({
        "propCeiling": PROP_CEILING,
        "seed": str(cd.get("seed", "?")),
        "cellsX": r.w,
        "cellsY": r.h,
        # Cell coordinates of the map's north-west corner, so the readout can
        # name a place the same way the flat map does.
        "x0": r.x0,
        "y1": r.y1,
        "metresPerCell": 64.0,
    })
    return hx, meta
