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
  html, body { margin:0; height:100%%; background:#11151c; color:#e8edf5;
       font:13px/1.5 "Segoe UI", system-ui, sans-serif; overflow:hidden; }
  /* touch-action keeps the browser from claiming the gesture and cancelling our
     pointer stream halfway through a drag. */
  #stage { position:absolute; inset:0; cursor:grab; overflow:hidden;
       touch-action:none; user-select:none; -webkit-user-select:none; }
  #stage.drag { cursor:grabbing; }
  #view { position:absolute; left:0; top:0; }
  #src { display:none; }
  #wait { position:absolute; left:50%%; top:50%%; transform:translate(-50%%,-50%%);
       color:#6d7f97; font-size:12px; letter-spacing:.3px; }
  .panel { position:absolute; background:rgba(18,23,31,.88); border:1px solid #2b3444;
       border-radius:10px; padding:10px 13px; backdrop-filter:blur(8px); }
  #info { top:12px; left:12px; max-width:330px; }
  #info h1 { margin:0 0 2px; font-size:15px; font-weight:600; letter-spacing:.2px; }
  #info .sub { color:#8fa0b8; font-size:11.5px; }
  #stats { margin-top:9px; display:grid; grid-template-columns:auto auto;
       gap:2px 14px; font-size:11.5px; }
  #stats b { font-weight:600; color:#cfe0f5; }
  #stats span { color:#8fa0b8; }
  #hud { bottom:12px; left:12px; font-variant-numeric:tabular-nums; font-size:11.5px;
       color:#a9b9cf; }
  #hud b { color:#e8edf5; font-weight:600; }
  #legend { bottom:12px; right:12px; }
  #legend div { display:flex; align-items:center; gap:7px; margin:2px 0;
       font-size:11.5px; color:#b9c7da; }
  #legend i { width:12px; height:12px; border-radius:3px; display:inline-block;
       border:1px solid rgba(255,255,255,.14); }
  #help { top:12px; right:12px; color:#8fa0b8; font-size:11.5px; text-align:right; }
  kbd { background:#222b38; border:1px solid #38455a; border-bottom-width:2px;
       border-radius:4px; padding:0 4px; font:inherit; font-size:11px; color:#dce7f5; }
</style>

<div id="stage"><canvas id="view"></canvas><div id="wait">unpacking the map&hellip;</div>
  <img id="src" src="%(img)s" width="%(w)d" height="%(h)d"
       alt="world map" draggable="false"></div>

<div class="panel" id="info">
  <h1>%(name)s</h1>
  <div class="sub">%(subtitle)s</div>
  <div id="stats">%(stats)s</div>
</div>

<div class="panel" id="help">
  drag to pan &middot; scroll or double-click to zoom<br><kbd>F</kbd> fit &middot; <kbd>1</kbd> 100%%
</div>

<div class="panel" id="hud">cell <b id="cell">-</b> &nbsp; world <b id="world">-</b> &nbsp;
  <b id="zoom">-</b></div>

<div class="panel" id="legend">%(legend)s</div>

<script>
const META = %(meta)s;
const BG = '#11151c';
const stage = document.getElementById('stage');
const view = document.getElementById('view'), src = document.getElementById('src');
const ctx = view.getContext('2d', { alpha: false });
const cellOut = document.getElementById('cell'), worldOut = document.getElementById('world');
const zoomOut = document.getElementById('zoom');
let scale = 1, tx = 0, ty = 0, ready = false, mip = null, mipK = 1;

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
  if (e.key === 'f' || e.key === 'F') fit();
  if (e.key === '1') zoomAt(stage.clientWidth / 2, stage.clientHeight / 2, 1 / scale);
});

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


def write_html(path, image, meta, stats, legend, title, subtitle):
    """image: PIL Image. meta: dict with w/h/px/x0/y1 in map pixels & cells."""
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
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path
