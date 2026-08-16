"""Write a single self-contained HTML file with a pan/zoom map viewer.

The image is inlined as a data URI so the page works straight off the disk with
no web server, no assets folder and nothing to copy around.
"""

import base64
import html
import io
import json

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>%(title)s</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  /* Any rule below that sets a display beats the browser's own [hidden], and
     the places panel does. Say it once, here, and hidden means hidden. */
  [hidden] { display:none !important; }
  /* Black, one grey, one red, and no rounded corners anywhere. The chrome is
     monospace and set in capitals so it reads as instrumentation over the map
     rather than as a second thing competing with it for attention. */
  :root {
    --bg:#000; --panel:#161614; --line:#2a2a28; --line2:#3a3a37;
    --fg:#fff; --dim:#8f8f8f; --red:#f00;
    --mono:ui-monospace,"Cascadia Mono","Segoe UI Mono",Consolas,monospace;
  }
  html, body { margin:0; height:100%%; background:var(--bg); color:var(--fg);
       font:12px/1.45 var(--mono); overflow:hidden; }
  /* touch-action keeps the browser from claiming the gesture and cancelling our
     pointer stream halfway through a drag. */
  #stage { position:absolute; inset:0; cursor:crosshair; overflow:hidden;
       touch-action:none; user-select:none; -webkit-user-select:none; }
  #stage.drag { cursor:grabbing; }
  #view { position:absolute; left:0; top:0; }
  #src { display:none; }
  #wait { position:absolute; left:50%%; top:50%%; transform:translate(-50%%,-50%%);
       color:var(--dim); font-size:11px; letter-spacing:.14em;
       text-transform:uppercase; }

  /* ---- the bar: the only thing that is always on screen ---- */
  #bar { position:absolute; top:0; left:0; right:0; height:30px; z-index:3;
       display:flex; align-items:stretch; background:var(--bg);
       border-bottom:1px solid var(--line); font-size:11px; }
  #bar .name { display:flex; align-items:center; padding:0 14px; color:var(--fg);
       letter-spacing:.1em; text-transform:uppercase; white-space:nowrap;
       overflow:hidden; text-overflow:ellipsis; max-width:34vw;
       border-right:1px solid var(--line); }
  #bar button { appearance:none; background:none; border:0;
       border-right:1px solid var(--line); color:var(--dim); font:inherit;
       letter-spacing:.1em; text-transform:uppercase; padding:0 14px;
       cursor:pointer; }
  #bar button:hover { color:var(--fg); background:var(--panel); }
  #bar button.on { color:var(--red); background:var(--panel); }
  #bar .gap { flex:1; }
  #bar .read { display:flex; align-items:center; gap:14px; padding:0 14px;
       color:var(--dim); font-variant-numeric:tabular-nums; white-space:nowrap;
       text-transform:uppercase; letter-spacing:.1em;
       border-left:1px solid var(--line); }
  #bar .read b { color:var(--fg); font-weight:400; }
  #show { position:absolute; top:0; left:0; z-index:5; background:var(--bg);
       border:0; border-right:1px solid var(--line);
       border-bottom:1px solid var(--line); color:var(--dim); font:inherit;
       font-size:11px; padding:7px 12px; cursor:pointer; letter-spacing:.1em;
       text-transform:uppercase; }
  #show:hover { color:var(--fg); }
  body.bare #bar, body.bare .panel, body.bare #help { display:none; }
  body.bare #markers { display:none; }

  /* ---- panels: one at a time, hung under the bar ---- */
  .panel { position:absolute; top:30px; left:0; width:296px; z-index:3;
       background:var(--panel); border-right:1px solid var(--line);
       border-bottom:1px solid var(--line);
       max-height:calc(100%% - 58px); display:flex; flex-direction:column; }
  .panel h2 { margin:0; padding:9px 14px 7px; font-size:10px; font-weight:400;
       letter-spacing:.14em; text-transform:uppercase; color:var(--dim);
       border-bottom:1px solid var(--line); display:flex;
       justify-content:space-between; gap:10px; }
  .panel h2 em { font-style:normal; color:var(--fg); }
  .pad { padding:10px 14px; overflow:auto; }
  /* The places panel is the one with a list in it, so it is given a height to
     fill rather than left to hug three rows of content. */
  #places { height:min(620px, calc(100%% - 58px)); }

  .sub { margin:0 0 10px; color:var(--dim); font-size:10px; letter-spacing:.06em;
       text-transform:uppercase; }
  #stats { display:grid; grid-template-columns:auto 1fr; gap:3px 12px;
       font-size:11px; }
  #stats span { color:var(--dim); text-transform:uppercase; letter-spacing:.06em; }
  #stats b { font-weight:400; text-align:right;
       font-variant-numeric:tabular-nums; }

  /* The colour key lives with the rest of the world's facts rather than in a
     panel of its own. */
  #legend { margin-top:12px; border-top:1px solid var(--line); padding-top:10px; }
  #legend div { display:flex; align-items:center; gap:9px; padding:2px 0;
       font-size:11px; color:var(--dim); text-transform:uppercase;
       letter-spacing:.04em; }
  #legend i { width:7px; height:7px; display:inline-block; flex:none; }

  #find { display:block; width:100%%; padding:7px 14px; background:var(--bg);
       color:var(--fg); border:0; border-bottom:1px solid var(--line);
       border-radius:0; font:inherit; font-size:11px; letter-spacing:.06em; }
  #find::placeholder { color:#5c5c58; text-transform:uppercase; }
  #find:focus { outline:none; }
  /* Ticking twenty-five kinds one at a time is what these are for. */
  .pick { display:flex; border-bottom:1px solid var(--line); }
  .pick button { flex:1; appearance:none; background:none; border:0;
       border-right:1px solid var(--line); color:var(--dim); font:inherit;
       font-size:10px; letter-spacing:.1em; text-transform:uppercase;
       padding:6px 0; cursor:pointer; }
  .pick button:last-child { border-right:0; }
  .pick button:hover { color:var(--fg); background:var(--bg); }
  #kinds { flex:0 1 auto; overflow:auto; min-height:56px; max-height:176px;
       padding:8px 14px; }
  #list { flex:1 1 74px; overflow:auto; min-height:74px; padding:8px 14px;
       border-top:1px solid var(--line); }
  #list .none { color:var(--dim); font-size:10px; padding:2px 0;
       text-transform:uppercase; letter-spacing:.06em; }
  .krow, .prow { display:flex; align-items:center; gap:9px; padding:3px 0;
       font-size:11px; cursor:pointer; }
  .krow input, .prow input { accent-color:var(--red); margin:0; flex:none; }
  /* Square, because nothing here is round. */
  .dot { width:7px; height:7px; flex:none; }
  .krow { color:var(--dim); text-transform:uppercase; letter-spacing:.04em; }
  .krow:hover { color:var(--fg); }
  .krow .n { margin-left:auto; color:#5c5c58;
       font-variant-numeric:tabular-nums; }
  .prow { color:var(--dim); }
  .prow:hover { color:var(--fg); }
  .prow .nm { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .prow.done .nm { text-decoration:line-through; color:#5c5c58; }
  .prow .d { margin-left:auto; color:#5c5c58; font-size:10px; flex:none;
       font-variant-numeric:tabular-nums; }

  /* Pins ride over the map as ordinary elements, so they are real text and
     click without the canvas having to hit-test anything. */
  #markers { position:absolute; inset:0; pointer-events:none; overflow:hidden; }
  .mk { position:absolute; transform:translate(-50%%,-50%%); pointer-events:auto;
       cursor:pointer; white-space:nowrap; font-size:10px; line-height:1;
       letter-spacing:.06em; text-transform:uppercase; padding:4px 7px;
       background:var(--bg); border:1px solid var(--line2); color:var(--fg);
       display:flex; align-items:center; gap:6px; }
  .mk:hover { border-color:var(--red); z-index:2; }
  .mk.done { color:#5c5c58; border-color:var(--line); }
  .mk i { font-style:normal; }
  /* Spoiler-free: the pin still says a place is here and still centres on it,
     it just does not say what it is. */
  .mk.mute { padding:4px; }
  .mk.mute i { display:none; }

  #help { position:absolute; bottom:0; left:0; right:0; padding:7px 14px;
       color:#5c5c58; font-size:10px; letter-spacing:.06em; z-index:3;
       text-transform:uppercase; border-top:1px solid var(--line);
       background:var(--bg); }
  #help b { color:var(--dim); font-weight:400; }
</style>

<div id="stage"><canvas id="view"></canvas><div id="markers"></div>
  <div id="wait">unpacking the map&hellip;</div>
  <img id="src" src="%(img)s" width="%(w)d" height="%(h)d"
       alt="world map" draggable="false"></div>

<button id="show" hidden>&plus; Show</button>

<div id="bar">
  <span class="name">%(name)s</span>
  <button data-panel="info">World</button>
  <button data-panel="places" id="placesTab" hidden>Places</button>
  <button id="names" hidden
          title="Show what each place is called (N). Off, the map says where things are without telling you what they are.">Names</button>
  <button id="hide" title="Hide everything (H)">Hide</button>
  <span class="gap"></span>
  <span class="read">cell <b id="cell">-</b> world <b id="world">-</b>
    <b id="zoom">-</b></span>
</div>

<div class="panel" id="info" hidden>
  <h2>World</h2>
  <div class="pad">
    <p class="sub">%(subtitle)s</p>
    <div id="stats">%(stats)s</div>
    <div id="legend">%(legend)s</div>
  </div>
</div>

<div class="panel" id="places" hidden>
  <h2>Places <em id="placeCount"></em></h2>
  <input id="find" type="search" placeholder="Search places"
         autocomplete="off" spellcheck="false">
  <div class="pick">
    <button data-pick="all">All</button>
    <button data-pick="none">None</button>
    <button data-pick="invert">Invert</button>
  </div>
  <div id="kinds"></div>
  <div id="list"></div>
</div>

<div id="help">
  drag pan &middot; scroll or double-click zoom &middot;
  <b>F</b> fit &middot; <b>1</b> 100%% &middot; <b>N</b> names &middot;
  <b>H</b> hide
</div>

<script>
const META = %(meta)s;
/* Every place the generator laid down, in cell coordinates. Empty when the
   render had no tile map to read them off. */
const PLACES = %(places)s;
/* What the ticked-off places are remembered under: the world's name rather
   than the file's, so moving or renaming the page keeps them. */
const WORLD_KEY = %(world_key)s;
const BG = '#11151c';
const stage = document.getElementById('stage');
const view = document.getElementById('view'), src = document.getElementById('src');
const ctx = view.getContext('2d', { alpha: false });
const cellOut = document.getElementById('cell'), worldOut = document.getElementById('world');
const zoomOut = document.getElementById('zoom');
let scale = 1, tx = 0, ty = 0, ready = false, mip = null, mipK = 1;

/* --------------------------------------------------------------- the chrome */
/* The map is the thing worth looking at, so everything else starts put away
   and only one panel is ever open at a time. Hide takes even the bar off and
   leaves a single way back. */

const bar = document.getElementById('bar');
const showBtn = document.getElementById('show');
const tabs = [...bar.querySelectorAll('button[data-panel]')];
const panels = {
  info: document.getElementById('info'),
  places: document.getElementById('places')
};
let openPanel = null;

function openOnly(which) {
  openPanel = openPanel === which ? null : which;
  for (const name of Object.keys(panels)) panels[name].hidden = name !== openPanel;
  for (const b of tabs) b.classList.toggle('on', b.dataset.panel === openPanel);
}
for (const b of tabs) b.addEventListener('click', () => openOnly(b.dataset.panel));

function setBare(on) {
  document.body.classList.toggle('bare', on);
  showBtn.hidden = !on;
}
document.getElementById('hide').addEventListener('click', () => setBare(true));
showBtn.addEventListener('click', () => setBare(false));

/* A key pressed into the search box is a letter being typed, not a command:
   without this, searching for a farm fits the map to the window instead. */
function typing(e) {
  const t = e.target;
  return !!t && (t.tagName === 'INPUT' || t.tagName === 'SELECT'
                 || t.tagName === 'TEXTAREA' || t.isContentEditable);
}

/* Viewport coordinates, which is what every gesture below works in. */
function at(e) {
  const r = stage.getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
}

/* The map is far larger than any window -- nine kilometres at two metres a
   pixel is sixteen megapixels -- so it is never handed to the browser as one
   transformed element. Each frame copies just the rectangle of it that is on
   screen, which costs the same whether the world is small or huge and whatever
   the zoom. */
function draw() {
  const w = stage.clientWidth, h = stage.clientHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cw = Math.round(w * dpr), ch = Math.round(h * dpr);
  if (view.width !== cw || view.height !== ch) {
    view.width = cw; view.height = ch;
    view.style.width = w + 'px'; view.style.height = h + 'px';
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, w, h);
  updateMarkers();
  if (!ready) return;
  const x0 = Math.max(0, -tx / scale), y0 = Math.max(0, -ty / scale);
  const x1 = Math.min(META.w, (w - tx) / scale), y1 = Math.min(META.h, (h - ty) / scale);
  if (x1 <= x0 || y1 <= y0) return;
  /* Zoomed out, sample the reduced copy: shrinking sixteen megapixels every
     frame is the one thing here that is not cheap. */
  const small = mip && scale <= mipK;
  const img = small ? mip : src, k = small ? mipK : 1;
  ctx.imageSmoothingEnabled = scale < 1;
  ctx.drawImage(img, x0 * k, y0 * k, (x1 - x0) * k, (y1 - y0) * k,
                tx + x0 * scale, ty + y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale);
}

/* Never draw more than once per frame however many pointer events arrive. */
let queued = false;
function paint() {
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => { queued = false; draw(); });
}

/* Always leave some of the map on screen: a pan that loses it entirely reads as
   a broken viewer rather than as a pan. */
function apply() {
  const w = META.w * scale, h = META.h * scale;
  const kx = Math.min(stage.clientWidth, w) * 0.35;
  const ky = Math.min(stage.clientHeight, h) * 0.35;
  tx = Math.min(stage.clientWidth - kx, Math.max(kx - w, tx));
  ty = Math.min(stage.clientHeight - ky, Math.max(ky - h, ty));
  zoomOut.textContent = Math.round(scale * 100) + '%%';
  paint();
}
function fit() {
  scale = Math.min(stage.clientWidth / META.w, stage.clientHeight / META.h) * 0.94;
  tx = (stage.clientWidth - META.w * scale) / 2;
  ty = (stage.clientHeight - META.h * scale) / 2;
  apply();
}
function zoomAt(cx, cy, factor) {
  const ns = Math.min(32, Math.max(0.02, scale * factor));
  tx = cx - (cx - tx) * (ns / scale);
  ty = cy - (cy - ty) * (ns / scale);
  scale = ns;
  apply();
}
function readout(x, y) {
  const mx = (x - tx) / scale, my = (y - ty) / scale;
  const inside = mx >= 0 && my >= 0 && mx < META.w && my < META.h;
  cellOut.textContent = inside
    ? `${Math.floor(mx / META.px) + META.x0}, ${META.y1 - Math.floor(my / META.px)}` : '-';
  worldOut.textContent = inside
    ? `${Math.round((mx / META.px + META.x0) * 64)}, ${Math.round((META.y1 + 1 - my / META.px) * 64)}` : '-';
}

stage.addEventListener('wheel', e => {
  e.preventDefault();
  /* Wheels report pixels, lines or pages; a smooth exponential of the
     normalised delta zooms a trackpad gently and a mouse wheel briskly. */
  const step = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
  const [x, y] = at(e);
  zoomAt(x, y, Math.pow(0.9985, e.deltaY * step));
}, { passive: false });

let dragId = null, grabX = 0, grabY = 0;
stage.addEventListener('pointerdown', e => {
  if (e.pointerType === 'mouse' && e.button !== 0) return;
  e.preventDefault();
  dragId = e.pointerId;
  grabX = e.clientX - tx;
  grabY = e.clientY - ty;
  stage.classList.add('drag');
  stage.setPointerCapture(e.pointerId);
});
stage.addEventListener('pointermove', e => {
  const [x, y] = at(e);
  if (e.pointerId === dragId) {
    tx = e.clientX - grabX;
    ty = e.clientY - grabY;
    apply();
  }
  readout(x, y);
});
function release(e) {
  if (e.pointerId !== dragId) return;
  dragId = null;
  stage.classList.remove('drag');
  if (stage.hasPointerCapture(e.pointerId)) stage.releasePointerCapture(e.pointerId);
}
stage.addEventListener('pointerup', release);
stage.addEventListener('pointercancel', release);
stage.addEventListener('dblclick', e => { const [x, y] = at(e); zoomAt(x, y, 2); });
stage.addEventListener('dragstart', e => e.preventDefault());
addEventListener('resize', apply);

addEventListener('keydown', e => {
  if (typing(e)) return;
  const k = e.key.toLowerCase();
  if (k === 'h') { setBare(!document.body.classList.contains('bare')); return; }
  if (k === 'n' && !namesBtn.hidden) { setNames(!namesOn); return; }
  if (k === 'f') fit();
  if (k === '1') zoomAt(stage.clientWidth / 2, stage.clientHeight / 2, 1 / scale);
});

/* -------------------------------------------------------------- the places */
/* The landmarks the generator laid down, read off the tile under each cell.
   The pins are ordinary elements laid over the canvas and moved whenever the
   map moves, so they are real text and they click without the canvas having to
   hit-test anything. */

const MAX_MARKERS = 26;
const LIST_CAP = 250;
/* The pins are monospace, so a pin's width on screen follows from the number of
   characters in it -- which is what lets one be kept clear of another without
   measuring anything the browser would have to lay out. */
const CHAR_W = 6.7, LABEL_PAD = 27, LABEL_H = 19;
/* The generator's filler variants outnumber the real landmarks several times
   over, so the legend starts with them off. */
const OFF_BY_DEFAULT = ['Random Site'];

const markersBox = document.getElementById('markers');
const kindsBox = document.getElementById('kinds');
const listBox = document.getElementById('list');
const findBox = document.getElementById('find');
const placeCount = document.getElementById('placeCount');

const kinds = new Map();
const found = new Set();
let shown = [];
/* A map of a world you have not played yet should not hand you the answers.
   Off, the pins say a place is there and will still centre on it; they do not
   say what it is, and the list gives coordinates rather than names. */
let namesOn = false;

const STORE = 'scrapmap:' + WORLD_KEY;
try {
  for (const id of JSON.parse(localStorage.getItem(STORE) || '[]')) found.add(id);
} catch (e) { /* storage refused is not a reason to fail */ }
function remember() {
  try { localStorage.setItem(STORE, JSON.stringify([...found])); } catch (e) {}
}

const idOf = p => p.kind + '|' + p.cx + ',' + p.cy;
const titleOf = p => p.what ? p.kind + ' · ' + p.what : p.kind;

/* A stable colour per kind, the same one the solid view gives it. */
function kindColour(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return 'hsl(' + (h %% 360) + ' 60%% 63%%)';
}

/* Cell coordinates back to a pixel in the map image: the inverse of what the
   readout does going the other way. */
function mapXY(p) {
  return [(p.cx - META.x0 + 0.5) * META.px, (META.y1 - p.cy + 0.5) * META.px];
}

function buildKinds() {
  const tally = new Map();
  for (const p of PLACES) tally.set(p.kind, (tally.get(p.kind) || 0) + 1);
  const order = [...tally.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  for (const [kind, n] of order) {
    const state = { n: n, on: OFF_BY_DEFAULT.indexOf(kind) < 0 };
    kinds.set(kind, state);
    const row = document.createElement('label');
    row.className = 'krow';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = state.on;
    box.addEventListener('change', () => { state.on = box.checked; refresh(); });
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.style.background = kindColour(kind);
    const name = document.createElement('span');
    name.textContent = kind;
    const count = document.createElement('span');
    count.className = 'n';
    count.textContent = n;
    // So All/None/Invert can find the box that belongs to each kind.
    box.dataset.kind = kind;
    row.append(box, dot, name, count);
    kindsBox.append(row);
  }
}

/* All, none, or the other ones -- because ticking twenty-five kinds off one at
   a time to look at one of them is not a legend, it is a chore. */
function pick(what) {
  for (const state of kinds.values()) {
    state.on = what === 'all' ? true : what === 'none' ? false : !state.on;
  }
  for (const box of kindsBox.querySelectorAll('input')) {
    box.checked = kinds.get(box.dataset.kind).on;
  }
  refresh();
}

const namesBtn = document.getElementById('names');
function setNames(on) {
  namesOn = on;
  namesBtn.classList.toggle('on', on);
  drawList();
  paint();
}

function refresh() {
  const q = findBox.value.trim().toLowerCase();
  shown = PLACES.filter(p => {
    const k = kinds.get(p.kind);
    if (!k || !k.on) return false;
    if (!q) return true;
    return (p.kind + ' ' + p.what + ' ' + p.tile).toLowerCase().includes(q);
  });
  placeCount.textContent = shown.length === PLACES.length
    ? PLACES.length : shown.length + ' of ' + PLACES.length;
  drawList();
  paint();
}

function drawList() {
  listBox.textContent = '';
  if (!shown.length) {
    const none = document.createElement('div');
    none.className = 'none';
    none.textContent = findBox.value.trim()
      ? 'No place matches that.' : 'No kind is ticked.';
    listBox.append(none);
    return;
  }
  for (const p of shown.slice(0, LIST_CAP)) {
    const id = idOf(p);
    const row = document.createElement('div');
    row.className = 'prow' + (found.has(id) ? ' done' : '');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = found.has(id);
    box.title = 'Tick off a place you have been to';
    box.addEventListener('click', e => e.stopPropagation());
    box.addEventListener('change', () => {
      if (box.checked) found.add(id); else found.delete(id);
      row.classList.toggle('done', box.checked);
      remember();
      paint();
    });
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.style.background = kindColour(p.kind);
    const name = document.createElement('span');
    name.className = 'nm';
    name.textContent = namesOn ? titleOf(p) : 'place';
    const at = document.createElement('span');
    at.className = 'd';
    at.textContent = p.cx + ', ' + p.cy;
    row.append(box, dot, name, at);
    row.addEventListener('click', () => goTo(p));
    listBox.append(row);
  }
  if (shown.length > LIST_CAP) {
    const more = document.createElement('div');
    more.className = 'none';
    more.textContent = 'and ' + (shown.length - LIST_CAP)
                     + ' more — narrow it with the search box';
    listBox.append(more);
  }
}

/* Put a place in the middle of the window, close enough to see what it is. */
function goTo(p) {
  const [mx, my] = mapXY(p);
  scale = Math.min(8, Math.max(0.5, 220 / (Math.sqrt(p.cells) * META.px)));
  tx = stage.clientWidth / 2 - mx * scale;
  ty = stage.clientHeight / 2 - my * scale;
  apply();
}

/* The pins, moved to wherever their place now sits on screen. Only the ones on
   screen are put up, biggest first, and never one on top of another. */
const pool = [];
function updateMarkers() {
  const w = stage.clientWidth, h = stage.clientHeight;
  const near = [];
  for (const p of shown) {
    const [mx, my] = mapXY(p);
    const x = tx + mx * scale, y = ty + my * scale;
    if (x < -40 || y < -20 || x > w + 40 || y > h + 20) continue;
    near.push({ p: p, x: x, y: y,
                w: namesOn ? titleOf(p).length * CHAR_W + LABEL_PAD : 15 });
  }
  /* Biggest first, so a silo district keeps its pin and the ruin beside it is
     the one that gives way. */
  near.sort((a, b) => b.p.cells - a.p.cells);
  // A nameless pin is a fifteen-pixel square rather than a two-hundred-pixel
  // label, so far more of them fit before the map stops being readable.
  const cap = namesOn ? MAX_MARKERS : MAX_MARKERS * 3;
  const keep = [];
  for (const m of near) {
    if (keep.length >= cap) break;
    let clash = false;
    for (const q of keep) {
      if (Math.abs(q.x - m.x) < (q.w + m.w) * 0.5 + 8
          && Math.abs(q.y - m.y) < LABEL_H) { clash = true; break; }
    }
    if (!clash) keep.push(m);
  }
  while (pool.length < keep.length) {
    const el = document.createElement('div');
    el.className = 'mk';
    const dot = document.createElement('span');
    dot.className = 'dot';
    const text = document.createElement('i');
    el.append(dot, text);
    el._dot = dot;
    el._text = text;
    markersBox.append(el);
    pool.push(el);
  }
  for (let i = 0; i < pool.length; i++) {
    const el = pool[i];
    if (i >= keep.length) { el.hidden = true; continue; }
    const m = keep[i];
    el.hidden = false;
    el.style.left = m.x.toFixed(1) + 'px';
    el.style.top = m.y.toFixed(1) + 'px';
    el._dot.style.background = kindColour(m.p.kind);
    el._text.textContent = titleOf(m.p);
    el.classList.toggle('mute', !namesOn);
    el.classList.toggle('done', found.has(idOf(m.p)));
    el.title = 'cell ' + m.p.cx + ', ' + m.p.cy + ' — click to centre';
    el.onclick = () => goTo(m.p);
  }
}

if (PLACES && PLACES.length) {
  document.getElementById('placesTab').hidden = false;
  namesBtn.hidden = false;
  buildKinds();
  findBox.addEventListener('input', refresh);
  for (const b of document.querySelectorAll('.pick button')) {
    b.addEventListener('click', () => pick(b.dataset.pick));
  }
  namesBtn.addEventListener('click', () => setNames(!namesOn));
  refresh();
}

function start() {
  const f = 4;
  if (META.w >= 512) {
    const c = document.createElement('canvas');
    c.width = Math.round(META.w / f);
    c.height = Math.round(META.h / f);
    const g = c.getContext('2d');
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = 'high';
    g.drawImage(src, 0, 0, c.width, c.height);
    mip = c;
    mipK = c.width / META.w;
  }
  ready = true;
  document.getElementById('wait').remove();
  fit();
  draw();   /* the first frame should not wait on a callback the browser may be
               holding back, so paint once straight away */
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') draw();
});
fit();
if (src.decode) { src.decode().then(start, start); }
else if (src.complete) { start(); }
else { src.addEventListener('load', start); }
</script>
"""


def _legend(entries):
    out = []
    for label, rgb in entries:
        out.append('<div><i style="background:rgb(%d,%d,%d)"></i>%s</div>'
                   % (rgb[0], rgb[1], rgb[2], html.escape(label)))
    return "".join(out)


def _stats(pairs):
    out = []
    for k, v in pairs:
        out.append("<span>%s</span><b>%s</b>" % (html.escape(k), html.escape(str(v))))
    return "".join(out)


def _encode(image):
    """The map as a data URI, in whichever format keeps the page smallest.

    A 5 km world at two metres per pixel is 16 megapixels; as PNG that is a 15 MB
    page, and the whole point of inlining the image is that the file stays easy
    to keep and to send. WebP at this quality is visually identical over the
    whole map and about a quarter of the size.
    """
    for mime, opts in (("webp", dict(format="WEBP", quality=95, method=4)),
                       ("png", dict(format="PNG", optimize=True))):
        try:
            buf = io.BytesIO()
            image.save(buf, **opts)
        except Exception:
            continue
        return ("data:image/%s;base64," % mime
                + base64.b64encode(buf.getvalue()).decode("ascii"))
    raise RuntimeError("could not encode the map image")


def write_html(path, image, meta, stats, legend, title, subtitle, places=None):
    """image: PIL Image. meta: dict with w/h/px/x0/y1 in map pixels & cells.

    ``places`` is poi.collect's landmarks, which the page turns into pins on
    the map and a legend you can pick the kinds apart with.
    """
    data_uri = _encode(image)

    page = PAGE % {
        "title": html.escape(title),
        "name": html.escape(title),
        "subtitle": html.escape(subtitle),
        "img": data_uri,
        "w": image.width,
        "h": image.height,
        "meta": json.dumps(meta),
        "stats": _stats(stats),
        "legend": _legend(legend),
        "places": json.dumps(places or []),
        "world_key": json.dumps(title),
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path
