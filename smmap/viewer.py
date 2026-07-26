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
  #stage { position:absolute; inset:0; cursor:grab; overflow:hidden; }
  #stage.drag { cursor:grabbing; }
  #map { position:absolute; transform-origin:0 0; image-rendering:pixelated;
         will-change:transform; }
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

<div id="stage"><img id="map" src="%(img)s" width="%(w)d" height="%(h)d" alt="world map"></div>

<div class="panel" id="info">
  <h1>%(name)s</h1>
  <div class="sub">%(subtitle)s</div>
  <div id="stats">%(stats)s</div>
</div>

<div class="panel" id="help">
  drag to pan &middot; scroll to zoom<br><kbd>F</kbd> fit &middot; <kbd>1</kbd> 100%%
</div>

<div class="panel" id="hud">cell <b id="cell">-</b> &nbsp; world <b id="world">-</b> &nbsp;
  <b id="zoom">-</b></div>

<div class="panel" id="legend">%(legend)s</div>

<script>
const META = %(meta)s;
const stage = document.getElementById('stage'), map = document.getElementById('map');
let scale = 1, tx = 0, ty = 0;

function apply() {
  map.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
  document.getElementById('zoom').textContent = Math.round(scale * 100) + '%%';
}
function fit() {
  const s = Math.min(stage.clientWidth / META.w, stage.clientHeight / META.h) * 0.94;
  scale = s;
  tx = (stage.clientWidth - META.w * s) / 2;
  ty = (stage.clientHeight - META.h * s) / 2;
  apply();
}
function zoomAt(cx, cy, factor) {
  const ns = Math.min(24, Math.max(0.05, scale * factor));
  tx = cx - (cx - tx) * (ns / scale);
  ty = cy - (cy - ty) * (ns / scale);
  scale = ns; apply();
}

stage.addEventListener('wheel', e => {
  e.preventDefault();
  zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.15 : 1 / 1.15);
}, { passive: false });

let drag = null;
stage.addEventListener('pointerdown', e => {
  drag = { x: e.clientX - tx, y: e.clientY - ty };
  stage.classList.add('drag'); stage.setPointerCapture(e.pointerId);
});
stage.addEventListener('pointerup', e => {
  drag = null; stage.classList.remove('drag');
});
stage.addEventListener('pointermove', e => {
  const r = stage.getBoundingClientRect();
  const mx = (e.clientX - r.left - tx) / scale, my = (e.clientY - r.top - ty) / scale;
  const cx = Math.floor(mx / META.px) + META.x0;
  const cy = META.y1 - Math.floor(my / META.px);
  const inside = mx >= 0 && my >= 0 && mx < META.w && my < META.h;
  document.getElementById('cell').textContent = inside ? `${cx}, ${cy}` : '-';
  document.getElementById('world').textContent = inside
    ? `${Math.round((mx / META.px + META.x0) * 64)}, ${Math.round((META.y1 + 1 - my / META.px) * 64)}` : '-';
  if (drag) { tx = e.clientX - drag.x; ty = e.clientY - drag.y; apply(); }
});

addEventListener('keydown', e => {
  if (e.key === 'f' || e.key === 'F') fit();
  if (e.key === '1') { const r = stage.getBoundingClientRect();
    zoomAt(r.width / 2, r.height / 2, 1 / scale); }
});
fit();
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
