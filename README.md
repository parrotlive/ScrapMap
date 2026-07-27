# Scrap Mechanic map

Makes a top-down map of your Scrap Mechanic survival world.

**Double-click `ScrapMap.exe`.** A window opens with your worlds already in it,
the one you played last picked out. Press **Make map** and it renders and opens
in your browser. Nothing to install, nothing to find, nothing to configure.

![](docs/gui.png)

![](docs/example.png)

Everything the world generator placed is drawn where it stands — roads,
warehouses, silos, ruins, fences, rocks and trees, each lit and casting a shadow
so you can read a place at a glance:

![](docs/detail.png)

Points of interest are drawn from their own ground data rather than from the
coarse whole-tile grid, so a district arrives with its roads, yards and canals
rather than as a field with some pipes in it:

![](docs/poi.png)

## Why this exists

The usual way to get an overview map of a survival world is a chore: back up
your save, download a repo, paste a block of Lua into the game's
`terrain_overworld.lua`, overwrite `tile_database.lua`, launch the game, load the
world so it dumps a `cells.json`, copy that file into an `html/assets/json`
folder, then serve the page — and any tile nobody has screenshotted yet shows up
blank.

This tool reads the save file directly. Nothing is patched, the game never has to
run, and both the terrain and everything standing on it are drawn from the game's
own data, so there is no such thing as a missing tile.

|                        | usual map tool          | this                |
| ---------------------- | ----------------------- | ------------------- |
| Edit game files        | yes, two of them        | no                  |
| Launch the game        | yes, and load the world | no                  |
| Move files by hand     | yes (`cells.json`)      | no                  |
| Web server             | yes, or hand-inline it  | no, one HTML file   |
| Tiles without art      | drawn blank             | always drawn        |
| Steps for the user     | ~7                      | double-click, press one button |

## Requirements

Windows, and Scrap Mechanic installed. `ScrapMap.exe` carries its own Python,
numpy and Pillow, so there is nothing else to install.

To run it from the source instead, you need Python 3.8+ and you double-click
`Make map.bat`, which installs `numpy` and `Pillow` the first time if they are
missing. To build the executable yourself:

```
python build_exe.py
```

That makes a throwaway virtual environment under `build/`, fetches PyInstaller
into it and cuts a 28 MB `ScrapMap.exe` from it, which opens its window about
two seconds after you double-click it. Nothing is installed into the Python you
use, and the environment it is cut from holds only what the tool imports.

## The window

Every save on the PC, newest first, with the one you last played picked out.
Detail runs from quick (4 m per pixel) through normal to fine (1 m per pixel,
about a minute and a half for a full world); you can turn off the structures or
the shading, write a plain image instead of a page, and choose where it lands.
Pick several worlds and it does them one after another. It tells you roughly how
long it will take before you start, how far along it is while it runs, and the
same button stops it.

## Output

`<world>_map.html` beside `ScrapMap.exe` — a single self-contained file (the
image is embedded, so you can move it anywhere or send it to someone). Drag to
pan, scroll to zoom, `F` to fit, `1` for 100%. The readout shows cell and world
coordinates under the cursor, which is handy for `/teleport`.

Ground colours come from the game's own terrain textures, so grass, sand, dirt
and rock read the way they do in game. Relief is hillshaded from the terrain
heightmap. Water is drawn where the world actually has water and nowhere else —
the sea, every lake, the pond inside a hideout, the canals around the silo
district — with chemical baths and oil pools drawn as what they are, and a dry
pit like the excavation mine left dry however far below the sea it goes.

## Command line

Not required, but it's there — the executable is the same program, so
`ScrapMap.exe --list` works exactly like `python -m smmap --list`:

```
python -m smmap                  # newest survival world -> HTML, opens it
python -m smmap --gui            # the window
python -m smmap --list           # show every save it found
python -m smmap "My World"       # a world by name (substring is fine)
python -m smmap --all            # every survival world
python -m smmap --png            # PNG instead of HTML
python -m smmap --px 64          # 64 px per 64 m cell (one metre per pixel)
python -m smmap --no-structures  # bare terrain, still with its water
python -m smmap --no-shade       # flat colours, no hillshading
python -m smmap --game "D:\...\Scrap Mechanic"
```

Default is 32 px per cell — two metres per pixel, so a full world is 4608 x 3584
and takes about ten seconds.

## How it works

Everything below was recovered from the save format and the game's data files;
there is no third-party dependency for reading either.

**The save** (`.db`) is SQLite. Every BLOB shares one envelope — 16-byte uid,
`u16` key length, key, `u16` world id, `u8` flags, `u32` compressed size — around
a raw LZ4 block (`lz4.py`).

**The cell grid** is a `LUA` value blob in `ScriptData`, written by
`sm.terrainData.save()`. It is bit-packed rather than byte-aligned: an 8-bit tag
then a payload, where a table is `count` + one "is array" bit + a signed start
index (which is how the grid stores negative world coordinates), and a string
realigns to a byte boundary before its text. `smlua.py` decodes it into the
`uid` / `rotation` / `xOffset` / `yOffset` / `elevation` / `cliffLevel` grids for
every cell in the world. Tile UUIDs are stored byte-reversed.

**The terrain** comes from the game's `.tile` files (`tiles.py`). Each holds six
LZ4-compressed LOD levels; a level is `(S/2+1)²` float32 heights followed by `S²`
cells of eight `uint8` material weights, with S = 65 down to 3. That grid is a
fixed 65x65 per tile whatever the tile measures on the ground — one sample a
metre for a 64 m meadow, one every eight for a 512 m point of interest, and for
the Silo District not even that: its whole-tile grid is empty.

**The real ground for those** is one grid per 64 m cell, sitting base64'd and
LZ4'd in the `.tileson`: 33x33 float32 heights, the same again for the mask the
`.tile` keeps beside them, then 65x65x8 material weights stored plane by plane
rather than interleaved. For a one-cell tile it reproduces the `.tile`'s own
LOD 0 exactly, which is how the layout was pinned down; for a 512 m district it
is sixty-four times the detail, and it is where a point of interest keeps its
roads and its yards. Cells run x fastest — verified by the boundary samples
neighbouring cells share, 8744 of 8744 shared edges matching exactly.

**Colours** are the mean colours of the ground diffuse textures listed in
`Data/Terrain/Materials/gnd_standard_materialset.json`, blended by weight over
the "Grass" base the terrain falls back to where all eight weights are zero
(`palette.py`).

**The structures** are in the same `.tileson`: assets, harvestables and
kinematics with a position in tile metres, an Euler rotation, a scale and a
per-material colour map — and nested prefabs, which is where the bulk of a place
lives. A warehouse is one prefab, the ruined city is 124, and each `.prefab` has
a `.prefabson` of exactly the same shape, so `props.py` walks the tree and
flattens it into one list of world-space placements. It carries rotation and
scale as a matrix rather than as angles, because composing a child's rotation
with a parent's non-uniform scale does not in general give back a rotation and a
scale. Reading only the top level draws the fences and misses the buildings:
expanding the tree takes the ruined city from 1272 placements to 5232.

Each placement is looked up in the game's `.assetset` and `.harvestableset`
databases, which name it and point at a collision mesh in plain `.obj`
(`assets.py`). `detail.py` reduces that mesh to its extreme points, rotates and
scales them into world space, and fills the outline they project to — deepest
first, and only where the prop stands clear of the ground, so mine workings do
not print through the mountain above them. The colour is the map colour for its
kind — building, road, rock, plant, wreck — carrying 42% of the prop's own
paint, which keeps a town legible instead of noisy. Their heights drive a rim
light and a shadow, which is what makes a warehouse look like a warehouse from a
kilometre up.

**Water** is not a plane at sea level. There is no sea plane: the ocean, every
lake and every pond is a box some tile places, and its surface is the top of
that box — `waterLevel = height + scaleZ * 0.5` in the game's own
`terrain_util2.lua`. Ocean and lake tiles put a 64 x 64 box over each of their
cells with its top at −2 m; a point of interest up on a plateau carries its
canals at whatever height it stands. Such a box has no collision mesh and no
shape of its own, and the only thing marking it out is that its renderable draws
with a water material — seven assets do, from `water` through `oil` and
`chemicals` to `sewerwater`.

Drawing water wherever the ground falls below zero instead looks right at a
glance and is wrong everywhere: ordinary meadow, forest and field tiles ripple a
metre or so either side of zero, so a fifth of every one of them floods. One
world came out with 3905 separate bodies of water, 1627 of them four pixels or
smaller; going by the volumes it has 164, of which 31 are that small. It cuts
the other way too — the excavation mine descends 95 m below the sea and is bone
dry, because nothing ever put water in it.

A body of liquid stops at whatever comes up through it, which is what gives a
pond its shoreline and lets a pier, a canopy or a silo stand in the water rather
than under it. That test needs a prop's height, and a prop's height is a single
number for the whole of it — fine for a silo, wrong for the rock a lake is a
hollow in, which is a bowl forty metres across whose rim breaks the surface. Its
outline covers the lake, so its rim height drains the lake. Comparing the water
surface against the middle of a prop rather than its top tells a pier from a
basin. Ground lying under a water surface and drawn dry anyway falls from 2.0%
of the map to 1.0%, and what is left of that is the mine, the sunken forest
basins and the tree canopies that really do hang over the water.

Three conventions could not simply be read off the formats, so each was settled
by measurement rather than by guessing:

- **The rotation convention** for the tileson's Euler triples. The binary `.tile`
  records store the same placements as quaternions, so matching the two by
  position over 431 assets and searching the orders and signs gives `Rx·Ry·Rz`
  with a mean Frobenius error of 0.0000.
- **How a tile's sample block maps into the image** — flip, transpose and
  rotation direction. Rendering a whole world under all sixteen combinations and
  scoring colour continuity across cell seams: the one used scores 1.4, the next
  best 6.1, against an in-cell reference of 3.3. Coastlines and roads line up
  across cells because of it.
- **That the props share that frame.** Props stand on the ground, so the right
  mapping is the one that puts a tree's foot closest to the terrain beneath it:
  over 146 tiles with relief it is out by 3.9 m, every mirrored or transposed
  alternative by 6.3 m or more.

The cliff level, incidentally, is not a height. Added as one it steps the seams
by a metre where the raw elevation joins up to within 0.68 m, and draws a bright
and dark rectangle around every plateau in the world; averaged onto the cell
corners instead it ramps across the ring of cells where the game puts its cliff
tiles.

## Layout

```
ScrapMap.exe        the whole thing in one file (built by build_exe.py)
Make map.bat        double-click launcher, for running from the source
build_exe.py        packs it into the executable
smmap/
  app.py            where the packaged program starts
  __main__.py       command line
  gui.py            the window
  output.py         writes the page or the image, for all of the above
  discover.py       finds Steam, the game and your saves
  savefile.py       save database reader
  smlua.py          'LUA' bit-packed value decoder
  bitreader.py      MSB-first bit reader
  lz4.py            LZ4 block decompressor
  tiles.py          .tile index, LOD decoder, per-cell surface grids
  props.py          .tileson / .prefabson placements, prefab trees flattened
  assets.py         asset and harvestable databases, collision meshes
  detail.py         rasterises props and pools into a per-tile overlay
  palette.py        terrain and liquid colours
  render.py         map compositing
  viewer.py         self-contained HTML viewer
```

The viewer never hands the whole map to the browser as one transformed element:
sixteen megapixels of it would be a large thing to ask a compositor to scale on
every frame of a drag. Each frame copies only the rectangle that is on screen
into a viewport-sized canvas, which costs the same at any zoom and for any size
of world.

Saves are opened read-only and immutable; the tool never writes to them.
