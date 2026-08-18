# Changelog

## 2.2

**What the save holds.** A map used to be the world as the generator made it.
It carries what is standing in it now as well: every creation and every
structure, block by block, in the colours they were painted, along with the
beds, the beacons and the quest markers. All of it comes out of the save's own
bodies, so it is where the game put it rather than where a tile says something
should be.

**Yours, kept apart from the world's.** The places legend has a heading for
each, so ticking Yours on its own gives you what you built and nothing else; and
the solid view draws your vehicles, your builds, your loose creations and what
you welded down as four separate things that come and go one at a time.

**The value reader is finished.** Positions, rotations, colours and the game's
object handles were tags it did not know, so any part of a save holding one was
skipped. Widths measured against 4,824 real blobs from six saves.

Fixed:

- Where you died went missing. The only record of it in a save is the bag you
  dropped, and the game's respawn manager takes a bag off its list the moment
  the cell holding it loads. The bag itself is read out of the world instead,
  which is there for as long as the bag is.
- A death of yours and a robot's read alike in the list. They are named apart
  now: the hundred the game remembers are robots, and yours says so.

## 2.1

**Places.** Both maps now name what is in your world — warehouses, the silo
district, the ruined city, the stations, the minidungeon entrances, every
builder quest — read from the tiles themselves rather than from a hand-written
list. Tick kinds off in the legend, search them, click one to go there, tick it
when you have been.

**Spoilers off by default.** A map opens showing where places are, not what
they are. `N` names them.

**The page got out of the way.** Five panels became one bar with three tabs,
all closed to start; `H` hides even that.

**Performance mode.** When the view cannot keep up it gives back detail until
it can, and takes it back when the view is still.

**Linux.** `./make-map.sh`. Saves are found inside the game's Proton prefix,
across every Steam library, and paths are matched case-insensitively where the
filesystem cares.

Fixed:

- Dragging up and down panned the wrong way.
- Modded worlds came out purple: only the game's own folders were searched, so
  a world built on a terrain mod had no tiles it could find. Workshop items and
  local mods are read too.
- `--no-objects` wrote pages with object controls that did nothing.
- Typing in the search box drove the camera.

## 2.0

The world in three dimensions: real height, real water, and every object the
generator placed as its own geometry at its own place, size and angle — 99.95%
of the 2.74 million things a world stands up.

Fixed a rounding fault in the dry-water marker that had quantised every world
flat, collision meshes in FBX that were being dropped, and detail settings that
never reached the ground.
