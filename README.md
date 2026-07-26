# Scrap Mechanic map

Makes a top-down map of your Scrap Mechanic survival world.

**Double-click `Make map.bat`.** That's it. It finds the game, finds your saves,
picks the world you played last, renders it and opens it in your browser.

![](docs/example.png)

## Why this exists

The usual way to get an overview map of a survival world is a chore: back up
your save, download a repo, paste a block of Lua into the game's
`terrain_overworld.lua`, overwrite `tile_database.lua`, launch the game, load the
world so it dumps a `cells.json`, copy that file into an `html/assets/json`
folder, then serve the page — and any tile nobody has screenshotted yet shows up
blank.

This tool reads the save file directly. Nothing is patched, the game never has to
run, and terrain is drawn from the game's own `.tile` data, so there is no such
thing as a missing tile.

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

Colours come from the game's own ground textures, so grass, sand, dirt and rock
read the way they do in game. Anything below the water plane is drawn as water,
shaded by depth. Relief is hillshaded from the terrain heightmap.

## Command line

Not required, but it's there:

```
python -m smmap                  # newest survival world -> HTML, opens it
python -m smmap --list           # show every save it found
python -m smmap "cock"           # a world by name (substring is fine)
python -m smmap --all            # every survival world
python -m smmap --png            # PNG instead of HTML
python -m smmap --px 32          # 32 px per 64 m cell (bigger, slower)
python -m smmap --no-shade       # flat colours, no hillshading
python -m smmap --game "D:\...\Scrap Mechanic"
```

Default is 16 px per cell, so a full world is 2304 x 1792 and takes about four
seconds.

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
cells of eight `uint8` material weights, with S = 65 down to 3. The grid is a
fixed 65x65 per tile whatever its size, which is about right for a map.

**Colours** are the mean colours of the ground diffuse textures listed in
`Data/Terrain/Materials/gnd_standard_materialset.json`, blended by weight over
the "Grass" base the terrain falls back to where all eight weights are zero
(`palette.py`).

The one convention that could not be read off the format is how a tile's sample
block maps into the image — flip, transpose and rotation direction. It was
settled by rendering a whole world under all sixteen combinations and scoring
colour continuity across cell seams: the one used scores 1.4, the next best 6.1,
against an in-cell reference of 3.3. Coastlines and roads line up across cells
because of it.

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
  tiles.py          .tile index and LOD decoder
  palette.py        terrain colours
  render.py         map compositing
  viewer.py         self-contained HTML viewer
```

Saves are opened read-only and immutable; the tool never writes to them.
