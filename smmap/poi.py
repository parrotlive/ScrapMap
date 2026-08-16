"""Name the places in a world worth going to, and say where they are.

A world's landmarks are its tiles. The generator does not scatter a warehouse
about the countryside; it lays down a `Warehouse_Exterior_2Floors_256_01` tile
and the warehouse is what stands on it. So the name of the tile under a cell is
the name of the place, and finding the places in a world is a matter of grouping
the cells each laying of a tile covers and reading its name.

The names are systematic enough to classify by, which is what gives the viewer
its legend: `BuilderQuest_Baguette_128_01` is a builder quest, and every builder
quest in the game is named that way, so "show me only the builder quests" is a
question the tile names can answer on their own.

Tiles that are only scenery -- meadows, forest, road and cliff pieces -- are not
places and are left out; what is left is what a player would put a pin in.
"""

CELL = 64.0

# Tile-name fragment -> what to call it. Order matters: the first match wins, so
# anything that is a special case of something else has to come above it
# (MechanicStation_QuestTile before MechanicStation, ChemicalLake before Lake).
_KINDS = (
    ("mechanicstation_questtile", "Mechanic Station (quest)"),
    ("builderquest",              "Builder Quest"),
    ("bunkinvestigation",         "Bunk Investigation"),
    ("mechanicstation",           "Mechanic Station"),
    ("schematicstation",          "Schematic Station"),
    ("packingstation",            "Packing Station"),
    ("undergroundstation",        "Underground Station"),
    ("minidungeon",               "Minidungeon Entrance"),
    ("overworldtounderground",    "Cave Entrance"),
    ("drillbottrainstation",      "Drillbot Train Station"),
    ("excavationisland",          "Excavation Island"),
    ("warehouse",                 "Warehouse"),
    ("silodistrict",              "Silo District"),
    ("ruincity",                  "Ruined City"),
    ("hideout",                   "Hideout"),
    ("scrapyard",                 "Scrapyard"),
    ("farmbotgraveyard",          "Farmbot Graveyard"),
    ("farmingpatch",              "Farming Patch"),
    ("chemicalplant",             "Chemical Plant"),
    ("chemicallake",              "Chemical Lake"),
    ("oillake",                   "Oil Lake"),
    ("oilpool",                   "Oil Pool"),
    ("haybalelabyrinth",          "Hay Bale Labyrinth"),
    ("sleepcapsuleburial",        "Sleep Capsule Burial"),
    ("campingspot",               "Camping Spot"),
    ("crashedship",               "Crashed Ship"),
    ("survivalstartarea",         "Start Area"),
    ("kiosk",                     "Kiosk"),
    ("ruin",                      "Ruin"),
)

# Biomes whose tiles are places rather than landscape. Everything else -- the
# meadows, the forests, the road and cliff pieces the generator joins them with
# -- is the countryside between the places, and putting a pin in each would bury
# the map under thousands of them.
_PLACE_BIOMES = ("poi", "questtiles", "start_area", "excavation", "bosstrain")

# A tile in a place biome whose name says nothing more specific than the biome it
# decorates. These are the generator's filler variants -- a meadow with a bit
# more going on -- and they outnumber the real landmarks several times over, so
# they get their own kind and the viewer starts with them switched off.
_FILLER = "Random Site"

# Words a variant is better off without: they describe the tile's role, which
# the kind has already said, rather than which one of its kind this is.
_NOISE = ("overworld", "overworldentrance", "entrance", "exterior")


def _classify(tile):
    """(kind, the name fragment that decided it), or (None, None).

    The fragment comes back so the variant can be worked out by taking away
    whatever the kind already said.
    """
    if tile.biome not in _PLACE_BIOMES:
        return None, None
    low = tile.name.lower().replace("_", "")
    for frag, label in _KINDS:
        flat = frag.replace("_", "")
        if flat in low:
            return label, flat
    if tile.name.lower().startswith("random"):
        return _FILLER, low
    # A place biome with an unrecognised name is still a place; the tile name is
    # a better answer than dropping it, and it keeps new content visible when
    # the game adds some.
    return _pretty(tile.name), low


def kind_of(tile):
    """What kind of place this tile is, or None if it is only landscape."""
    return _classify(tile)[0]


def _pretty(name):
    """`Warehouse_Exterior_2Floors_256_01` -> `Warehouse Exterior 2Floors`."""
    parts = []
    for p in name.split("_"):
        # The trailing size and serial carry nothing a reader wants.
        if p.isdigit() or p.upper() == "NEW":
            continue
        parts.append(p)
    return " ".join(parts) or name


def _variant(tile, frag):
    """The distinguishing half of a tile name, for a subtitle.

    `BuilderQuest_Baguette_128_01` classified by the fragment "builderquest" is
    a Baguette, which is the only part of that name a player is looking for.
    Words the fragment already accounts for come out; the size and the serial
    were never worth reading.
    """
    rest = []
    for p in tile.name.split("_"):
        low = p.lower()
        if p.isdigit() or p.upper() == "NEW" or low in frag or low in _NOISE:
            continue
        rest.append(p)
    return " ".join(rest)


def _components(cells):
    """Split one tile's cells into the separate layings of that tile.

    A world lays the same tile down many times and each laying is a place in its
    own right, so cells are grouped by touching rather than by which tile they
    came from. Two layings of one tile that happen to abut would read as one
    place; the generator does not put two warehouses shoulder to shoulder.
    """
    left = set(cells)
    out = []
    while left:
        start = left.pop()
        group, stack = [start], [start]
        while stack:
            i, j = stack.pop()
            for n in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if n in left:
                    left.discard(n)
                    group.append(n)
                    stack.append(n)
        out.append(group)
    return out


def collect(r):
    """Every place in the rendered world, as plain dicts.

    Needs a render made with fields=True, which is what leaves behind the
    cell-to-tile map this walks. Positions come back in world metres and in cell
    coordinates, so a caller can put them wherever it needs them.
    """
    if not r.placements:
        return []

    out = []
    for tile, cells in r.placements.items():
        kind, frag = _classify(tile)
        if kind is None:
            continue
        what = _variant(tile, frag)
        for group in _components([(c[0], c[1]) for c in cells]):
            ci = sum(c[0] for c in group) / float(len(group))
            cj = sum(c[1] for c in group) / float(len(group))
            out.append({
                "kind": kind,
                "what": what,
                "tile": tile.name,
                "cells": len(group),
                # Cell coordinates, as the game and the flat map name them.
                # World metres follow from these, so they are not carried too.
                "cx": round(r.x0 + ci, 1),
                "cy": round(r.y0 + cj, 1),
                "h": round(_height_at(r, ci, cj), 1),
            })

    # Biggest first: a silo district should come above a camping spot in any
    # list, and the viewer draws them in this order too.
    out.sort(key=lambda p: (-p["cells"], p["kind"], p["what"]))
    return out


def _height_at(r, ci, cj):
    """Ground height at the middle of a place, in metres."""
    hm = getattr(r, "height_map", None)
    if hm is None:
        return 0.0
    px = r.px
    # The map image runs north at the top, which is the flip render() applies.
    row = int((r.h - 1 - cj) * px + px * 0.5)
    col = int(ci * px + px * 0.5)
    row = max(0, min(hm.shape[0] - 1, row))
    col = max(0, min(hm.shape[1] - 1, col))
    v = float(hm[row, col])
    return 0.0 if v != v else v          # NaN guard


def summary(places):
    """kind -> how many, biggest kinds first. What the legend is built from."""
    counts = {}
    for p in places:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
