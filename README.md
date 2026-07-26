# Scrap Mechanic map

Makes a top-down map of your Scrap Mechanic survival world.

**Double-click `Make map.bat`.** That's it. It finds the game, finds your saves,
picks the world you played last, renders it and opens it in your browser.

![](docs/example.png)

Everything the world generator placed is drawn where it stands — roads,
warehouses, silos, ruins, fences, rocks and trees, each lit and casting a shadow
so you can read a place at a glance:

![](docs/detail.png)

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
| Steps for the user     | ~7                      | 1 (double-click)    |

## Requirements

Windows with Scrap Mechanic installed, and Python 3.8+. The launcher installs
`numpy` and `Pillow` on first run if they are missing.

## Output

`<world>_map.html` next to the tool — a single self-contained file (the image is
embedded, so you can move it anywhere or send it to someone). Drag to pan, scroll
to zoom, `F` to fit, `1` for 100%. The readout shows cell and world coordinates
under the cursor, which is handy for `/teleport`.

Ground colours come from the game's own terrain textures, so grass, sand, dirt
and rock read the way they do in game. Anything below the water plane is drawn as
water, shaded by depth, and relief is hillshaded from the terrain heightmap.

## Command line

Not required, but it's there:

```
python -m smmap                  # newest survival world -> HTML, opens it
python -m smmap --list           # show every save it found
python -m smmap "cock"           # a world by name (substring is fine)
python -m smmap --all            # every survival world
python -m smmap --png            # PNG instead of HTML
python -m smmap --px 64          # 64 px per 64 m cell (one metre per pixel)
python -m smmap --no-structures  # bare terrain
python -m smmap --no-shade       # flat colours, no hillshading
python -m smmap --game "D:\...\Scrap Mechanic"
```

Default is 32 px per cell — two metres per pixel, so a full world is 4608 x 3584
and takes about seven seconds.

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
fixed 65x65 per tile whatever the tile measures on the ground, so a 64 m meadow
is sampled every metre but a 512 m point of interest only every eight — which is
why a map drawn from the height field alone shows an empty field where a factory
stands.

**Colours** are the mean colours of the ground diffuse textures listed in
`Data/Terrain/Materials/gnd_standard_materialset.json`, blended by weight over
the "Grass" base the terrain falls back to where all eight weights are zero
(`palette.py`).

**The structures** are in the `.tileson` beside each `.tile`: a list of assets,
harvestables and kinematics with a position in tile metres, an Euler rotation, a
scale and a per-material colour map. Each one is looked up in the game's
`.assetset` and `.harvestableset` databases, which name it and point at a
collision mesh in plain `.obj` (`assets.py`). `detail.py` reduces that mesh to
its extreme points, rotates and scales them into world space, and fills the
outline they project to — deepest first, and only where the prop stands clear of
the ground, so mine workings do not print through the mountain above them. The
colour is the map colour for its kind — building, road, rock, plant, wreck —
carrying 42% of the prop's own paint, which keeps a town legible instead of
noisy. Their heights drive a rim light and a shadow, which is what makes a
warehouse look like a warehouse from a kilometre up.

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
Make map.bat        double-click launcher
smmap/
  __main__.py       CLI, discovery, output
  discover.py       finds Steam, the game and your saves
  savefile.py       save database reader
  smlua.py          'LUA' bit-packed value decoder
  bitreader.py      MSB-first bit reader
  lz4.py            LZ4 block decompressor
  tiles.py          .tile index, LOD decoder and .tileson prop lists
  assets.py         asset and harvestable databases, collision meshes
  detail.py         rasterises props into a per-tile overlay
  palette.py        terrain colours
  render.py         map compositing
  viewer.py         self-contained HTML viewer
```

Saves are opened read-only and immutable; the tool never writes to them.
