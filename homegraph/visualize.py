#!/usr/bin/env python3
"""Render a graph to a self-contained HTML page.

No D3, no CDN, no network. The plan named D3, but pulling ~280 KB from a CDN
would make the page useless offline and add the project's first runtime
dependency to a package that has none. The layout is computed here in Python
and the page draws it on a canvas -- so what ships is one file that opens in a
browser, on a plane, in five years.

Layout is force-directed with grid-approximated repulsion: each node repels
only the neighbours in its own and adjacent cells, which turns the O(n²) part
into roughly O(n). Deterministic -- the seed is fixed and no randomness is read
from the clock, so the same graph produces the same picture twice. That matters
for a visualisation you are going to compare against last week's.

Canvas rather than SVG. At 5 000 nodes an SVG means 5 000 DOM elements and the
browser spends its time in layout rather than drawing.
"""
from __future__ import annotations

import html
import json
import math
import os
import random

from .store import Store, provenance_note

MODEL_COLOURS = {
    "m1": "#8a5a2b", "m2": "#6a9c78", "m3": "#4a6fa5",
    "m4": "#9a7aa0", "m5": "#b5544c", "code": "#6b6862",
}
# What each model is, for a reader. The identifier stays `m3` everywhere it
# is typed -- `--model m3=...`, the query language, the MCP tool -- and the
# legend shows the name alone with the code on hover, so the picture does not
# invent a second vocabulary for the same thing.
#
# Presentation only, and it lives beside the colours because that is already
# the one place the page's per-model appearance is decided. It is not a second
# definition of what a model holds: nothing reads these to make a decision.
MODEL_NAMES = {
    "m1": "dokumenter", "m2": "bilder", "m3": "markdown",
    "m4": "alt annet", "m5": "mesh", "code": "kode",
}
DEFAULT_COLOUR = "#8b8680"

# How many hits the results list names before it says it stopped. Same rule as
# the exclusion report: a list that is cut without saying so reads as the
# whole answer.
MAX_HITS = 200


def _layout(nodes, edges, iterations=180, seed=20260722, width=1600.0):
    """Force-directed positions. Returns [(x, y), ...] parallel to `nodes`.

    Iterations scale down with size. Cost per pass is roughly linear in node
    count, so a fixed count that is comfortable at 500 nodes takes six seconds
    at 5 000 -- measured, not assumed. The budget below keeps any graph under
    the three-second ceiling CP-6 sets, at the price of a looser layout on the
    largest ones. Stated here rather than discovered as "why is this slow".

    The budget leaves real margin (about 2.1s at 5 000 nodes, not 2.99s): a
    timing gate whose verdict depends on what else the machine is doing tells
    you about the machine.
    """
    n = len(nodes)
    if n == 0:
        return []
    if n > 1500:
        iterations = max(24, min(iterations, int(105_000 / n)))
    # nosec B311 -- this seeds a picture, not a key. The point of
    # random here is that it is REPRODUCIBLE: a fixed seed so the
    # same graph draws the same layout twice, which CP-6 asserts.
    rng = random.Random(seed)  # noqa: S311
    # Seeded on a ring per model so runs start from the same place and models
    # begin visually separated rather than converging from a random mush.
    models = sorted({node["model"] for node in nodes})
    slot = {m: i for i, m in enumerate(models)}
    pos = []
    for i, node in enumerate(nodes):
        # `max(..., 1)` guarded a division that cannot see zero: `_layout`
        # returns early when there are no nodes, and every node carries a
        # model, so `models` is non-empty here.
        angle = (2 * math.pi * (slot[node["model"]] + rng.random())
                 / len(models))
        radius = width * 0.35 * (0.35 + 0.65 * rng.random())
        pos.append([math.cos(angle) * radius, math.sin(angle) * radius])

    adjacency = [[] for _ in range(n)]
    for a, b in edges:
        adjacency[a].append(b)
        adjacency[b].append(a)

    k = width / math.sqrt(n)          # n == 0 returned above
    cell = k * 2.0
    temp = width * 0.1

    # Flat parallel lists rather than a list of pairs. The repulsion loop is
    # the hot spot -- roughly 36 partners per node per pass -- and `px[j]`
    # costs one indirection where `pos[j][0]` costs two. Worth about a third of
    # the runtime, which is the difference between 3.2s and 2.1s at 5 000 nodes.
    px = [p[0] for p in pos]
    py = [p[1] for p in pos]
    k2 = k * k
    inv_cell = 1.0 / cell

    for _step in range(iterations):
        grid: dict[tuple[int, int], list[int]] = {}
        for i in range(n):
            key = (int(px[i] * inv_cell), int(py[i] * inv_cell))
            bucket = grid.get(key)
            if bucket is None:
                grid[key] = [i]
            else:
                bucket.append(i)

        dx_acc = [0.0] * n
        dy_acc = [0.0] * n
        for (cx, cy), members in grid.items():
            near = []
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    bucket = grid.get((cx + ox, cy + oy))
                    if bucket:
                        near.extend(bucket)
            for i in members:
                xi = px[i]
                yi = py[i]
                ax = 0.0
                ay = 0.0
                for j in near:
                    dx = xi - px[j]
                    dy = yi - py[j]
                    d2 = dx * dx + dy * dy
                    if d2 < 1e-9:
                        continue
                    force = k2 / d2
                    ax += dx * force
                    ay += dy * force
                dx_acc[i] = ax
                dy_acc[i] = ay

        for i in range(n):
            xi = px[i]
            yi = py[i]
            ax = dx_acc[i]
            ay = dy_acc[i]
            for j in adjacency[i]:
                dx = xi - px[j]
                dy = yi - py[j]
                d = math.hypot(dx, dy) or 1e-6
                force = d * d / k
                ax -= dx / d * force
                ay -= dy / d * force
            dx_acc[i] = ax
            dy_acc[i] = ay

        for i in range(n):
            dx = dx_acc[i]
            dy = dy_acc[i]
            d = math.hypot(dx, dy) or 1e-6
            limit = min(d, temp) / d
            px[i] += dx * limit
            py[i] += dy * limit
        temp = max(temp * 0.97, width * 0.0015)

    pos = list(zip(px, py))
    return [(round(x, 1), round(y, 1)) for x, y in pos]


def collect(model_paths, limit_per_model=2000, min_degree=0):
    """Read nodes and edges out of the stores. Never mutates them."""
    nodes, index, edges, missing = [], {}, [], []
    for model, path in sorted(model_paths.items()):
        if not path or not os.path.exists(path):
            missing.append(model)
            continue
        with Store(path) as s:
            rows = s.db.execute(
                "SELECT node_key, title, kind, subtype FROM nodes "
                "ORDER BY node_key LIMIT ?", (limit_per_model,)).fetchall()
            keys = set()
            for r in rows:
                key = "%s::%s" % (model, r["node_key"])
                index[key] = len(nodes)
                keys.add(r["node_key"])
                nodes.append({
                    "key": key, "model": model,
                    "title": (r["title"] or r["node_key"])[:80],
                    "kind": r["kind"] or "", "subtype": r["subtype"] or "",
                    # Whether this node stands for a file on disk. Tags and
                    # broken-link stubs do not; sections do, through their
                    # parent file. Decided here, in Python, so it is under
                    # test -- the page only turns the flag into a URL.
                    "link": bool(r["node_key"].startswith("/")),
                })
            for r in s.db.execute(
                    "SELECT s.node_key a, d.node_key b, e.rel r, "
                    "e.method m, e.confidence c FROM edges e "
                    "JOIN nodes s ON s.id = e.src JOIN nodes d ON d.id = e.dst"):
                if r["a"] in keys and r["b"] in keys:
                    edges.append((index["%s::%s" % (model, r["a"])],
                                  index["%s::%s" % (model, r["b"])],
                                  r["r"], r["m"], r["c"]))
    if min_degree:
        degree = [0] * len(nodes)
        for a, b, *_ in edges:
            degree[a] += 1
            degree[b] += 1
        keep = {i for i, d in enumerate(degree) if d >= min_degree}
        remap = {old: new for new, old in enumerate(sorted(keep))}
        nodes = [nodes[i] for i in sorted(keep)]
        edges = [(remap[a], remap[b], r, m, c) for a, b, r, m, c in edges
                 if a in remap and b in remap]
    return nodes, edges, missing


def render(model_paths, out_path, limit_per_model=2000, min_degree=0,
           iterations=180, title="homegraph"):
    nodes, edges, missing = collect(model_paths, limit_per_model, min_degree)
    positions = _layout(nodes, [(a, b) for a, b, *_ in edges],
                        iterations=iterations)
    # The same warning `md backlinks`, `mesh neighbors`, the MCP server and
    # `query` produce, from the same function. The picture was the one read
    # path that handed back edges without it: a relation guessed from a
    # filename collision was drawn identically to one the text states, and a
    # drawing is the form in which people trust a graph most.
    note = provenance_note([{"method": m, "confidence": c}
                            for _, _, _, m, c in edges])
    payload = {
        "nodes": [[n["key"], n["model"], n["title"], n["kind"], n["subtype"],
                   positions[i][0], positions[i][1], 1 if n["link"] else 0]
                  for i, n in enumerate(nodes)],
        "edges": [[a, b, r, m, c] for a, b, r, m, c in edges],
        "colours": MODEL_COLOURS,
        "names": MODEL_NAMES,
        "maxhits": MAX_HITS,
        "missing": missing,
        "derived": sum(1 for *_, c in edges if c is not None and c < 1.0),
        "note": note or "",
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(_PAGE.replace("__TITLE__", html.escape(title))
                 .replace("__DATA__", json.dumps(payload, separators=(",", ":"))))
    return {"nodes": len(nodes), "edges": len(edges), "missing": missing,
            "derived": payload["derived"], "note": note or "",
            "path": out_path,
            "bytes": os.path.getsize(out_path)}


_PAGE = """<!DOCTYPE html>
<html lang="no"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
 :root{--bg:#fbfaf8;--fg:#1c1b19;--muted:#6b6862;--rule:#e3e0da;--card:#fff}
 @media (prefers-color-scheme:dark){:root{--bg:#16151a;--fg:#e6e3dd;
   --muted:#9b968d;--rule:#2e2c33;--card:#1d1c22}}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);overflow:hidden;
   font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
 canvas{display:block;cursor:grab}
 canvas.drag{cursor:grabbing}
 #panel{position:fixed;top:12px;left:12px;background:var(--card);
   border:1px solid var(--rule);border-radius:8px;padding:12px 14px;
   max-width:290px;box-shadow:0 2px 14px rgba(0,0,0,.10)}
 #panel h1{font-size:15px;margin:0 0 2px}
 #panel .sub{color:var(--muted);font-size:12px;margin:0 0 10px}
 #q{width:100%;padding:6px 8px;border:1px solid var(--rule);border-radius:5px;
   background:var(--bg);color:var(--fg);font:inherit;margin-bottom:8px}
 label{display:flex;align-items:center;gap:7px;font-size:13px;padding:2px 0;
   cursor:pointer}
 .dot{width:11px;height:11px;border-radius:50%;flex:0 0 auto}
 .n{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums}
 #tip{position:fixed;pointer-events:none;background:var(--card);
   border:1px solid var(--rule);border-radius:6px;padding:6px 9px;
   font-size:12px;max-width:420px;display:none;
   box-shadow:0 2px 10px rgba(0,0,0,.18);word-break:break-all}
 #hint{position:fixed;bottom:12px;left:12px;color:var(--muted);font-size:12px}
 #warn{color:#b5544c;font-size:12px;margin-top:8px}
 #hits{position:fixed;top:12px;right:12px;background:var(--card);
   border:1px solid var(--rule);border-radius:8px;padding:12px 14px;
   width:360px;max-height:calc(100vh - 24px);display:none;
   flex-direction:column;box-shadow:0 2px 14px rgba(0,0,0,.10)}
 #hits h2{font-size:13px;margin:0 0 8px;font-weight:600}
 #hits .cut{color:var(--muted);font-size:12px;margin:0 0 8px}
 #hitlist{overflow-y:auto;margin:0;padding:0;list-style:none}
 #hitlist li{padding:3px 0;border-top:1px solid var(--rule);font-size:12px}
 #hitlist li:first-child{border-top:none}
 #hitlist a{color:var(--fg);text-decoration:none;display:block;
   word-break:break-all;line-height:1.35}
 #hitlist a:hover{text-decoration:underline}
 #hitlist .dir{color:var(--muted)}
 #hitlist .m{color:var(--muted);font-size:11px}
 #hitlist .none{color:var(--muted);cursor:default}
</style></head><body>
<canvas id="c"></canvas>
<div id="panel">
  <h1>__TITLE__</h1>
  <p class="sub" id="stats"></p>
  <input id="q" placeholder="søk i titler…" autocomplete="off">
  <div id="legend"></div>
  <div id="warn"></div>
</div>
<div id="hits">
  <h2 id="hitcount"></h2>
  <p class="cut" id="hitcut"></p>
  <ul id="hitlist"></ul>
</div>
<div id="tip"></div>
<div id="hint">dra for å panorere · rull for å zoome · hold over en node</div>
<script>
const D = __DATA__;
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
const tip = document.getElementById('tip');
let view = {x:0, y:0, k:1}, hidden = new Set(), query = '', hover = -1;

const counts = {};
for (const n of D.nodes) counts[n[1]] = (counts[n[1]] || 0) + 1;
document.getElementById('stats').textContent =
  D.nodes.length.toLocaleString('no') + ' noder · ' +
  D.edges.length.toLocaleString('no') + ' kanter' +
  (D.derived ? ' · ' + D.derived.toLocaleString('no') +
   ' utledet (stiplet)' : '');
const warnings = [];
if (D.missing.length) warnings.push('DELVIS: ' + D.missing.join(', ') +
  ' mangler.');
if (D.note) warnings.push('DELVIS: ' + D.note);
if (warnings.length) document.getElementById('warn').textContent =
  warnings.join('  ');

const legend = document.getElementById('legend');
for (const m of Object.keys(counts).sort()) {
  const col = D.colours[m] || '#8b8680';
  const l = document.createElement('label');
  // Built with DOM calls, not innerHTML: model keys come from the command
  // line, and a name is not a place to start trusting input.
  const cb = document.createElement('input');
  cb.type = 'checkbox'; cb.checked = true;
  const dot = document.createElement('span');
  dot.className = 'dot'; dot.style.background = col;
  const nm = document.createElement('span');
  nm.textContent = (D.names && D.names[m]) || m;
  const cnt = document.createElement('span');
  cnt.className = 'n'; cnt.textContent = counts[m].toLocaleString('no');
  // The identifier stays reachable on hover. `--model m3=...`, the query
  // language and the MCP tool all use it, so hiding it entirely would leave
  // the picture speaking a vocabulary the commands do not.
  l.title = m;
  l.append(cb, dot, nm, cnt);
  cb.onchange = e => {
    // The list follows the legend. Hiding a model and leaving its files in
    // the results would make the two halves of the page disagree about what
    // is being looked at.
    e.target.checked ? hidden.delete(m) : hidden.add(m);
    draw(); updateHits();
  };
  legend.appendChild(l);
}
// The results list. The picture answers "where is it"; this answers "which
// files are they", which is the question you actually take somewhere else.
//
// Built with DOM calls throughout. A filename is arbitrary bytes off a disk
// -- `<img src=x onerror=...>.md` is a legal name -- and innerHTML here
// would make one badly-named file in your home directory into script running
// in your browser. textContent and setAttribute cannot do that.
const hitsBox = document.getElementById('hits');
const hitList = document.getElementById('hitlist');
const hitCount = document.getElementById('hitcount');
const hitCut = document.getElementById('hitcut');

function fileOf(key) {
  // `m3::/dir/a.md#2` -> `/dir/a.md`. Sections point
  // at the file they are part of; there is nothing else to open.
  const i = key.indexOf('::');
  const p = i < 0 ? key : key.slice(i + 2);
  const h = p.indexOf('#');
  return h < 0 ? p : p.slice(0, h);
}

function linkFor(key) {
  // The whole URL, in one place, so CP-9 can extract this function and run
  // it. The first version of that gate built the URL itself and then
  // compared it to its own construction -- two implementations agreeing
  // with each other, which is the trap this file's own docstrings warn
  // about. The mutation that dropped encodeURI walked straight through it.
  return 'file://' + encodeURI(fileOf(key));
}

function updateHits() {
  hitList.textContent = '';
  if (!query) { hitsBox.style.display = 'none'; return; }

  const seen = new Set(), rows = [];
  for (let i = 0; i < D.nodes.length; i++) {
    const n = D.nodes[i];
    if (hidden.has(n[1])) continue;
    if (!n[2].toLowerCase().includes(query)) continue;
    const key = n[7] ? fileOf(n[0]) : null;
    // One row per file, not per section: a note with forty sections is one
    // thing to open.
    const id = key || n[0];
    if (seen.has(id)) continue;
    seen.add(id);
    rows.push([id, key, n[1], n[0]]);
  }
  rows.sort((a, b) => a[0].localeCompare(b[0], 'no'));

  hitsBox.style.display = 'flex';
  hitCount.textContent = rows.length.toLocaleString('no') + ' treff';
  // No silent cap. The list stops at maxhits and says that it did -- a cut
  // list that does not admit it reads as the whole answer.
  hitCut.textContent = rows.length > D.maxhits
    ? 'viser de ' + D.maxhits + ' første' : '';
  if (!rows.length) {
    const li = document.createElement('li');
    li.className = 'none'; li.textContent = 'ingen treff';
    hitList.appendChild(li);
    return;
  }
  for (const [id, key, model, nodeKey] of rows.slice(0, D.maxhits)) {
    const li = document.createElement('li');
    const cut = id.lastIndexOf('/');
    if (key) {
      const a = document.createElement('a');
      a.setAttribute('href', linkFor(nodeKey));
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener');
      if (cut > 0) {
        const d = document.createElement('span');
        d.className = 'dir'; d.textContent = id.slice(0, cut + 1);
        a.appendChild(d);
      }
      a.appendChild(document.createTextNode(id.slice(cut + 1)));
      li.appendChild(a);
    } else {
      // Tags and unresolved wikilinks are real nodes with nothing to open.
      // Listed anyway, without a link, rather than dropped: a search that
      // silently omits part of what it matched is the failure this whole
      // package keeps finding in itself.
      const span = document.createElement('span');
      span.className = 'none'; span.textContent = id;
      li.appendChild(span);
    }
    const m = document.createElement('span');
    m.className = 'm';
    m.textContent = '  ' + ((D.names && D.names[model]) || model);
    li.appendChild(m);
    hitList.appendChild(li);
  }
}

document.getElementById('q').oninput = e => {
  query = e.target.value.toLowerCase(); draw(); updateHits();
};

function fit() {
  cv.width = innerWidth * devicePixelRatio;
  cv.height = innerHeight * devicePixelRatio;
  cv.style.width = innerWidth + 'px'; cv.style.height = innerHeight + 'px';
  if (D.nodes.length) {
    let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
    for (const n of D.nodes){x0=Math.min(x0,n[5]);y0=Math.min(y0,n[6]);
      x1=Math.max(x1,n[5]);y1=Math.max(y1,n[6]);}
    const pad = 60;
    view.k = Math.min((innerWidth-pad*2)/Math.max(x1-x0,1),
                      (innerHeight-pad*2)/Math.max(y1-y0,1));
    view.x = innerWidth/2 - (x0+x1)/2*view.k;
    view.y = innerHeight/2 - (y0+y1)/2*view.k;
  }
  draw();
}
const sx = n => n[5]*view.k + view.x, sy = n => n[6]*view.k + view.y;

function draw() {
  const r = devicePixelRatio;
  ctx.setTransform(r,0,0,r,0,0);
  ctx.clearRect(0,0,innerWidth,innerHeight);
  const vis = i => !hidden.has(D.nodes[i][1]);

  // Two passes, because a guessed relation must not be drawn as a stated
  // one. Solid = the data says so; dashed and dimmer = inferred, and the
  // inference can be wrong. Same rule the text answers carry.
  const rule = getComputedStyle(document.body).getPropertyValue('--rule');
  ctx.lineWidth = 0.6; ctx.strokeStyle = rule;
  ctx.setLineDash([]); ctx.globalAlpha = 0.75; ctx.beginPath();
  for (const [a,b,,,c] of D.edges) {
    if (!vis(a) || !vis(b)) continue;
    if (c !== null && c < 1) continue;
    ctx.moveTo(sx(D.nodes[a]), sy(D.nodes[a]));
    ctx.lineTo(sx(D.nodes[b]), sy(D.nodes[b]));
  }
  ctx.stroke();

  ctx.setLineDash([3, 3]); ctx.globalAlpha = 0.45; ctx.beginPath();
  for (const [a,b,,,c] of D.edges) {
    if (!vis(a) || !vis(b)) continue;
    if (c === null || c >= 1) continue;
    ctx.moveTo(sx(D.nodes[a]), sy(D.nodes[a]));
    ctx.lineTo(sx(D.nodes[b]), sy(D.nodes[b]));
  }
  ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;

  for (let i = 0; i < D.nodes.length; i++) {
    if (!vis(i)) continue;
    const n = D.nodes[i];
    const match = query && n[2].toLowerCase().includes(query);
    ctx.globalAlpha = (!query || match) ? 1 : 0.12;
    ctx.beginPath();
    ctx.arc(sx(n), sy(n), match ? 5 : (i === hover ? 5 : 2.6), 0, 6.283);
    ctx.fillStyle = match ? '#d94f3d' : (D.colours[n[1]] || '#8b8680');
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

let drag = null;
cv.onmousedown = e => { drag = {x:e.clientX, y:e.clientY, vx:view.x, vy:view.y};
  cv.classList.add('drag'); };
addEventListener('mouseup', () => { drag = null; cv.classList.remove('drag'); });
cv.onmousemove = e => {
  if (drag) { view.x = drag.vx + e.clientX - drag.x;
              view.y = drag.vy + e.clientY - drag.y; draw(); return; }
  let best = -1, bd = 100;
  for (let i = 0; i < D.nodes.length; i++) {
    if (hidden.has(D.nodes[i][1])) continue;
    const dx = sx(D.nodes[i]) - e.clientX, dy = sy(D.nodes[i]) - e.clientY;
    const d = dx*dx + dy*dy;
    if (d < bd) { bd = d; best = i; }
  }
  if (best !== hover) { hover = best; draw(); }
  if (best >= 0) {
    const n = D.nodes[best];
    tip.style.display = 'block';
    tip.style.left = Math.min(e.clientX + 14, innerWidth - 440) + 'px';
    tip.style.top = (e.clientY + 14) + 'px';
    // DOM calls, not an HTML string. The old version escaped `<` in two of
    // the four fields it interpolated and nothing else -- and every one of
    // them comes from a filename on disk, which is arbitrary bytes. Escaping
    // by hand is a list you have to keep complete forever; not building HTML
    // at all is a property.
    tip.textContent = '';
    const b = document.createElement('b');
    b.textContent = n[2];
    const meta = document.createElement('div');
    meta.textContent = ((D.names && D.names[n[1]]) || n[1]) + ' · ' + n[3] +
      (n[4] ? ' · ' + n[4] : '');
    const key = document.createElement('div');
    key.style.color = 'var(--muted)';
    key.textContent = n[0];
    tip.append(b, meta, key);
  } else tip.style.display = 'none';
};
cv.onwheel = e => {
  e.preventDefault();
  const f = e.deltaY < 0 ? 1.12 : 1/1.12;
  view.x = e.clientX - (e.clientX - view.x) * f;
  view.y = e.clientY - (e.clientY - view.y) * f;
  view.k *= f; draw();
};
addEventListener('resize', fit);
fit();
</script></body></html>
"""
