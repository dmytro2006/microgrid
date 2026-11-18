"""Animacje działające po stronie przeglądarki (bez przeładowań Streamlit).

- ``animated_map_html``  – mapa Krakowa (deck.gl + kafelki Carto): cząstki energii płyną
  wzdłuż połączeń z prędkością i gęstością ∝ mocy, węzły PCC zmieniają kolor wg obciążenia.
- ``flow_diagram_html``  – schemat jednego węzła PCC (SVG): animowane przepływy mocy,
  obracająca się turbina (prędkość ∝ wiatr), słońce (jasność ∝ PV), poziom SoC BESS,
  ładowarki zajęte przez pojazdy, wykres z kursorem czasu.

Oba widoki dostają cały szereg czasowy jako JSON i odtwarzają go lokalnie (play / pauza /
prędkość / suwak), więc animacja jest płynna niezależnie od serwera.
"""

from __future__ import annotations

import json

import numpy as np

from .config import GRID_SUPPLY_POINT
from .map_view import NODE_COLORS, OFFSETS
from .simulation import Results

MAX_FRAMES = 2016  # np. 7 dni × 288 kroków 5-min


def _stride(n: int) -> int:
    return max(1, int(np.ceil(n / MAX_FRAMES)))


def _series(r: Results, step: int) -> dict:
    ts = r.ts.iloc[::step]
    cols = ["load", "pv", "wind", "p_ev", "p_bess", "soc_bess", "p_grid", "price", "n_active", "n_queue"]
    return {c: np.round(ts[c].to_numpy(dtype=float), 2).tolist() for c in cols}


_DAYS_PL = ("pn", "wt", "śr", "cz", "pt", "sb", "nd")


def _times(r: Results, step: int) -> list[str]:
    return [f"{_DAYS_PL[t.weekday()]} {t:%d.%m %H:%M}" for t in r.ts["time"].iloc[::step]]


_COMMON_CSS = """
:root { --bg:#ffffff; --fg:#1d2733; --muted:#6b7785; --panel:#f4f6f9; --line:#dde3ea; --accent:#1c4c78; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0e1117; --fg:#e6e9ee; --muted:#9aa4b0; --panel:#1a1f2b; --line:#2b3242; --accent:#6fa8dc; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:13px/1.35 system-ui, -apple-system, Segoe UI, sans-serif; }
.bar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:8px 4px; }
.bar button { border:1px solid var(--line); background:var(--panel); color:var(--fg); border-radius:6px;
  padding:5px 12px; cursor:pointer; font-size:13px; min-width:44px; }
.bar button:hover { border-color:var(--accent); }
.bar input[type=range] { flex:1; min-width:160px; accent-color:var(--accent); }
.bar select { background:var(--panel); color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:4px; }
.clock { font-variant-numeric:tabular-nums; font-weight:600; min-width:120px; }
.muted { color:var(--muted); }
"""

_CONTROLS_JS = """
function makePlayer(n, render, opts) {
  opts = opts || {};
  let k = opts.start || 0, playing = !!opts.autoplay, speed = 1, acc = 0, last = performance.now();
  const btn = document.getElementById('play'), sl = document.getElementById('slider'),
        sp = document.getElementById('speed');
  sl.max = n - 1; sl.value = k;
  const setBtn = () => btn.textContent = playing ? '❚❚' : '▶';
  btn.onclick = () => { playing = !playing; if (k >= n - 1) k = 0; setBtn(); };
  sl.oninput = () => { k = +sl.value; render(k, 0); };
  sp.onchange = () => speed = +sp.value;
  setBtn();
  function frame(now) {
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    if (playing) { acc += dt * 12 * speed; while (acc >= 1) { acc -= 1; k = (k + 1) % n; } sl.value = k; }
    render(k, dt);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}
"""

_CONTROLS_HTML = """
<div class="bar">
  <button id="play" title="Odtwarzaj / pauza">▶</button>
  <span class="clock" id="clock"></span>
  <input type="range" id="slider" min="0" value="0">
  <label class="muted">tempo <select id="speed">
    <option value="0.25">¼×</option><option value="0.5">½×</option><option value="1" selected>1×</option>
    <option value="2">2×</option><option value="4">4×</option><option value="8">8×</option></select></label>
</div>
"""


# ------------------------------------------------------------------ mapa
def animated_map_html(results: list[Results], start_k: int = 0, autoplay: bool = True, height: int = 620) -> str:
    step = _stride(len(results[0].ts))
    sites = []
    for r in results:
        s = r.site
        sites.append({
            "name": s.name, "lat": s.lat, "lon": s.lon, "p_max": s.p_max_kw, "has_wind": s.wind_rated_kw > 0,
            "series": _series(r, step),
        })
    payload = {
        "times": _times(results[0], step), "sites": sites,
        "gpz": GRID_SUPPLY_POINT, "offsets": OFFSETS, "colors": NODE_COLORS,
    }
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://unpkg.com/deck.gl@9.0.38/dist.min.js"></script>
<style>{_COMMON_CSS}
#map {{ position:relative; height:{height - 60}px; border-radius:8px; overflow:hidden; border:1px solid var(--line); }}
#legend {{ position:absolute; z-index:2; left:10px; bottom:10px; background:rgba(255,255,255,.92); color:#1d2733;
  padding:8px 10px; border-radius:6px; font-size:12px; line-height:1.6; box-shadow:0 1px 4px rgba(0,0,0,.2); }}
#legend i {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; }}
#hud {{ position:absolute; z-index:2; right:10px; top:10px; background:rgba(255,255,255,.92); color:#1d2733;
  padding:8px 10px; border-radius:6px; font-size:12px; min-width:220px; box-shadow:0 1px 4px rgba(0,0,0,.2); }}
#hud table {{ border-collapse:collapse; width:100%; }} #hud td {{ padding:1px 4px; text-align:right; }}
#hud td:first-child {{ text-align:left; }}
</style></head><body>
{_CONTROLS_HTML}
<div id="map"><div id="legend"></div><div id="hud"></div></div>
<script>
const D = {json.dumps(payload, ensure_ascii=False)};
{_CONTROLS_JS}
const C = D.colors;
const legend = document.getElementById('legend');
legend.innerHTML = [['PV',C.PV],['Wiatr',C.Wiatr],['BESS',C.BESS],['Stacja EV',C['Stacja EV']],['GPZ',C.GPZ]]
  .map(([n,c]) => `<div><i style="background:rgb(${{c}})"></i>${{n}}</div>`).join('') +
  '<div class="muted" style="color:#6b7785">kolor PCC: zielony → czerwony = |P_grid|/P_max<br>cząstki: kierunek i moc przepływu</div>';

function loadColor(r) {{
  r = Math.max(0, Math.min(r, 1.2)) / 1.2;
  if (r < 0.6) {{ const f = r / 0.6; return [47 + f*203, 158 + f*18, 68 - f*60, 235]; }}
  const f = (r - 0.6) / 0.4; return [250 - f*49, 176 - f*134, 8 + f*34, 235];
}}
const gpz = [D.gpz.lon, D.gpz.lat];
const phase = {{}};   // faza przesunięcia cząstek dla każdego połączenia

function buildFrame(k, dt) {{
  const nodes = [{{pos: gpz, r: 230, c: C.GPZ.concat(235), name: D.gpz.name}}];
  const links = [], dots = [], labels = [{{pos: [gpz[0], gpz[1] + 0.0025], text: 'GPZ'}}];
  D.sites.forEach((s, si) => {{
    const S = s.series, pcc = [s.lon, s.lat], pg = S.p_grid[k];
    nodes.push({{pos: pcc, r: 170, c: loadColor(Math.abs(pg) / s.p_max), name: s.name}});
    labels.push({{pos: [s.lon, s.lat + 0.0058], text: s.name}});
    const comp = {{PV: S.pv[k], Wiatr: S.wind[k], BESS: S.p_bess[k], 'Stacja EV': S.p_ev[k]}};
    const flows = [];
    for (const [name, [dlat, dlon]] of Object.entries(D.offsets)) {{
      if (name === 'Wiatr' && !s.has_wind) continue;
      const pos = [s.lon + dlon, s.lat + dlat], p = comp[name];
      const extra = name === 'BESS' ? 40 + 60 * S.soc_bess[k] : 80;
      nodes.push({{pos, r: name === 'BESS' ? extra : 80, c: C[name].concat(225), name: s.name + ' – ' + name}});
      const out = (name === 'PV' || name === 'Wiatr' || p < 0);   // źródło → PCC
      flows.push({{id: si + name, a: out ? pos : pcc, b: out ? pcc : pos, p: Math.abs(p),
                   c: out ? [255, 196, 40] : [232, 89, 12]}});
    }}
    flows.push({{id: si + 'grid', a: pg >= 0 ? gpz : pcc, b: pg >= 0 ? pcc : gpz, p: Math.abs(pg),
                 c: pg >= 0 ? [60, 110, 200] : [40, 170, 90]}});
    for (const f of flows) {{
      links.push({{a: f.a, b: f.b, w: 1 + 7 * Math.min(f.p, 1.5 * s.p_max) / s.p_max,
                  c: f.p < 0.5 ? [150, 150, 150, 60] : f.c.concat(110)}});
      if (f.p < 0.5) continue;
      const v = 0.15 + 1.2 * Math.min(f.p, 1.5 * s.p_max) / s.p_max;      // prędkość cząstek
      phase[f.id] = ((phase[f.id] || 0) + dt * v) % 1;
      const n = Math.max(2, Math.round(3 + 10 * f.p / s.p_max));
      for (let i = 0; i < n; i++) {{
        const t = (phase[f.id] + i / n) % 1;
        dots.push({{pos: [f.a[0] + (f.b[0] - f.a[0]) * t, f.a[1] + (f.b[1] - f.a[1]) * t],
                   c: f.c.concat(240), r: 22 + 40 * Math.min(f.p, s.p_max) / s.p_max}});
      }}
    }}
  }});
  return {{nodes, links, dots, labels}};
}}

const deckgl = new deck.Deck({{
  parent: document.getElementById('map'),
  initialViewState: {{
    latitude: D.sites.reduce((a, s) => a + s.lat, 0) / D.sites.length,
    longitude: D.sites.reduce((a, s) => a + s.lon, 0) / D.sites.length,
    zoom: 12.3, pitch: 35, bearing: 0
  }},
  controller: true,
  getTooltip: ({{object}}) => object && object.name ? object.name : null,
}});
const tiles = new deck.TileLayer({{
  id: 'tiles', data: 'https://basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}@2x.png',
  minZoom: 0, maxZoom: 19, tileSize: 256,
  renderSubLayers: props => {{
    const {{west, south, east, north}} = props.tile.bbox;
    return new deck.BitmapLayer(props, {{data: null, image: props.data, bounds: [west, south, east, north]}});
  }}
}});
const hud = document.getElementById('hud'), clock = document.getElementById('clock');
let lastK = -1;
function render(k, dt) {{
  const F = buildFrame(k, dt);
  deckgl.setProps({{layers: [
    tiles,
    new deck.LineLayer({{id: 'links', data: F.links, getSourcePosition: d => d.a, getTargetPosition: d => d.b,
      getColor: d => d.c, getWidth: d => d.w, widthUnits: 'pixels'}}),
    new deck.ScatterplotLayer({{id: 'dots', data: F.dots, getPosition: d => d.pos, getRadius: d => d.r,
      getFillColor: d => d.c, radiusUnits: 'meters'}}),
    new deck.ScatterplotLayer({{id: 'nodes', data: F.nodes, getPosition: d => d.pos, getRadius: d => d.r,
      getFillColor: d => d.c, stroked: true, getLineColor: [255, 255, 255], lineWidthMinPixels: 1.5,
      pickable: true, radiusUnits: 'meters', updateTriggers: {{getFillColor: k, getRadius: k}}}}),
    new deck.TextLayer({{id: 'labels', data: F.labels, getPosition: d => d.pos, getText: d => d.text,
      getSize: 13, getColor: [30, 30, 30], fontWeight: 'bold', characterSet: 'auto',
      getAlignmentBaseline: 'bottom', outlineWidth: 3, outlineColor: [255, 255, 255], fontSettings: {{sdf: true}}}}),
  ]}});
  if (k !== lastK) {{
    lastK = k; clock.textContent = D.times[k];
    hud.innerHTML = '<b>' + D.times[k] + '</b><table><tr class="muted"><td></td><td>P_grid</td><td>EV</td><td>PV</td><td>SoC</td></tr>' +
      D.sites.map(s => {{ const S = s.series; return `<tr><td>${{s.name.split(' ')[0]}}</td><td>${{S.p_grid[k].toFixed(0)}}</td>` +
        `<td>${{S.p_ev[k].toFixed(0)}}</td><td>${{S.pv[k].toFixed(0)}}</td><td>${{(100*S.soc_bess[k]).toFixed(0)}}%</td></tr>`; }}).join('') +
      '</table><div class="muted" style="color:#6b7785">kW · cena ' + D.sites[0].series.price[k].toFixed(0) + ' PLN/MWh</div>';
  }}
}}
makePlayer(D.times.length, render, {{start: {int(start_k // step)}, autoplay: {str(autoplay).lower()}}});
</script></body></html>"""


# ------------------------------------------------------------------ schemat węzła
def flow_diagram_html(r: Results, height: int = 640) -> str:
    step = _stride(len(r.ts))
    s = r.site
    payload = {
        "times": _times(r, step), "S": _series(r, step),
        "site": {"name": s.name, "p_max": s.p_max_kw, "pv_kwp": s.pv_kwp, "wind_kw": s.wind_rated_kw,
                 "bess_kwh": s.bess_kwh, "n_ch": s.n_chargers},
    }
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_COMMON_CSS}
svg text {{ fill:var(--fg); font-size:13px; }}
svg .sub {{ fill:var(--muted); font-size:11px; }}
svg .val {{ font-weight:700; font-size:14px; font-variant-numeric:tabular-nums; }}
.box {{ fill:var(--panel); stroke:var(--line); stroke-width:1.5; }}
.wire {{ fill:none; stroke:var(--line); stroke-width:10; stroke-linecap:round; }}
.flow {{ fill:none; stroke-width:5; stroke-linecap:round; stroke-dasharray:2 16; }}
#chart {{ width:100%; height:150px; display:block; }}
</style></head><body>
{_CONTROLS_HTML}
<svg id="diag" viewBox="0 0 900 400" width="100%" style="max-height:{height - 220}px">
  <defs>
    <radialGradient id="sunG"><stop offset="0" stop-color="#ffd43b"/><stop offset="1" stop-color="#f59f00"/></radialGradient>
  </defs>
  <!-- przewody -->
  <path id="w_pv"   class="wire" d="M150,90 L450,200"/>
  <path id="w_wind" class="wire" d="M150,310 L450,200"/>
  <path id="w_grid" class="wire" d="M450,40 L450,200"/>
  <path id="w_bess" class="wire" d="M750,90 L450,200"/>
  <path id="w_ev"   class="wire" d="M750,310 L450,200"/>
  <path id="w_load" class="wire" d="M450,360 L450,200"/>
  <path id="f_pv"   class="flow" d="M150,90 L450,200"   stroke="#f2b705"/>
  <path id="f_wind" class="flow" d="M150,310 L450,200"  stroke="#3bb2d0"/>
  <path id="f_grid" class="flow" d="M450,40 L450,200"   stroke="#1c4c78"/>
  <path id="f_bess" class="flow" d="M750,90 L450,200"   stroke="#2f9e44"/>
  <path id="f_ev"   class="flow" d="M750,310 L450,200"  stroke="#e8590c"/>
  <path id="f_load" class="flow" d="M450,360 L450,200"  stroke="#868e96"/>

  <!-- PCC -->
  <circle id="pcc" cx="450" cy="200" r="34" stroke="#fff" stroke-width="3"/>
  <text x="450" y="196" text-anchor="middle" class="val" style="fill:#fff">PCC</text>
  <text id="pccPct" x="450" y="213" text-anchor="middle" style="fill:#fff;font-size:11px"></text>

  <!-- sieć -->
  <g transform="translate(450,28)">
    <path d="M-16,12 L0,-22 L16,12 M-11,0 L11,0 M-7,-10 L7,-10" stroke="#1c4c78" stroke-width="3" fill="none"/>
  </g>
  <text x="480" y="22" class="val" id="t_grid"></text><text x="480" y="40" class="sub">sieć (GPZ) · P_max <tspan id="t_pmax"></tspan> kW</text>

  <!-- PV + słońce -->
  <g transform="translate(95,70)">
    <circle id="sun" cx="0" cy="-22" r="16" fill="url(#sunG)"/>
    <rect x="-34" y="4" width="68" height="34" rx="3" fill="#264b7a" transform="skewX(-15)"/>
    <path d="M-30,15 H32 M-30,27 H30 M-12,4 V38 M8,4 V38" stroke="#7fa7d9" stroke-width="1.2" transform="skewX(-15)"/>
  </g>
  <text x="40" y="135" class="val" id="t_pv"></text><text x="40" y="152" class="sub">PV · <tspan id="t_pvkwp"></tspan> kWp</text>

  <!-- turbina -->
  <g transform="translate(95,280)">
    <path d="M0,0 L-4,70 L4,70 Z" fill="#adb5bd"/>
    <g id="rotor"><circle r="5" fill="#495057"/>
      <path d="M0,0 L-3,-42 L3,-42 Z" fill="#dee2e6" stroke="#868e96"/>
      <path d="M0,0 L-3,-42 L3,-42 Z" fill="#dee2e6" stroke="#868e96" transform="rotate(120)"/>
      <path d="M0,0 L-3,-42 L3,-42 Z" fill="#dee2e6" stroke="#868e96" transform="rotate(240)"/></g>
  </g>
  <text x="140" y="345" class="val" id="t_wind"></text><text x="140" y="362" class="sub">wiatr · <tspan id="t_windkw"></tspan> kW</text>

  <!-- BESS -->
  <g transform="translate(790,55)">
    <rect x="-28" y="0" width="56" height="80" rx="6" class="box"/>
    <rect x="-10" y="-7" width="20" height="7" rx="2" fill="var(--line)"/>
    <rect id="soc" x="-22" y="6" width="44" height="68" rx="3" fill="#2f9e44"/>
  </g>
  <text x="680" y="160" class="val" id="t_bess"></text><text x="680" y="177" class="sub">BESS · <tspan id="t_soc"></tspan></text>

  <!-- stacja EV -->
  <g id="chargers" transform="translate(690,280)"></g>
  <text x="690" y="370" class="val" id="t_ev"></text><text x="690" y="387" class="sub" id="t_evq"></text>

  <!-- odbiory -->
  <g transform="translate(450,372)">
    <path d="M-18,0 L0,-14 L18,0 V18 H-18 Z" fill="#868e96"/>
  </g>
  <text x="480" y="378" class="val" id="t_load"></text><text x="480" y="395" class="sub">odbiory bazowe</text>
  <text x="20" y="22" class="val">{s.name}</text>
  <text x="20" y="40" class="sub" id="t_price"></text>
</svg>
<canvas id="chart"></canvas>
<script>
const D = {json.dumps(payload, ensure_ascii=False)};
{_CONTROLS_JS}
const S = D.S, site = D.site, N = D.times.length;
const $ = id => document.getElementById(id);
$('t_pmax').textContent = site.p_max; $('t_pvkwp').textContent = site.pv_kwp; $('t_windkw').textContent = site.wind_kw;
const nCh = Math.min(site.n_ch, 16), cols = 8;
const chg = $('chargers');
for (let i = 0; i < nCh; i++) {{
  const x = (i % cols) * 24 - 84, y = Math.floor(i / cols) * 34 - 16;
  chg.insertAdjacentHTML('beforeend',
    `<g transform="translate(${{x}},${{y}})"><rect x="-8" y="-12" width="16" height="26" rx="3" class="box"/>` +
    `<rect id="car${{i}}" x="-6" y="-9" width="12" height="9" rx="2" fill="var(--line)"/></g>`);
}}
function loadColor(r) {{
  r = Math.max(0, Math.min(r, 1.2)) / 1.2;
  let c; if (r < 0.6) {{ const f = r/0.6; c = [47+f*203, 158+f*18, 68-f*60]; }}
  else {{ const f = (r-0.6)/0.4; c = [250-f*49, 176-f*134, 8+f*34]; }}
  return `rgb(${{c.map(Math.round)}})`;
}}
// definicja przepływów: znak dodatni = w stronę PCC
const flows = {{
  pv: k => S.pv[k], wind: k => S.wind[k], grid: k => S.p_grid[k],
  bess: k => -S.p_bess[k], ev: k => -S.p_ev[k], load: k => -S.load[k],
}};
const off = {{}}; let rot = 0;
const fmt = v => (Math.abs(v) < 0.05 ? 0 : v).toFixed(1) + ' kW';

// --- wykres (canvas) ---
const cv = $('chart'), ctx = cv.getContext('2d');
function drawChart(k) {{
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== W * dpr) {{ cv.width = W * dpr; cv.height = H * dpr; }}
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.body), fg = css.getPropertyValue('--muted');
  const keys = [['p_grid', '#1c4c78', 2], ['p_ev', '#e8590c', 1.2], ['pv', '#f2b705', 1.2], ['p_bess', '#2f9e44', 1.2]];
  let lo = 0, hi = site.p_max;
  for (const [key] of keys) for (const v of S[key]) {{ if (v < lo) lo = v; if (v > hi) hi = v; }}
  const pad = 22, x = i => pad + (W - pad - 6) * i / (N - 1), y = v => 6 + (H - 24) * (1 - (v - lo) / (hi - lo));
  ctx.strokeStyle = '#c92a2a'; ctx.setLineDash([5, 4]); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(pad, y(site.p_max)); ctx.lineTo(W - 6, y(site.p_max)); ctx.stroke(); ctx.setLineDash([]);
  ctx.strokeStyle = fg; ctx.globalAlpha = 0.4; ctx.beginPath(); ctx.moveTo(pad, y(0)); ctx.lineTo(W - 6, y(0)); ctx.stroke();
  ctx.globalAlpha = 1;
  for (const [key, col, lw] of keys) {{
    ctx.strokeStyle = col; ctx.lineWidth = lw; ctx.beginPath();
    S[key].forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))); ctx.stroke();
  }}
  ctx.fillStyle = fg; ctx.font = '11px system-ui';
  ctx.fillText('P_grid', pad + 4, H - 4); ctx.fillStyle = '#e8590c'; ctx.fillText('EV', pad + 50, H - 4);
  ctx.fillStyle = '#f2b705'; ctx.fillText('PV', pad + 74, H - 4); ctx.fillStyle = '#2f9e44'; ctx.fillText('BESS', pad + 98, H - 4);
  ctx.fillStyle = '#c92a2a'; ctx.fillText('P_max', pad + 136, H - 4);
  ctx.strokeStyle = css.getPropertyValue('--fg'); ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(x(k), 4); ctx.lineTo(x(k), H - 18); ctx.stroke();
}}

let lastK = -1;
function render(k, dt) {{
  for (const [name, f] of Object.entries(flows)) {{
    const p = f(k), el = $('f_' + name), mag = Math.min(Math.abs(p) / site.p_max, 1.5);
    off[name] = ((off[name] || 0) + dt * (20 + 220 * mag) * Math.sign(p)) % 1000;
    el.style.strokeDashoffset = -off[name];
    el.style.opacity = Math.abs(p) < 0.5 ? 0 : 1;
    el.style.strokeWidth = 3 + 7 * mag;
  }}
  rot = (rot + dt * 720 * Math.min(S.wind[k] / Math.max(site.wind_kw, 1), 1)) % 360;
  $('rotor').setAttribute('transform', `rotate(${{rot}})`);
  if (k === lastK) return; lastK = k;
  $('clock').textContent = D.times[k];
  const ratio = Math.abs(S.p_grid[k]) / site.p_max;
  $('pcc').setAttribute('fill', loadColor(ratio)); $('pccPct').textContent = Math.round(100 * ratio) + '% P_max';
  const pvr = site.pv_kwp ? S.pv[k] / site.pv_kwp : 0;
  $('sun').setAttribute('opacity', 0.15 + 0.85 * Math.min(1, pvr * 1.4));
  $('sun').setAttribute('r', 10 + 10 * Math.min(1, pvr * 1.4));
  const h = 68 * S.soc_bess[k]; $('soc').setAttribute('y', 6 + 68 - h); $('soc').setAttribute('height', h);
  $('t_grid').textContent = (S.p_grid[k] >= 0 ? 'import ' : 'eksport ') + fmt(Math.abs(S.p_grid[k]));
  $('t_pv').textContent = fmt(S.pv[k]); $('t_wind').textContent = fmt(S.wind[k]);
  $('t_bess').textContent = (S.p_bess[k] > 0.05 ? 'ładowanie ' : S.p_bess[k] < -0.05 ? 'rozładowanie ' : '') + fmt(Math.abs(S.p_bess[k]));
  $('t_soc').textContent = 'SoC ' + Math.round(100 * S.soc_bess[k]) + '% z ' + site.bess_kwh + ' kWh';
  $('t_ev').textContent = (S.p_ev[k] < -0.05 ? 'V2G ' : '') + fmt(Math.abs(S.p_ev[k]));
  $('t_evq').textContent = `stacja EV · ${{S.n_active[k]}}/${{site.n_ch}} zajęte · kolejka ${{S.n_queue[k]}}`;
  $('t_load').textContent = fmt(S.load[k]);
  $('t_price').textContent = 'cena energii: ' + S.price[k].toFixed(0) + ' PLN/MWh';
  const busy = Math.round(S.n_active[k] * nCh / site.n_ch);
  for (let i = 0; i < nCh; i++) $('car' + i).setAttribute('fill', i < busy ? (S.p_ev[k] < 0 ? '#2f9e44' : '#e8590c') : 'var(--line)');
  drawChart(k);
}}
window.addEventListener('resize', () => {{ lastK = -1; }});
makePlayer(N, render, {{autoplay: true}});
</script></body></html>"""
