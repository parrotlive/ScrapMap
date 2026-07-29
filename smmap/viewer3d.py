"""Write a single self-contained HTML file that draws the world in 3D.

Same bargain as the flat viewer: everything is inlined, so the file works off
the disk with no server, no assets folder and nothing to install. What differs
is that the height is no longer drawn into the picture, it is the picture --
the ground colour is draped over a mesh the GPU displaces from a height
texture, and the lighting, the shadows and the water are worked out per frame
rather than baked once.

The mesh carries no vertex buffers at all. Every vertex works out where it
belongs from gl_VertexID and reads its own height out of the texture, so
changing the detail is a matter of drawing a different number of vertices and
nothing has to be rebuilt or uploaded. That is what makes the detail control
instant, and it is why a nine kilometre world costs a few hundred kilobytes of
geometry rather than a hundred megabytes.

Shadows are baked into an offscreen texture whenever the sun moves rather than
marched per pixel per frame, which is the difference between a slideshow and
something you can fly around.
"""

import base64
import html
import io
import json

import numpy as np

from . import palette

PAGE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin:0; height:100%; background:#0a0e14; color:#e8edf5;
       font:13px/1.5 "Segoe UI", system-ui, sans-serif; overflow:hidden; }
  #stage { position:absolute; inset:0; touch-action:none; user-select:none;
       -webkit-user-select:none; cursor:grab; }
  #stage.drag { cursor:grabbing; }
  #gl { display:block; width:100%; height:100%; }
  #wait { position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
       color:#6d7f97; font-size:12px; letter-spacing:.3px; text-align:center; }
  .panel { position:absolute; background:rgba(14,19,27,.86); border:1px solid #2b3444;
       border-radius:10px; padding:10px 13px; backdrop-filter:blur(8px); }
  #info { top:12px; left:12px; max-width:320px; }
  #info h1 { margin:0 0 2px; font-size:15px; font-weight:600; letter-spacing:.2px; }
  #info .sub { color:#8fa0b8; font-size:11.5px; }
  #stats { margin-top:9px; display:grid; grid-template-columns:auto auto;
       gap:2px 14px; font-size:11.5px; }
  #stats b { font-weight:600; color:#cfe0f5; }
  #stats span { color:#8fa0b8; }
  #ctl { top:12px; right:12px; width:212px; }
  #ctl label { display:block; margin:0 0 9px; font-size:11.5px; color:#a9b9cf; }
  #ctl label:last-child { margin-bottom:0; }
  #ctl .row { display:flex; justify-content:space-between; margin-bottom:3px; }
  #ctl .row b { color:#e8edf5; font-weight:600; font-variant-numeric:tabular-nums; }
  #ctl input[type=range] { width:100%; display:block; accent-color:#5b8fd6;
       margin:0; height:16px; }
  #ctl select { width:100%; background:#1a2230; color:#dce7f5; border:1px solid #38455a;
       border-radius:5px; padding:3px 5px; font:inherit; font-size:11.5px; }
  #ctl .check { display:flex; align-items:center; gap:7px; }
  #ctl .check input { accent-color:#5b8fd6; margin:0; }
  #help { bottom:12px; left:12px; color:#8fa0b8; font-size:11.5px; }
  #help b { color:#cfe0f5; font-weight:600; }
  #hud { bottom:12px; right:12px; font-variant-numeric:tabular-nums;
       font-size:11.5px; color:#a9b9cf; }
  #hud b { color:#e8edf5; font-weight:600; }
  #oops { max-width:420px; left:50%; top:50%; transform:translate(-50%,-50%);
       line-height:1.6; }
  #oops h2 { margin:0 0 6px; font-size:14px; }
  #oops p { margin:0; color:#a9b9cf; font-size:12px; }
</style>

<div id="stage"><canvas id="gl"></canvas>
  <div id="wait">unpacking the world&hellip;</div></div>

<div class="panel" id="info">
  <h1>__NAME__</h1>
  <div class="sub">__SUBTITLE__</div>
  <div id="stats">__STATS__</div>
</div>

<div class="panel" id="ctl">
  <label><span class="row"><span>Sun direction</span><b id="azOut">315&deg;</b></span>
    <input id="az" type="range" min="0" max="359" value="315"></label>
  <label><span class="row"><span>Sun height</span><b id="elOut">38&deg;</b></span>
    <input id="el" type="range" min="2" max="88" value="38"></label>
  <label><span class="row"><span>Relief</span><b id="exOut">1.0&times;</b></span>
    <input id="ex" type="range" min="5" max="60" value="10"></label>
  <label><span class="row"><span>Detail</span></span>
    <select id="detail">
      <option value="0.25">Low</option>
      <option value="0.5" selected>Normal</option>
      <option value="1">High</option>
    </select></label>
  <label class="check"><input id="shadows" type="checkbox" checked> Shadows</label>
  <label class="check"><input id="wet" type="checkbox" checked> Water</label>
  <label class="check" id="objRow" hidden><input id="objs" type="checkbox" checked>
    Objects</label>
  <label id="reachRow" hidden
         title="How far a one-metre object is drawn. Bigger things carry proportionally further, so a warehouse is visible long after the bushes around it have gone.">
    <span class="row"><span>Object range</span><b id="reachOut">250 m</b></span>
    <input id="reach" type="range" min="40" max="700" value="250"></label>
</div>

<div class="panel" id="help">
  drag to turn &middot; right-drag or <b>shift</b>-drag to move &middot; scroll to zoom<br>
  <b>R</b> reset view &middot; <b>T</b> straight down &middot; <b>W A S D</b> move
</div>

<div class="panel" id="hud">eye <b id="eye">-</b> &nbsp; <b id="fps">-</b></div>

<script>
const META = __META__;
const LIQUID = __LIQUID__;
/* One entry per distinct mesh: where its triangles are, which run of instances
   belongs to it, and how big it is. Null when the world went up without its
   objects. */
const OBJ_DRAWS = __OBJ_DRAWS__;
const OBJ_VERTS = __OBJ_VERTS__;
const OBJ_INDEX = __OBJ_INDEX__;
const OBJ_INST = __OBJ_INST__;
const OBJ_STRIDE = 36;

/* ---------------------------------------------------------------- matrices */
/* Just enough 4x4 to place a camera and to turn a pixel back into a ray. */

function perspective(fov, aspect, near, far) {
  const f = 1 / Math.tan(fov / 2), d = near - far;
  return [f / aspect, 0, 0, 0,
          0, f, 0, 0,
          0, 0, (far + near) / d, -1,
          0, 0, 2 * far * near / d, 0];
}
function lookAt(eye, at, up) {
  let z = norm(sub(eye, at));
  let x = norm(cross(up, z));
  let y = cross(z, x);
  return [x[0], y[0], z[0], 0,
          x[1], y[1], z[1], 0,
          x[2], y[2], z[2], 0,
          -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
}
function mul(a, b) {
  const o = new Array(16);
  for (let c = 0; c < 4; c++)
    for (let r = 0; r < 4; r++)
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1]
                   + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
  return o;
}
function invert(m) {
  const o = new Array(16);
  o[0] = m[5]*m[10]*m[15] - m[5]*m[11]*m[14] - m[9]*m[6]*m[15]
       + m[9]*m[7]*m[14] + m[13]*m[6]*m[11] - m[13]*m[7]*m[10];
  o[4] = -m[4]*m[10]*m[15] + m[4]*m[11]*m[14] + m[8]*m[6]*m[15]
       - m[8]*m[7]*m[14] - m[12]*m[6]*m[11] + m[12]*m[7]*m[10];
  o[8] = m[4]*m[9]*m[15] - m[4]*m[11]*m[13] - m[8]*m[5]*m[15]
       + m[8]*m[7]*m[13] + m[12]*m[5]*m[11] - m[12]*m[7]*m[9];
  o[12] = -m[4]*m[9]*m[14] + m[4]*m[10]*m[13] + m[8]*m[5]*m[14]
        - m[8]*m[6]*m[13] - m[12]*m[5]*m[10] + m[12]*m[6]*m[9];
  o[1] = -m[1]*m[10]*m[15] + m[1]*m[11]*m[14] + m[9]*m[2]*m[15]
       - m[9]*m[3]*m[14] - m[13]*m[2]*m[11] + m[13]*m[3]*m[10];
  o[5] = m[0]*m[10]*m[15] - m[0]*m[11]*m[14] - m[8]*m[2]*m[15]
       + m[8]*m[3]*m[14] + m[12]*m[2]*m[11] - m[12]*m[3]*m[10];
  o[9] = -m[0]*m[9]*m[15] + m[0]*m[11]*m[13] + m[8]*m[1]*m[15]
       - m[8]*m[3]*m[13] - m[12]*m[1]*m[11] + m[12]*m[3]*m[9];
  o[13] = m[0]*m[9]*m[14] - m[0]*m[10]*m[13] - m[8]*m[1]*m[14]
        + m[8]*m[2]*m[13] + m[12]*m[1]*m[10] - m[12]*m[2]*m[9];
  o[2] = m[1]*m[6]*m[15] - m[1]*m[7]*m[14] - m[5]*m[2]*m[15]
       + m[5]*m[3]*m[14] + m[13]*m[2]*m[7] - m[13]*m[3]*m[6];
  o[6] = -m[0]*m[6]*m[15] + m[0]*m[7]*m[14] + m[4]*m[2]*m[15]
       - m[4]*m[3]*m[14] - m[12]*m[2]*m[7] + m[12]*m[3]*m[6];
  o[10] = m[0]*m[5]*m[15] - m[0]*m[7]*m[13] - m[4]*m[1]*m[15]
        + m[4]*m[3]*m[13] + m[12]*m[1]*m[7] - m[12]*m[3]*m[5];
  o[14] = -m[0]*m[5]*m[14] + m[0]*m[6]*m[13] + m[4]*m[1]*m[14]
        - m[4]*m[2]*m[13] - m[12]*m[1]*m[6] + m[12]*m[2]*m[5];
  o[3] = -m[1]*m[6]*m[11] + m[1]*m[7]*m[10] + m[5]*m[2]*m[11]
       - m[5]*m[3]*m[10] - m[9]*m[2]*m[7] + m[9]*m[3]*m[6];
  o[7] = m[0]*m[6]*m[11] - m[0]*m[7]*m[10] - m[4]*m[2]*m[11]
       + m[4]*m[3]*m[10] + m[8]*m[2]*m[7] - m[8]*m[3]*m[6];
  o[11] = -m[0]*m[5]*m[11] + m[0]*m[7]*m[9] + m[4]*m[1]*m[11]
        - m[4]*m[3]*m[9] - m[8]*m[1]*m[7] + m[8]*m[3]*m[5];
  o[15] = m[0]*m[5]*m[10] - m[0]*m[6]*m[9] - m[4]*m[1]*m[10]
        + m[4]*m[2]*m[9] + m[8]*m[1]*m[6] - m[8]*m[2]*m[5];
  let det = m[0]*o[0] + m[1]*o[4] + m[2]*o[8] + m[3]*o[12];
  if (!det) return o;
  det = 1 / det;
  for (let i = 0; i < 16; i++) o[i] *= det;
  return o;
}
const sub = (a, b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
const dot = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2],
                         a[0]*b[1]-a[1]*b[0]];
function norm(a) {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0]/l, a[1]/l, a[2]/l];
}

/* ----------------------------------------------------------------- shaders */

/* Shared by every shader that has to know the shape of the ground.
 *
 * Height arrives as an 8-bit RGBA texture with a 16-bit number split across
 * two channels, so it has to be read with texelFetch and filtered by hand: ask
 * the sampler to interpolate it and you get the high byte of one texel mixed
 * with the low byte of the next, which is noise. Four fetches and a mix give
 * the smooth surface back. */
const COMMON = `
uniform sampler2D uHeight;
uniform ivec2 uTexSize;
uniform vec2 uRange;        /* lowest height, and the span above it */
uniform vec2 uSpan;         /* what the map covers on the ground, in metres */
uniform float uExag;

float solidTexel(ivec2 p) {
  p = clamp(p, ivec2(0), uTexSize - 1);
  vec4 t = texelFetch(uHeight, p, 0);
  float q = floor(t.r * 255.0 + 0.5) * 256.0 + floor(t.g * 255.0 + 0.5);
  return uRange.x + q * (1.0 / 65535.0) * uRange.y;
}
float solidAt(vec2 uv) {
  vec2 t = uv * vec2(uTexSize) - 0.5;
  vec2 i = floor(t), f = t - i;
  ivec2 p = ivec2(i);
  float a = solidTexel(p),               b = solidTexel(p + ivec2(1, 0));
  float c = solidTexel(p + ivec2(0, 1)), d = solidTexel(p + ivec2(1, 1));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
/* Level 0 is not a low water level, it is no water at all. */
vec2 liquidTexel(ivec2 p) {
  p = clamp(p, ivec2(0), uTexSize - 1);
  vec4 t = texelFetch(uHeight, p, 0);
  float packed = floor(t.b * 255.0 + 0.5) * 256.0 + floor(t.a * 255.0 + 0.5);
  float lvl = floor(packed * 0.25);
  if (lvl < 0.5) return vec2(-1e9, 0.0);
  return vec2(uRange.x + (lvl - 1.0) * (1.0 / 16382.0) * uRange.y,
              packed - lvl * 4.0);
}
vec3 worldOf(vec2 uv, float h) {
  return vec3((uv.x - 0.5) * uSpan.x, h * uExag, (uv.y - 0.5) * uSpan.y);
}
vec2 uvOf(vec3 w) { return w.xz / uSpan + 0.5; }
`;

/* The sky is a gradient with the sun burnt into it, and it is also what the
 * land fades into with distance, so both shaders want it. */
const SKY = `
uniform vec3 uSun;
vec3 skyColour(vec3 dir) {
  float up = dir.y;
  vec3 top = vec3(0.216, 0.373, 0.612);
  vec3 haze = vec3(0.639, 0.729, 0.827);
  vec3 below = vec3(0.153, 0.180, 0.216);
  vec3 c = up >= 0.0 ? mix(haze, top, pow(clamp(up, 0.0, 1.0), 0.55))
                     : mix(haze, below, clamp(-up * 3.0, 0.0, 1.0));
  float d = max(dot(dir, uSun), 0.0);
  /* A wide warm bloom with a small disc in it, so the sun is somewhere you can
     see rather than a number in a panel. */
  c += vec3(1.0, 0.85, 0.6) * (pow(d, 8.0) * 0.16 + pow(d, 900.0) * 2.4);
  return c;
}
`;

const SKY_VS = `#version 300 es
out vec2 vNdc;
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  vNdc = p * 2.0 - 1.0;
  gl_Position = vec4(vNdc, 1.0, 1.0);
}`;

const SKY_FS = `#version 300 es
precision highp float;
` + SKY + `
uniform mat4 uInvVP;
uniform vec3 uEye;
in vec2 vNdc;
out vec4 frag;
void main() {
  vec4 p = uInvVP * vec4(vNdc, 1.0, 1.0);
  frag = vec4(skyColour(normalize(p.xyz / p.w - uEye)), 1.0);
}`;

/* The shadow pass. One texel of the height field per pixel: stand on it and
 * walk towards the sun until something gets in the way. Steps grow as they go,
 * so a couple of dozen of them still reach a ridge a kilometre off, and the
 * shadow softens with distance the way a real one does.
 *
 * It walks twice and keeps both answers. Red counts the props as part of the
 * ground, which is what puts a building's shadow on the grass beside it. Green
 * counts only the land, and it is what the props themselves are lit by --
 * because a building stands inside its own footprint, and shading it with red
 * would have every object in the world in its own shadow. */
const SHADOW_FS = `#version 300 es
precision highp float;
precision highp int;
` + COMMON + `
uniform vec3 uSun;
uniform sampler2D uProp;
uniform float uPropCeiling;
out vec4 frag;

/* What blocks the light. When the props are drawn as real meshes they are no
   longer part of the ground, so the ground alone would leave a town casting no
   shadow at all; k puts their height back. With the props still in the ground
   the prop texture is a single black texel and k makes no difference. */
float reliefAt(vec2 uv, float k) {
  return solidAt(uv) + texture(uProp, uv).r * uPropCeiling * k;
}

float march(vec2 uv, float k) {
  vec3 w = worldOf(uv, reliefAt(uv, k));
  float step = length(uSpan) / float(max(uTexSize.x, uTexSize.y)) * 1.5;
  float t = step, s = 1.0;
  for (int i = 0; i < 40; i++) {
    vec3 p = w + uSun * t;
    vec2 q = uvOf(p);
    if (q.x < 0.0 || q.x > 1.0 || q.y < 0.0 || q.y > 1.0) break;
    float d = reliefAt(q, k) * uExag - p.y;
    if (d > 0.0) {
      s = min(s, 1.0 - clamp(d / (0.30 * t), 0.0, 1.0));
      if (s <= 0.001) break;
    }
    t += step;
    step *= 1.16;
  }
  return s;
}

void main() {
  if (uSun.y <= 0.01) { frag = vec4(0.0, 0.0, 0.0, 1.0); return; }
  vec2 uv = gl_FragCoord.xy / vec2(uTexSize);
  frag = vec4(march(uv, 1.0), march(uv, 0.0), 0.0, 1.0);
}`;

/* The land and the water share a vertex shader shape: work out which quad and
 * which corner this vertex is, turn that into a place on the map, and look up
 * how high the map is there. */
const GRID_HEAD = `#version 300 es
precision highp float;
precision highp int;
` + COMMON + `
uniform ivec2 uGrid;
uniform mat4 uVP;
const vec2 CORNER[6] = vec2[6](vec2(0,0), vec2(1,0), vec2(0,1),
                               vec2(1,0), vec2(1,1), vec2(0,1));
vec2 gridUv() {
  int q = gl_VertexID / 6;
  int qy = q / uGrid.x;
  vec2 c = CORNER[gl_VertexID - q * 6];
  return (vec2(float(q - qy * uGrid.x), float(qy)) + c) / vec2(uGrid);
}
`;

const LAND_VS = GRID_HEAD + `
out vec2 vUv;
out vec3 vWorld;
void main() {
  vec2 uv = gridUv();
  vec3 w = worldOf(uv, solidAt(uv));
  vUv = uv; vWorld = w;
  gl_Position = uVP * vec4(w, 1.0);
}`;

const LAND_FS = `#version 300 es
precision highp float;
precision highp int;
` + COMMON + SKY + `
uniform sampler2D uColour;
uniform sampler2D uShadow;
uniform vec3 uEye;
uniform float uFog;
uniform float uShadowOn;
in vec2 vUv;
in vec3 vWorld;
out vec4 frag;

void main() {
  /* The normal comes from the height field rather than from the mesh, so the
     lighting keeps its detail however coarsely the land is tessellated. */
  vec2 e = 1.0 / vec2(uTexSize);
  float m = uSpan.x / float(uTexSize.x);
  float hx = (solidAt(vUv + vec2(e.x, 0.0)) - solidAt(vUv - vec2(e.x, 0.0)));
  float hz = (solidAt(vUv + vec2(0.0, e.y)) - solidAt(vUv - vec2(0.0, e.y)));
  vec3 n = normalize(vec3(-hx * uExag, 2.0 * m, -hz * uExag));

  vec3 albedo = texture(uColour, vUv).rgb;
  float sun = max(dot(n, uSun), 0.0);
  float shade = mix(1.0, texture(uShadow, vUv).r, uShadowOn);
  /* Sky above and bounced ground below: cheap, and it keeps the shaded side of
     a hill blue rather than black. */
  vec3 ambient = mix(vec3(0.19, 0.18, 0.16), vec3(0.34, 0.40, 0.50),
                     0.5 + 0.5 * n.y);
  vec3 lit = albedo * (ambient + vec3(1.05, 0.98, 0.86) * sun * shade);

  vec3 toEye = vWorld - uEye;
  float d = length(toEye) * uFog;
  lit = mix(lit, skyColour(normalize(toEye)), 1.0 - exp(-d * d));
  frag = vec4(lit, 1.0);
}`;

/* Water is the same grid again, flattened onto whatever surface the tiles put
 * there. A vertex takes the highest level in the texels around it, which
 * spreads each pool a little past its own edge; the fragment then throws away
 * anything the land already stands above, so the shoreline ends up exactly
 * where the water plane cuts the ground rather than on a texel boundary. */
const WATER_VS = GRID_HEAD + `
out vec2 vUv;
out vec3 vWorld;
out float vLevel;
out float vKind;
void main() {
  vec2 uv = gridUv();
  ivec2 p = ivec2(floor(uv * vec2(uTexSize)));
  float best = -1e9, kind = 0.0;
  for (int j = -1; j <= 1; j++)
    for (int i = -1; i <= 1; i++) {
      vec2 l = liquidTexel(p + ivec2(i, j));
      if (l.x > best) { best = l.x; kind = l.y; }
    }
  vLevel = best;
  vKind = kind;
  vec3 w = worldOf(uv, best > -1e8 ? best : solidAt(uv));
  vUv = uv; vWorld = w;
  gl_Position = uVP * vec4(w, 1.0);
}`;

/* How a liquid surface looks, given how deep it is and what kind it is. Both
 * the water inside the map and the open sea beyond it come through here, so
 * that the two are the same water and the join does not show. */
const WATER_LOOK = `
uniform vec3 uEye;
uniform float uFog;
uniform float uTime;
uniform vec3 uShallow[3];
uniform vec3 uDeep[3];

vec4 waterLook(vec3 world, float depth, int kind, float shade) {
  float t = clamp(depth / 14.0, 0.0, 1.0);
  vec3 body = mix(uShallow[kind], uDeep[kind], t);

  /* Three swells crossing at angles, summed. Multiplying one wave by another
     is cheaper and looks like a chessboard, because that is what a separable
     function is; the sun picks the pattern out and the sea turns to gingham.
     Each term is a real travelling wave, and the normal is its slope, so the
     gradient falls out analytically rather than being sampled.

     Wavelengths run from twenty to sixty metres. Once a pixel covers a good
     part of the shortest of them there is nothing left to resolve and drawing
     it anyway is just aliasing, so the waves flatten out with distance and the
     sun's highlight broadens to match -- which is what the eye expects anyway:
     chop close to, a wide glitter path far off. */
  float pxm = max(fwidth(world.x), fwidth(world.z));
  float ripple = clamp(1.0 - (pxm - 1.5) / 8.0, 0.0, 1.0);
  const vec2 K1 = vec2(0.941, 0.339), K2 = vec2(-0.451, 0.893),
             K3 = vec2(0.717, -0.697);
  vec2 g = K1 * 0.016 * cos(dot(world.xz, K1) * 0.11 - uTime * 0.9)
         + K2 * 0.014 * cos(dot(world.xz, K2) * 0.17 + uTime * 1.15)
         + K3 * 0.009 * cos(dot(world.xz, K3) * 0.29 - uTime * 1.6);
  /* Three waves of fixed height repeat exactly, and the sun finds the repeat
     and rules a grid over the sea. A long slow swell drifting across them
     gives patches of chop and patches of calm, which is all it takes. */
  g *= 0.55 + 0.45 * cos(dot(world.xz, vec2(0.31, 0.95)) * 0.011 + uTime * 0.17);
  vec3 n = normalize(vec3(-g.x * ripple, 1.0, -g.y * ripple));

  vec3 view = normalize(uEye - world);
  float fres = pow(1.0 - clamp(dot(n, view), 0.0, 1.0), 4.0);
  vec3 refl = skyColour(reflect(-view, n));
  /* The highlight is deliberately wider than the waves are steep. Sharpen it
     and each crest lights separately, which on water this regular prints a
     lattice across the sea rather than a shimmer. */
  float spec = pow(max(dot(reflect(-uSun, n), view), 0.0),
                   mix(24.0, 64.0, ripple)) * shade * 0.75;

  vec3 c = mix(body, refl, 0.10 + 0.55 * fres) + vec3(1.0, 0.95, 0.85) * spec;
  /* Shallow water keeps some of the bed showing through. */
  float a = clamp(0.55 + 0.45 * sqrt(t) * 1.6, 0.0, 1.0);
  a = mix(a, 1.0, fres * 0.6);

  vec3 toEye = world - uEye;
  float d = length(toEye) * uFog;
  float fog = 1.0 - exp(-d * d);
  return vec4(mix(c, skyColour(normalize(toEye)), fog), mix(a, 1.0, fog));
}
`;

const WATER_FS = `#version 300 es
precision highp float;
precision highp int;
` + COMMON + SKY + WATER_LOOK + `
uniform sampler2D uShadow;
uniform float uShadowOn;
in vec2 vUv;
in vec3 vWorld;
in float vLevel;
in float vKind;
out vec4 frag;

void main() {
  if (vLevel < -1e8) discard;
  float depth = vLevel - solidAt(vUv);
  /* A finger's depth of water over a beach is not water, it is the rounding
     between two grids that were never going to agree to the millimetre. */
  if (depth <= 0.03) discard;
  /* A triangle straddling two kinds of pool interpolates between them, so the
     index has to be pinned back inside the array before it is used. */
  int k = clamp(int(vKind + 0.5), 0, 2);
  frag = waterLook(vWorld, depth, k,
                   mix(1.0, texture(uShadow, vUv).r, uShadowOn));
}`;

/* The world stops at its own border, and a sea that stops with it leaves the
 * map floating on nothing like a slab on a table. This is one big quad at sea
 * level with the map's own footprint cut out of it, so the ocean carries on to
 * the horizon and the fog takes it from there. */
const SEA_VS = `#version 300 es
precision highp float;
uniform mat4 uVP;
uniform float uSeaY;
uniform float uReach;
out vec3 vWorld;
const vec2 Q[6] = vec2[6](vec2(-1,-1), vec2(1,-1), vec2(-1,1),
                          vec2(1,-1), vec2(1,1), vec2(-1,1));
void main() {
  vec2 q = Q[gl_VertexID] * uReach;
  vWorld = vec3(q.x, uSeaY, q.y);
  gl_Position = uVP * vec4(vWorld, 1.0);
}`;

const SEA_FS = `#version 300 es
precision highp float;
precision highp int;
` + COMMON + SKY + WATER_LOOK + `
in vec3 vWorld;
out vec4 frag;
void main() {
  vec2 uv = uvOf(vWorld);
  /* Inside the map the water is the tiles' own, drawn with its real depth. */
  if (uv.x > 0.0 && uv.x < 1.0 && uv.y > 0.0 && uv.y < 1.0) discard;
  frag = waterLook(vWorld, 14.0, 0, 1.0);
}`;

/* Every placed object, drawn as the shape the game itself collides against.
 *
 * One draw call per distinct mesh, however many of them are standing about:
 * the mesh goes up once and each instance contributes a transform, a colour
 * and a size. The size is what lets a vertex decide it is too far away to be
 * worth drawing -- a warehouse is worth a kilometre and a bush is not -- which
 * is the whole of the level of detail here and most of the frame rate. */
const OBJ_VS = `#version 300 es
precision highp float;
precision highp int;
` + COMMON + `
uniform mat4 uVP;
uniform vec3 uEye;
uniform vec3 uCentre;      /* middle of this mesh, in its own frame */
uniform float uReach;      /* how many metres of view each metre of object buys */
in vec3 aVert;
in vec3 aPos;
in vec3 aM0;
in vec3 aM1;
in vec3 aM2;
in vec3 aRgb;
in float aRadius;
out vec3 vWorld;
out vec3 vRgb;
void main() {
  /* Packed column by column, so this is the placement's own matrix and not
     its transpose. */
  mat3 M = mat3(aM0, aM1, aM2);
  /* Relief lifts where a thing stands without stretching the thing itself: a
     silo at five times the relief is a silo five times as high up, not a silo
     five times as tall. */
  vec3 base = vec3(aPos.x, aPos.y * uExag, aPos.z);
  if (distance(uEye, base + M * uCentre) > aRadius * uReach) {
    gl_Position = vec4(0.0, 0.0, 2.0, 1.0);   /* behind the far plane: clipped */
    return;
  }
  vWorld = base + M * aVert;
  vRgb = aRgb;
  gl_Position = uVP * vec4(vWorld, 1.0);
}`;

const OBJ_FS = `#version 300 es
precision highp float;
precision highp int;
` + COMMON + SKY + `
uniform sampler2D uShadow;
uniform vec3 uEye;
uniform float uFog;
uniform float uShadowOn;
in vec3 vWorld;
in vec3 vRgb;
out vec4 frag;
void main() {
  /* A collision hull is flat-faced and closed, so the normal is the facet's
     own -- taken from how the world position changes across the pixel, which
     costs no buffer -- turned to face whoever is looking at it. */
  vec3 n = normalize(cross(dFdx(vWorld), dFdy(vWorld)));
  vec3 view = normalize(uEye - vWorld);
  if (dot(n, view) < 0.0) n = -n;

  float sun = max(dot(n, uSun), 0.0);
  /* Green: the land's shadow only. Red has this object in it. */
  float shade = mix(1.0, texture(uShadow, uvOf(vWorld)).g, uShadowOn);
  vec3 ambient = mix(vec3(0.19, 0.18, 0.16), vec3(0.34, 0.40, 0.50),
                     0.5 + 0.5 * n.y);
  vec3 lit = vRgb * (ambient + vec3(1.05, 0.98, 0.86) * sun * shade);

  vec3 toEye = vWorld - uEye;
  float d = length(toEye) * uFog;
  frag = vec4(mix(lit, skyColour(normalize(toEye)), 1.0 - exp(-d * d)), 1.0);
}`;

/* --------------------------------------------------------------- the world */

const stage = document.getElementById('stage');
const canvas = document.getElementById('gl');
const gl = canvas.getContext('webgl2', {
  antialias: true, alpha: false, depth: true, powerPreference: 'high-performance'
});

function giveUp(title, why) {
  const w = document.getElementById('wait');
  if (w) w.remove();
  const d = document.createElement('div');
  d.className = 'panel';
  d.id = 'oops';
  d.innerHTML = '<h2></h2><p></p>';
  d.querySelector('h2').textContent = title;
  d.querySelector('p').textContent = why;
  stage.appendChild(d);
}

if (!gl) {
  giveUp('This browser cannot draw the world in 3D.',
         'The page needs WebGL 2. Chrome, Edge, Firefox and Safari 15 or newer '
         + 'all have it, but it can be switched off in the browser settings or '
         + 'held back by an old graphics driver. The flat map needs none of this.');
}

function compile(type, source) {
  const s = gl.createShader(type);
  gl.shaderSource(s, source);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
    throw new Error(gl.getShaderInfoLog(s) + '\n' + source);
  return s;
}
function program(vs, fs) {
  const p = gl.createProgram();
  gl.attachShader(p, compile(gl.VERTEX_SHADER, vs));
  gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS))
    throw new Error(gl.getProgramInfoLog(p));
  const u = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) {
    const name = gl.getActiveUniform(p, i).name.replace(/\[0\]$/, '');
    u[name] = gl.getUniformLocation(p, name);
  }
  p.u = u;
  return p;
}

/* Decoding straight from base64 to a Blob rather than pointing an <img> at a
 * data: URI is not fussiness. The height texture is a number, not a picture:
 * if the browser premultiplies it by its own alpha channel -- which is the low
 * byte of the water level, and zero over dry land -- it erases the ground. The
 * bitmap options below are the way to say "hand me the bytes". */
function bytes(b64) {
  const bin = atob(b64);
  const a = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
  return a;
}
function decode(b64, mime) {
  const blob = new Blob([bytes(b64)], { type: mime });
  if (self.createImageBitmap) {
    return createImageBitmap(blob, {
      premultiplyAlpha: 'none', colorSpaceConversion: 'none'
    });
  }
  return new Promise((ok, no) => {
    const img = new Image();
    img.onload = () => ok(img);
    img.onerror = () => no(new Error('could not decode the texture'));
    img.src = URL.createObjectURL(blob);
  });
}

function texture(src, filter, w, h) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
  gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
  if (src instanceof Uint8Array) {
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA,
                  gl.UNSIGNED_BYTE, src);
  } else if (src) {
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, src);
  } else {
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA,
                  gl.UNSIGNED_BYTE, null);
  }
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
  return t;
}

/* ---------------------------------------------------------------- controls */

const cam = { yaw: 0.6, pitch: 0.55, dist: 1, tx: 0, ty: 0, tz: 0 };
const ui = {
  az: document.getElementById('az'), el: document.getElementById('el'),
  ex: document.getElementById('ex'), detail: document.getElementById('detail'),
  shadows: document.getElementById('shadows'), wet: document.getElementById('wet'),
  objs: document.getElementById('objs'), reach: document.getElementById('reach')
};
if (OBJ_DRAWS && OBJ_DRAWS.length) {
  document.getElementById('objRow').hidden = false;
  document.getElementById('reachRow').hidden = false;
}
let exag = 1, shadowStale = true;

function sunVec() {
  const a = ui.az.value * Math.PI / 180, e = ui.el.value * Math.PI / 180;
  return [Math.cos(e) * Math.sin(a), Math.sin(e), -Math.cos(e) * Math.cos(a)];
}
function reset() {
  cam.yaw = 0.55; cam.pitch = 0.52;
  cam.tx = 0; cam.tz = 0;
  cam.ty = (META.sea === null ? META.lo : META.sea) * exag;
  cam.dist = Math.max(META.spanX, META.spanY) * 0.95;
}
function eyeOf() {
  const c = Math.cos(cam.pitch);
  return [cam.tx + cam.dist * c * Math.sin(cam.yaw),
          cam.ty + cam.dist * Math.sin(cam.pitch),
          cam.tz + cam.dist * c * Math.cos(cam.yaw)];
}

let drag = null, lastX = 0, lastY = 0;
stage.addEventListener('pointerdown', e => {
  if (e.target.closest && e.target.closest('.panel')) return;
  e.preventDefault();
  drag = { id: e.pointerId, pan: e.button === 2 || e.button === 1 || e.shiftKey };
  lastX = e.clientX; lastY = e.clientY;
  stage.classList.add('drag');
  stage.setPointerCapture(e.pointerId);
});
stage.addEventListener('pointermove', e => {
  if (!drag || e.pointerId !== drag.id) return;
  const dx = e.clientX - lastX, dy = e.clientY - lastY;
  lastX = e.clientX; lastY = e.clientY;
  if (drag.pan) {
    /* Panning should move the ground under the cursor by roughly the distance
       the cursor moved, which depends on how far away the ground is. */
    const k = cam.dist * 0.0018;
    const s = Math.sin(cam.yaw), c = Math.cos(cam.yaw);
    cam.tx -= (dx * c - dy * s) * k;
    cam.tz += (dx * s + dy * c) * k;
  } else {
    cam.yaw -= dx * 0.005;
    cam.pitch = Math.min(1.553, Math.max(0.02, cam.pitch + dy * 0.005));
  }
});
function drop(e) {
  if (!drag || e.pointerId !== drag.id) return;
  drag = null;
  stage.classList.remove('drag');
  if (stage.hasPointerCapture(e.pointerId)) stage.releasePointerCapture(e.pointerId);
}
stage.addEventListener('pointerup', drop);
stage.addEventListener('pointercancel', drop);
stage.addEventListener('contextmenu', e => e.preventDefault());
stage.addEventListener('wheel', e => {
  e.preventDefault();
  const step = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
  const far = Math.max(META.spanX, META.spanY) * 3;
  cam.dist = Math.min(far, Math.max(8, cam.dist * Math.pow(1.0016, e.deltaY * step)));
}, { passive: false });

const held = new Set();
addEventListener('keydown', e => {
  const k = e.key.toLowerCase();
  if (k === 'r') { reset(); return; }
  if (k === 't') { cam.pitch = 1.553; return; }
  if ('wasdqe'.includes(k)) held.add(k);
});
addEventListener('keyup', e => held.delete(e.key.toLowerCase()));
addEventListener('blur', () => held.clear());

function fly(dt) {
  if (!held.size) return;
  const k = cam.dist * dt * 0.6;
  const s = Math.sin(cam.yaw), c = Math.cos(cam.yaw);
  if (held.has('w')) { cam.tx -= s * k; cam.tz -= c * k; }
  if (held.has('s')) { cam.tx += s * k; cam.tz += c * k; }
  if (held.has('a')) { cam.tx -= c * k; cam.tz += s * k; }
  if (held.has('d')) { cam.tx += c * k; cam.tz -= s * k; }
  if (held.has('q')) cam.yaw += dt * 1.2;
  if (held.has('e')) cam.yaw -= dt * 1.2;
}

/* ------------------------------------------------------------------- start */

const azOut = document.getElementById('azOut'), elOut = document.getElementById('elOut');
const exOut = document.getElementById('exOut'), eyeOut = document.getElementById('eye');
const fpsOut = document.getElementById('fps');

const reachOut = document.getElementById('reachOut');

function readUi() {
  azOut.textContent = ui.az.value + '°';
  elOut.textContent = ui.el.value + '°';
  reachOut.textContent = ui.reach.value + ' m';
  const e = ui.ex.value / 10;
  exOut.textContent = e.toFixed(1) + '×';
  if (e !== exag) { cam.ty *= e / exag; exag = e; }
  shadowStale = true;
}
for (const el of [ui.az, ui.el, ui.ex, ui.reach]) el.addEventListener('input', readUi);

async function start() {
  const [colour, height, prop] = await Promise.all([
    decode(__COLOUR__, '__COLOUR_MIME__'), decode(__HEIGHT__, 'image/png'),
    __PROP__ ? decode(__PROP__, 'image/png') : null
  ]);

  /* A card only has to offer 2048; anything bigger and the colour has to come
     down to fit, which costs nothing but sharpness at the closest zoom. */
  const cap = gl.getParameter(gl.MAX_TEXTURE_SIZE);
  let src = colour;
  if (Math.max(colour.width, colour.height) > cap) {
    const k = cap / Math.max(colour.width, colour.height);
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(colour.width * k));
    c.height = Math.max(1, Math.round(colour.height * k));
    const g = c.getContext('2d');
    g.imageSmoothingQuality = 'high';
    g.drawImage(colour, 0, 0, c.width, c.height);
    src = c;
  }

  const texColour = texture(src, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.generateMipmap(gl.TEXTURE_2D);
  const aniso = gl.getExtension('EXT_texture_filter_anisotropic');
  if (aniso) {
    gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT,
                     Math.min(8, gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT)));
  }
  const texHeight = texture(height, gl.NEAREST);
  const texShadow = texture(null, gl.LINEAR, META.texW, META.texH);
  /* With the props still baked into the ground there is nothing to add back,
     so a single black texel stands in and the shadow pass needs no branch. */
  const texProp = prop ? texture(prop, gl.LINEAR)
                       : texture(new Uint8Array([0, 0, 0, 255]), gl.NEAREST, 1, 1);
  const fbo = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D,
                          texShadow, 0);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);

  const pSky = program(SKY_VS, SKY_FS);
  const pShadow = program(SKY_VS, SHADOW_FS);
  const pLand = program(LAND_VS, LAND_FS);
  const pWater = program(WATER_VS, WATER_FS);
  const pSea = META.sea === null ? null : program(SEA_VS, SEA_FS);
  const haveObjects = !!(OBJ_DRAWS && OBJ_DRAWS.length);
  const pObj = haveObjects ? program(OBJ_VS, OBJ_FS) : null;

  /* The terrain, the water and the sky have no vertex attributes at all, but a
     bound array object is still required before a draw call. */
  const blankVao = gl.createVertexArray();
  gl.bindVertexArray(blankVao);
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texHeight);
  gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, texColour);
  gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, texShadow);
  gl.activeTexture(gl.TEXTURE3); gl.bindTexture(gl.TEXTURE_2D, texProp);

  /* The objects do have attributes: the mesh library in one buffer shared by
     every draw, and the instances in another that each draw points into at a
     different offset. */
  let objVao = null, objInstances = null, objLoc = null;
  if (haveObjects) {
    const verts = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, verts);
    gl.bufferData(gl.ARRAY_BUFFER, bytes(OBJ_VERTS), gl.STATIC_DRAW);
    const index = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, index);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, bytes(OBJ_INDEX), gl.STATIC_DRAW);
    objInstances = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, objInstances);
    gl.bufferData(gl.ARRAY_BUFFER, bytes(OBJ_INST), gl.STATIC_DRAW);

    objLoc = {};
    for (const n of ['aVert', 'aPos', 'aM0', 'aM1', 'aM2', 'aRgb', 'aRadius'])
      objLoc[n] = gl.getAttribLocation(pObj, n);

    objVao = gl.createVertexArray();
    gl.bindVertexArray(objVao);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, index);
    gl.bindBuffer(gl.ARRAY_BUFFER, verts);
    gl.enableVertexAttribArray(objLoc.aVert);
    gl.vertexAttribPointer(objLoc.aVert, 3, gl.FLOAT, false, 12, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, objInstances);
    for (const n of ['aPos', 'aM0', 'aM1', 'aM2', 'aRgb', 'aRadius']) {
      gl.enableVertexAttribArray(objLoc[n]);
      gl.vertexAttribDivisor(objLoc[n], 1);
    }
    gl.bindVertexArray(blankVao);
  }

  /* Point the instance attributes at one mesh's run of instances. Their layout
     never changes, only where in the buffer they start. */
  function atInstance(first) {
    const at = first * OBJ_STRIDE;
    gl.vertexAttribPointer(objLoc.aPos, 3, gl.FLOAT, false, OBJ_STRIDE, at);
    gl.vertexAttribPointer(objLoc.aM0, 3, gl.HALF_FLOAT, false, OBJ_STRIDE, at + 12);
    gl.vertexAttribPointer(objLoc.aM1, 3, gl.HALF_FLOAT, false, OBJ_STRIDE, at + 18);
    gl.vertexAttribPointer(objLoc.aM2, 3, gl.HALF_FLOAT, false, OBJ_STRIDE, at + 24);
    gl.vertexAttribPointer(objLoc.aRgb, 3, gl.UNSIGNED_BYTE, true, OBJ_STRIDE, at + 30);
    gl.vertexAttribPointer(objLoc.aRadius, 1, gl.UNSIGNED_BYTE, false,
                           OBJ_STRIDE, at + 33);
  }

  const span = [META.spanX, META.spanY];
  const range = [META.lo, META.hi - META.lo];
  const fog = 1 / (Math.max(META.spanX, META.spanY) * 1.35);

  /* Every program wants most of the same things, and asking for a uniform the
     compiler dropped is not an error, so they all go in together. */
  let sun = [0, 1, 0], eye = [0, 0, 0], clock = 0;
  function common(p) {
    gl.useProgram(p);
    if (p.u.uHeight) gl.uniform1i(p.u.uHeight, 0);
    if (p.u.uColour) gl.uniform1i(p.u.uColour, 1);
    if (p.u.uShadow) gl.uniform1i(p.u.uShadow, 2);
    if (p.u.uProp) gl.uniform1i(p.u.uProp, 3);
    if (p.u.uPropCeiling) gl.uniform1f(p.u.uPropCeiling, META.propCeiling);
    if (p.u.uTexSize) gl.uniform2i(p.u.uTexSize, META.texW, META.texH);
    if (p.u.uRange) gl.uniform2fv(p.u.uRange, range);
    if (p.u.uSpan) gl.uniform2fv(p.u.uSpan, span);
    if (p.u.uExag) gl.uniform1f(p.u.uExag, exag);
    if (p.u.uSun) gl.uniform3fv(p.u.uSun, sun);
    if (p.u.uEye) gl.uniform3fv(p.u.uEye, eye);
    if (p.u.uFog) gl.uniform1f(p.u.uFog, fog);
    if (p.u.uTime) gl.uniform1f(p.u.uTime, clock);
    if (p.u.uShallow) gl.uniform3fv(p.u.uShallow, LIQUID.shallow);
    if (p.u.uDeep) gl.uniform3fv(p.u.uDeep, LIQUID.deep);
    if (p.u.uShadowOn) gl.uniform1f(p.u.uShadowOn, ui.shadows.checked ? 1 : 0);
  }

  function bakeShadows() {
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.viewport(0, 0, META.texW, META.texH);
    gl.disable(gl.DEPTH_TEST);
    common(pShadow);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  reset();
  readUi();
  document.getElementById('wait').remove();

  let last = performance.now(), smooth = 0;
  function frame(now) {
    /* The first frame's timestamp can predate the clock reading taken just
       before the loop started, and a negative interval makes nonsense of both
       the movement keys and the frame rate. */
    const dt = Math.max(0, Math.min(0.1, (now - last) / 1000));
    last = now;
    clock = now / 1000;
    if (dt > 0) smooth = smooth ? smooth * 0.9 + 0.1 / dt : 1 / dt;
    fly(dt);

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(stage.clientWidth * dpr), h = Math.round(stage.clientHeight * dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }

    sun = sunVec();
    eye = eyeOf();
    if (shadowStale) { bakeShadows(); shadowStale = false; }

    /* The near plane has to follow the zoom: fixed at a metre it throws away
       most of the depth buffer when you are a kilometre up. */
    const near = Math.max(0.5, cam.dist * 0.004);
    const far = Math.max(META.spanX, META.spanY) * 30 + cam.dist;
    const proj = perspective(Math.PI / 4, w / Math.max(h, 1), near, far);
    const view = lookAt(eye, [cam.tx, cam.ty, cam.tz], [0, 1, 0]);
    const vp = mul(proj, view);

    gl.viewport(0, 0, w, h);
    gl.disable(gl.DEPTH_TEST);
    gl.depthMask(false);
    common(pSky);
    gl.uniformMatrix4fv(pSky.u.uInvVP, false, invert(vp));
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.depthMask(true);
    gl.clear(gl.DEPTH_BUFFER_BIT);

    const k = parseFloat(ui.detail.value);
    const gx = Math.max(1, Math.round(META.texW * k));
    const gy = Math.max(1, Math.round(META.texH * k));
    const verts = gx * gy * 6;

    common(pLand);
    gl.uniformMatrix4fv(pLand.u.uVP, false, vp);
    gl.uniform2i(pLand.u.uGrid, gx, gy);
    gl.drawArrays(gl.TRIANGLES, 0, verts);

    /* The objects go down on the land and before the water, so that a pier
       stands out of a lake and its piles still darken under it. */
    if (haveObjects && ui.objs.checked) {
      gl.bindVertexArray(objVao);
      gl.bindBuffer(gl.ARRAY_BUFFER, objInstances);
      common(pObj);
      gl.uniformMatrix4fv(pObj.u.uVP, false, vp);
      gl.uniform1f(pObj.u.uReach, parseFloat(ui.reach.value));
      for (const d of OBJ_DRAWS) {
        gl.uniform3fv(pObj.u.uCentre, d.centre);
        atInstance(d.start);
        gl.drawElementsInstanced(gl.TRIANGLES, d.elems, gl.UNSIGNED_INT,
                                 d.index * 4, d.count);
      }
      gl.bindVertexArray(blankVao);
    }

    if (ui.wet.checked) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      /* A shore is where the water surface and the land meet, which means the
         two are very nearly the same surface there and the depth buffer cannot
         tell them apart: at a distance the two grids are the same triangles a
         hair's breadth apart, and the sea speckles with land along every coast
         in the world. Biasing the water towards the eye by a fraction of a
         depth unit settles it, and the fragment shader still refuses to draw
         where the land really is above the surface, so nothing floods. */
      gl.enable(gl.POLYGON_OFFSET_FILL);
      gl.polygonOffset(-1.0, -2.0);
      if (pSea) {
        common(pSea);
        gl.uniformMatrix4fv(pSea.u.uVP, false, vp);
        gl.uniform1f(pSea.u.uSeaY, META.sea * exag);
        gl.uniform1f(pSea.u.uReach, Math.max(META.spanX, META.spanY) * 12);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
      }
      common(pWater);
      gl.uniformMatrix4fv(pWater.u.uVP, false, vp);
      gl.uniform2i(pWater.u.uGrid, gx, gy);
      gl.drawArrays(gl.TRIANGLES, 0, verts);
      gl.polygonOffset(0.0, 0.0);
      gl.disable(gl.POLYGON_OFFSET_FILL);
      gl.disable(gl.BLEND);
    }

    eyeOut.textContent = Math.round(eye[1]) + ' m';
    fpsOut.textContent = Math.round(smooth) + ' fps';
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

if (gl) {
  start().catch(e => {
    console.error(e);
    giveUp('The world would not draw.', String(e && e.message || e));
  });
}
</script>
"""


def _stats(pairs):
    return "".join("<span>%s</span><b>%s</b>" % (html.escape(k), html.escape(str(v)))
                   for k, v in pairs)


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _colour(image):
    """The ground colour, in whichever format keeps the page smallest."""
    for mime, opts in (("image/webp", dict(format="WEBP", quality=92, method=4)),
                       ("image/jpeg", dict(format="JPEG", quality=92)),
                       ("image/png", dict(format="PNG", optimize=True))):
        try:
            buf = io.BytesIO()
            image.save(buf, **opts)
        except Exception:
            continue
        return _b64(buf.getvalue()), mime
    raise RuntimeError("could not encode the colour texture")


def _height(array):
    """The height texture. PNG, and only PNG: this one is data, not a picture.

    Every byte of it is half of a number, so there is no such thing as a
    visually identical smaller version -- a lossy codec would put dents and
    ripples through the ground and move the water.
    """
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(array, "RGBA").save(buf, format="PNG", optimize=True)
    return _b64(buf.getvalue())


def _grey(array):
    """The prop height field as a PNG. One channel of data, three of nothing.

    Written as RGBA rather than as greyscale because that is what WebGL wants
    to be handed, and because over most of a world it is zero either way: PNG
    charges almost nothing for the three channels that never change.
    """
    from PIL import Image
    buf = io.BytesIO()
    rgba = np.zeros(array.shape + (4,), np.uint8)
    rgba[:, :, 0] = array
    rgba[:, :, 3] = 255
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return _b64(buf.getvalue())


def write_html(path, colour, height, meta, stats, title, subtitle,
               prop=None, objects=None):
    """colour: PIL Image. height: (h, w, 4) uint8 from terrain3d.

    ``prop`` is the separate prop-height field the shadow pass needs once the
    props have left the ground, and ``objects`` is what objects3d.collect
    returned: a mesh library, an instance buffer and a draw list.
    """
    b64, mime = _colour(colour)
    liquid = {
        "shallow": [c / 255.0 for pair in palette.LIQUID_RGB for c in pair[0]],
        "deep": [c / 255.0 for pair in palette.LIQUID_RGB for c in pair[1]],
    }
    o = objects or {}
    page = PAGE
    for token, value in (
            ("__TITLE__", html.escape(title)),
            ("__NAME__", html.escape(title)),
            ("__SUBTITLE__", html.escape(subtitle)),
            ("__STATS__", _stats(stats)),
            ("__META__", json.dumps(meta)),
            ("__LIQUID__", json.dumps(liquid)),
            ("__OBJ_DRAWS__", json.dumps(o.get("draws")) if o else "null"),
            ("__OBJ_VERTS__", _buffer(o.get("verts"))),
            ("__OBJ_INDEX__", _buffer(o.get("index"))),
            ("__OBJ_INST__", _buffer(o.get("instances"))),
            ("__COLOUR_MIME__", mime),
            ("__COLOUR__", "'%s'" % b64),
            ("__PROP__", "null" if prop is None else "'%s'" % _grey(prop)),
            ("__HEIGHT__", "'%s'" % _height(height))):
        page = page.replace(token, value)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


def _buffer(array):
    """A numpy array as a base64 JS string literal, or null."""
    if array is None:
        return "null"
    return "'%s'" % _b64(np.ascontiguousarray(array).tobytes())
