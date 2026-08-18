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

That accounts for the places the generator laid down. A played world has others
that no tile knows about -- where you sleep, where you put a beacon, where the
lift down is, where the fighting has been, where the game is currently pointing
you -- and those the save keeps itself, on the numbered storage channels the
game's own managers write to. marks() reads them.
"""

import re

CELL = 64.0

# The sm.storage channels worth reading, by the names survival_constants.lua
# gives them. Everything else on there is bookkeeping: which crates have been
# opened, which fires are lit, how far each patrolling bot has walked.
CH_SPAWNERS = 13          # tagged nodes, one of which is where a world starts
CH_BEDS = 15              # the bed each player last slept in
CH_UNITS = 17             # where the last hundred robots died
CH_BAGS = 19              # the bag you dropped when you died, if it is still out
CH_PERMANENT_BEDS = 34
CH_BEACONS = 35
CH_QUEST_ENTITIES = 46
CH_ELEVATORS = 73         # every lift down to the underground

_CHANNELS = (CH_SPAWNERS, CH_BEDS, CH_UNITS, CH_BAGS, CH_PERMANENT_BEDS,
             CH_BEACONS, CH_QUEST_ENTITIES, CH_ELEVATORS)

TICKS = 40.0              # the game's clock, in ticks per second

# How close two readings of where a bag is have to be to be the same bag. The
# two sources are a remembered position and a live one, so they differ by
# however far it settled after the cell that holds it was last written.
SAME_BAG = 4.0            # metres

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

# The same idea underground: a floor is peppered with two hundred single-chunk
# rock pockets to break the tunnels up, and pinning each one would bury the
# deposits worth walking to.
_UG_FILLER = "Rock Pocket"

# Underground tile names, first match winning as above. Everything placed in an
# underground world is underground content, so unlike the overworld these are
# not gated on the biome the tile was filed under -- the boss arena is filed as
# bosstrain and is still the bottom of the lift.
_UG_KINDS = (
    ("mininghub_entranceelevator", "Mining Hub Lift"),
    ("mininghub_entrance",         "Mining Hub Entrance"),
    ("mininghub",                  "Mining Hub"),
    ("undergroundstation",         "Underground Station"),
    ("drillbottrainstation",       "Drillbot Train Station"),
    ("trashbot",                   "Trashbot Arena"),
    ("scrapyard",                  "Scrapyard"),
    ("elevator",                   "Elevator"),
    ("midblocker",                 "Blocked Passage"),
    ("cave",                       "Cave"),
    ("golddeposit",                "Gold Deposit"),
    ("corraliumdeposit",           "Coralium Deposit"),
    ("mineral4deposit",            "Mineral Deposit"),
    ("t3deposit",                  "Tier 3 Deposit"),
    ("t4deposit",                  "Tier 4 Deposit"),
    ("gigagem",                    "Gigagem"),
    ("sapphirepillars",            "Sapphire Pillars"),
    ("crystaltrees",               "Crystal Trees"),
    ("potatohole",                 "Potato Hole"),
    ("potatonimbolium",            "Potato Hole"),
    ("minerbot",                   "Minerbot Camp"),
    ("cablebots",                  "Cablebot Nest"),
    ("dungeon",                    "Dungeon"),
    ("reward",                     "Reward Chamber"),
    ("_ds_",                       "Drill Spawn"),
    ("passage",                    "Passage"),
    ("coralium",                   "Coralium Chamber"),
    ("tunnelpocket",               "Chamber"),
)

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


_SIZE = re.compile(r"^\d+(x\d+)+$")


def _ug_classify(tile):
    """(kind, fragment) for a tile placed in an underground world."""
    low = tile.name.lower()
    for frag, label in _UG_KINDS:
        if frag in low:
            return label, frag.strip("_")
    # A pocket that says nothing else about itself is one of the filler rocks.
    if "pocket" in low:
        return _UG_FILLER, "pocket"
    return _pretty(tile.name), low


# Words that describe how a tile is used rather than which one it is. The size
# in chunks is on the end of every underground tile name and says nothing a
# reader wants either.
_UG_NOISE = ("poi", "tunnelpocket", "pocket", "gameplay", "entrance",
             "overworld", "exterior")


def _ug_variant(tile, frag):
    """The distinguishing half of an underground tile name.

    Underground tiles are named in lower case with the size in chunks on the
    end -- `drill1_cave_3a_bot_8x8x3` -- so the size goes, whatever the kind
    already said goes, and what is left is capitalised to read as a name.
    """
    rest = []
    for p in tile.name.split("_"):
        low = p.lower()
        if p.isdigit() or _SIZE.match(low) or p.upper() == "NEW":
            continue
        # Either way round: a one-word fragment is part of a longer token, and
        # a fragment spanning two words accounts for each of them.
        if low in frag or frag in low or low in _UG_NOISE:
            continue
        rest.append(p[:1].upper() + p[1:])
    return " ".join(rest)


def underground(floor, renderer):
    """Every place on one underground floor, as plain dicts.

    Built from the layings themselves rather than from the cell grid, because a
    cell underground holds a column: two pockets in the same cell at different
    heights are two places, and a cave tile spread over four cells is one.
    """
    from .savefile import CHUNK_SIZE, CHUNKS_PER_CELL

    out = []
    by_tile = {}
    for p in floor.pieces:
        by_tile.setdefault(p.tile, []).append(p)

    for tile, pieces in by_tile.items():
        kind, frag = _ug_classify(tile)
        what = _ug_variant(tile, frag)
        for group in _touching(pieces):
            x0 = min(p.dx for p in group)
            x1 = max(p.dx + p.dw for p in group)
            y0 = min(p.dy for p in group)
            y1 = max(p.dy + p.dh for p in group)
            z = min(p.z0 for p in group)
            cells = sum(p.dw * p.dh for p in group) / float(
                CHUNKS_PER_CELL * CHUNKS_PER_CELL)
            ci = (x0 + x1) * 0.5 / CHUNKS_PER_CELL - 0.5
            cj = (y0 + y1) * 0.5 / CHUNKS_PER_CELL - 0.5
            # A cave or a pocket is placed at a height, and that height is what
            # it stands at. A floor tile is always placed at the bottom and
            # carries its own relief, so for those the ground has to be read.
            floor_tile = all(p.kind == "floor" for p in group)
            h = _height_at(renderer, ci, cj) if floor_tile else z * CHUNK_SIZE
            out.append({
                "kind": kind,
                "what": what,
                "tile": tile.name,
                # Rounded up, so a single-chunk pocket still counts as a place
                # rather than as a sixteenth of one.
                "cells": max(1, int(round(cells))),
                "cx": round(renderer.x0 + ci, 1),
                "cy": round(renderer.y0 + cj, 1),
                "h": round(h, 1),
            })

    out.sort(key=lambda p: (-p["cells"], p["kind"], p["what"]))
    return out


def _touching(pieces):
    """Group layings of one tile that are part of the same thing.

    Two layings belong together when they sit at the same height and their
    chunk footprints meet: that is a cave tile spread over the four cells it
    covers. Two pockets of the same kind in different corners of a floor do
    not, and neither does one stacked above another.
    """
    left = list(pieces)
    out = []
    while left:
        group = [left.pop()]
        moved = True
        while moved:
            moved = False
            for i in range(len(left) - 1, -1, -1):
                p = left[i]
                if any(_meets(p, q) for q in group):
                    group.append(left.pop(i))
                    moved = True
        out.append(group)
    return out


def _meets(a, b):
    return (a.z0 == b.z0
            and a.dx <= b.dx + b.dw and b.dx <= a.dx + a.dw
            and a.dy <= b.dy + b.dh and b.dy <= a.dy + a.dh)


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


def marks(save, world_id, tick=0, bags=()):
    """Everything the save itself remembers about where things happened.

    Positions come out of the game's own managers, so they are exact rather than
    read off a tile: the bed is the bed, and a death marker is the spot you went
    down on. Anything filed under another world -- another floor, a warehouse --
    is left for that world's own map.

    ``bags`` is where the lost-items bags are actually standing, which
    creations.bags reads off the bodies. It is passed in rather than looked up
    because finding it means decoding every body in the world, and that has
    already been done by the time this is called.
    """
    from .savefile import GLOBAL_WORLD

    got = {}
    for scope in (GLOBAL_WORLD, world_id):
        for channel, values in save.storage(scope, _CHANNELS).items():
            got.setdefault(channel, []).extend(values)

    out = []
    for value in got.get(CH_SPAWNERS, ()):
        for node in _rows(value, "all"):
            if "PLAYER_SPAWN" in _tags(node):
                _add(out, node, "Spawn point", "where the world starts you")
    # Two different things both mean "where you wake up", and calling both of
    # them a bed is what makes one look like the other. A permanent bed came
    # with the world -- the one at the Mechanic Station you are given at the
    # start -- and is where you respawn without your having put it anywhere. A
    # bed on the other channel is one you laid down and slept in.
    for value in got.get(CH_BEDS, ()):
        for bed in _rows(value):
            _add(out, bed, "Bed", "the one you slept in", world_id)
    for value in got.get(CH_PERMANENT_BEDS, ()):
        for bed in _rows(value):
            _add(out, bed, "Respawn bed", _named(bed.get("location")), world_id)
    # The unit manager's death markers are not yours. Every one of them is
    # written by a bot's own script as it dies -- haybot, farmbot, cablebot,
    # each of the totebots -- and the game keeps the last hundred so its AI
    # knows where the fighting has been. So this is where you killed things,
    # which is a fair map of where you have been, but it is not a tally of how
    # often you died. Where you died is the bag below, and the two are named
    # apart because a hundred of these around a battle read exactly like a
    # hundred of yours if they are not.
    for value in got.get(CH_UNITS, ()):
        for death in _rows(value, "deathMarkers"):
            stamp = death.get("timeStamp")
            # Newest first, so the last thing that happened is the first thing
            # the list says.
            _add(out, death, "Robot died here", _ago(stamp, tick),
                 order=-(stamp if isinstance(stamp, (int, float)) else 0))
    # Where you died is the bag you dropped doing it, and it is the only thing
    # in the save that says so -- there is no tally of deaths anywhere in it.
    # Two things know where the bag is and neither on its own is enough:
    #
    #   the respawn manager's channel, which lists a bag only while its cell is
    #   not loaded, because LostItems unmarks the bag as its cell comes in and
    #   marks it again as the cell goes out -- so a bag lying where you were
    #   standing when you saved is not on the channel at all;
    #
    #   the bag standing in the world, which is there for as long as the bag is.
    #
    # Read both. They disagree by a fraction of a metre -- the channel remembers
    # where the bag was when its cell last went out, the body is where it is now
    # -- so one bag is one pin rather than two.
    down = [tuple(float(v) for v in at[:3]) for at in bags]
    for value in got.get(CH_BAGS, ()):
        for player in _rows(value, "players"):
            for bag in _rows(player):
                at = bag.get("position")
                where = bag.get("world")
                if at is None or len(at) < 3:
                    continue
                if isinstance(where, int) and where != world_id:
                    continue
                at = tuple(float(v) for v in at[:3])
                if not any(_apart(at, b) <= SAME_BAG for b in down):
                    down.append(at)
    for at in down:
        _add(out, {"position": at}, "You died here", "your bag is still there")
    for value in got.get(CH_BEACONS, ()):
        for beacon in _rows(value, "beacons"):
            _add(out, beacon, "Beacon", "", world_id)
    for value in got.get(CH_QUEST_ENTITIES, ()):
        for name, quest in _pairs(value):
            _add(out, quest, "Quest marker", _quest(name), world_id)
    for value in got.get(CH_ELEVATORS, ()):
        for _key, group in _pairs(value):
            for lift in _rows(group):
                _add(out, lift, "Lift down", _lift(_tags(lift)))

    out.sort(key=lambda row: (row[0]["kind"], row[1], row[0]["what"]))
    # The same bed is written twice, once per channel, and a world with two
    # players in it writes each of theirs; one bed in one place is one pin.
    seen, kept = set(), []
    for place, _order in out:
        key = (place["kind"], place["cx"], place["cy"], place["h"])
        if key not in seen:
            seen.add(key)
            kept.append(place)
    return kept


def _apart(a, b):
    """How far apart two positions are, in metres."""
    return sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5


def _rows(value, key=None):
    """The records of one storage channel, however it happens to be keyed."""
    if key is not None:
        value = value.get(key) if isinstance(value, dict) else None
    if not isinstance(value, dict):
        return []
    return [v for v in value.values() if isinstance(v, dict)]


def _pairs(value):
    if not isinstance(value, dict):
        return []
    return [(k, v) for k, v in value.items() if isinstance(v, dict)]


def _tags(node):
    tags = node.get("tags")
    return list(tags.values()) if isinstance(tags, dict) else []


def _add(out, node, kind, what, world_id=None, order=0):
    """One record, if it has a position and belongs to the world being drawn."""
    at = node.get("position")
    if at is None:
        at = node.get("pos")
    if at is None or len(at) < 3:
        return
    # Records that say which world they are in are filtered on it; the ones that
    # do not are already filed under a world of their own.
    where = node.get("world")
    if world_id is not None and isinstance(where, int) and where != world_id:
        return
    out.append(({
        "kind": kind,
        "what": what,
        "tile": "",
        "cells": 1,
        "cx": round(float(at[0]) / CELL - 0.5, 2),
        "cy": round(float(at[1]) / CELL - 0.5, 2),
        "h": round(float(at[2]), 1),
    }, order))


def _ago(stamp, tick):
    """How long before the save was written something happened."""
    if not isinstance(stamp, (int, float)) or not tick or stamp > tick:
        return ""
    hours = (tick - stamp) / TICKS / 3600.0
    if hours < 1:
        return "%d minutes before you saved" % max(1, int(hours * 60))
    return "%.1f hours before you saved" % hours


def _named(where):
    """`MechanicStation` -> `Mechanic Station`.

    The places the game names to itself run their words together, and the
    tile names it is read beside do not, so they are pulled apart here.
    """
    if not where:
        return ""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", _pretty(str(where)))


def _quest(name):
    """`quest_tutorial.marker_water` -> `Tutorial · marker water`."""
    name = str(name)
    if name.startswith("quest_"):
        name = name[6:]
    head, _dot, tail = name.partition(".")
    head = head.replace("_", " ").strip()
    tail = tail.replace("_", " ").strip()
    head = head[:1].upper() + head[1:]
    return "%s · %s" % (head, tail) if tail else head


# The lifts the game tags, which is how one is told from another.
_LIFTS = {"MAIN": "the main shaft", "MAIN_BOTTOM": "the main shaft, below",
          "MECHANICSTATION": "at the Mechanic Station",
          "EXCAVATION": "at the excavation site"}


def _lift(tags):
    """`UNDERGROUND_ELEVATOR_MECHANICSTATION` -> `at the Mechanic Station`."""
    for tag in tags:
        rest = str(tag).replace("UNDERGROUND_ELEVATOR", "").strip("_")
        if rest:
            return _LIFTS.get(rest, rest.title())
    return ""


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
