# Changelog

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
