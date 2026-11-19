"""
A self-contained HTML preview: the song plus one lamp per CSV column.

Open it in a browser next to the copied audio file. Lamps are driven from
audio.currentTime (not a timer), so seeking and pausing stay in sync. The
Pi's light_delay is not applied: the preview shows the CSV as written.
"""

import html
import json
from urllib.parse import quote
from typing import Dict, List, Optional

import numpy as np

from .safety import runs
from .timing import row_times

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#0b1020; --fg:#e8ecf4; --muted:#8a93a8; --off:#1d2436; --on:#ffd36b; --dead:#2a1d1d; }
  body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
         font:15px/1.4 system-ui,-apple-system,sans-serif; }
  h1 { font-size:18px; margin:0 0 4px; }
  .sub { color:var(--muted); margin:0 0 16px; }
  audio { width:100%; max-width:720px; }
  .lamps { display:flex; flex-wrap:wrap; gap:14px; margin:20px 0; }
  .lamp { width:64px; text-align:center; color:var(--muted); font-size:12px; }
  .bulb { width:56px; height:56px; margin:0 auto 6px; border-radius:50%; background:var(--off);
          transition:background 30ms linear, box-shadow 30ms linear; }
  .bulb.on { background:var(--on); box-shadow:0 0 24px 6px rgba(255,211,107,.55); }
  .bulb.dead { background:var(--dead); }
  .beat { width:10px; height:10px; border-radius:50%; background:var(--off); display:inline-block;
          vertical-align:middle; margin-left:8px; }
  .beat.on { background:#6bd3ff; }
  table { border-collapse:collapse; margin-top:12px; font-size:13px; }
  td { padding:2px 12px 2px 0; } td:first-child { color:var(--muted); }
</style></head><body>
<h1>__TITLE__</h1>
<p class="sub">__SUBTITLE__</p>
<audio id="a" controls src="__AUDIO__"></audio>
<div>Beat <span id="beat" class="beat"></span> <span id="t" class="sub"></span></div>
<div id="lamps" class="lamps"></div>
<table id="m"></table>
<script>
const D = __DATA__;
const lamps = document.getElementById('lamps');
const bulbs = D.channels.map((c, i) => {
  const el = document.createElement('div'); el.className = 'lamp';
  const b = document.createElement('div'); b.className = 'bulb' + (c.dead ? ' dead' : '');
  el.appendChild(b); el.appendChild(document.createTextNode('ch ' + (i + 1) + (c.dead ? ' (off)' : '')));
  lamps.appendChild(el); return b;
});
const m = document.getElementById('m');
for (const [k, v] of Object.entries(D.metrics)) {
  const tr = m.insertRow(); tr.insertCell().textContent = k;
  tr.insertCell().textContent = (typeof v === 'number') ? (Math.round(v * 1000) / 1000) : v;
}
function isOn(spans, t) {           // spans: sorted [start, end) seconds
  let lo = 0, hi = spans.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (t < spans[mid][0]) hi = mid - 1; else if (t >= spans[mid][1]) lo = mid + 1; else return true;
  }
  return false;
}
function nearBeat(t) {
  let lo = 0, hi = D.beats.length - 1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (D.beats[mid] <= t) lo = mid + 1; else hi = mid - 1; }
  return hi >= 0 && t - D.beats[hi] < 0.08;
}
const audio = document.getElementById('a'), beat = document.getElementById('beat'), tl = document.getElementById('t');
function frame() {
  const t = audio.currentTime;
  D.channels.forEach((c, i) => bulbs[i].classList.toggle('on', isOn(c.spans, t)));
  beat.classList.toggle('on', nearBeat(t));
  tl.textContent = t.toFixed(2) + ' s';
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
</script></body></html>
"""


def spans_by_channel(levels: np.ndarray, sample_rate: int, timing_ms: int = 25) -> List[List[List[float]]]:
    """ON spans in seconds per column (relay view: ON when > 0.5)."""
    on = np.asarray(levels) > 0.5
    times = row_times(on.shape[0] + 1, sample_rate, timing_ms)
    return [[[round(float(times[s]), 4), round(float(times[e]), 4)] for s, e in runs(on[:, c])]
            for c in range(on.shape[1])]


def write_preview(path: str, levels: np.ndarray, sample_rate: int, audio_name: str,
                  title: str, subtitle: str, beat_times, metrics: Dict[str, float],
                  dead: Optional[List[int]] = None, timing_ms: int = 25) -> None:
    """Write the preview page; ``audio_name`` is relative to the page."""
    dead = set(dead or [])
    data = {
        "channels": [{"spans": spans, "dead": i in dead}
                     for i, spans in enumerate(spans_by_channel(levels, sample_rate, timing_ms))],
        "beats": [round(float(b), 4) for b in beat_times],
        "metrics": {k: (v if not isinstance(v, float) or np.isfinite(v) else "n/a")
                    for k, v in metrics.items()},
    }
    page = (PAGE.replace("__TITLE__", html.escape(title))
                .replace("__SUBTITLE__", html.escape(subtitle))
                .replace("__AUDIO__", html.escape(quote(audio_name), quote=True))
                .replace("__DATA__", json.dumps(data, allow_nan=False, default=float)
                         .replace("</", "<\\/")))
    with open(path, "w") as f:
        f.write(page)


BUNDLE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#0b1020; --fg:#e8ecf4; --muted:#8a93a8; --off:#1d2436; --on:#ffd36b;
          --dead:#2a1d1d; --btn:#1a2134; --sel:#3a4a7a; }
  body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
         font:15px/1.4 system-ui,-apple-system,sans-serif; }
  h1 { font-size:18px; margin:0 0 4px; } .sub { color:var(--muted); margin:0 0 12px; }
  audio { width:100%; max-width:720px; }
  .row { display:flex; flex-wrap:wrap; gap:8px; margin:10px 0; align-items:center; }
  button { background:var(--btn); color:var(--fg); border:1px solid #2c3550; border-radius:6px;
           padding:6px 10px; font:inherit; cursor:pointer; }
  button.sel { background:var(--sel); border-color:#6b84d3; }
  .lamps { display:flex; flex-wrap:wrap; gap:14px; margin:18px 0; }
  .lamp { width:64px; text-align:center; color:var(--muted); font-size:12px; }
  .bulb { width:56px; height:56px; margin:0 auto 6px; border-radius:50%; background:var(--off); }
  .bulb.on { background:var(--on); box-shadow:0 0 24px 6px rgba(255,211,107,.55); }
  .bulb.dead { background:var(--dead); }
  #desc { max-width:720px; } table { border-collapse:collapse; font-size:13px; }
  td,th { padding:2px 12px 2px 0; text-align:left; } th { color:var(--muted); font-weight:500; }
</style></head><body>
<h1>__TITLE__</h1>
<p class="sub">__SUBTITLE__</p>
<audio id="a" controls></audio>
<div class="row"><span class="sub">Play:</span>
  <button id="m-clip">30 s clip</button><button id="m-full">Full song</button>
  <label><input type="checkbox" id="loop" checked> loop</label>
  <label><input type="checkbox" id="cycle"> next variant at the end</label>
  <span id="t" class="sub"></span></div>
<div id="variants" class="row"></div>
<p id="desc"></p>
<div id="lamps" class="lamps"></div>
<table id="m"><thead><tr><th>variant</th><th>on beat</th><th>toggles/ch/min</th>
<th>relay violations</th><th>duty</th></tr></thead><tbody></tbody></table>
<script>
const D = __DATA__;
const audio = document.getElementById('a');
let mode = 'clip', current = 0;
const lamps = document.getElementById('lamps');
const bulbs = D.dead.map((dead, i) => {
  const el = document.createElement('div'); el.className = 'lamp';
  const b = document.createElement('div'); b.className = 'bulb' + (dead ? ' dead' : '');
  const lab = document.createElement('div'); lab.textContent = 'ch ' + (i + 1) + (dead ? ' (off)' : '');
  el.appendChild(b); el.appendChild(lab); el.label = lab;
  lamps.appendChild(el); return b;
});
const vrow = document.getElementById('variants');
const buttons = D.variants.map((v, i) => {
  const b = document.createElement('button'); b.textContent = (i + 1) + '. ' + v.title;
  b.onclick = () => select(i, true); vrow.appendChild(b); return b;
});
const tbody = document.querySelector('#m tbody');
D.variants.forEach(v => {
  const m = v.metrics.full, tr = tbody.insertRow();
  [v.title, m.on_beat_fraction, m.toggles_per_channel_min_median,
   m.short_on_violations + m.short_off_violations, m.duty_mean].forEach(x => {
    tr.insertCell().textContent = (typeof x === 'number') ? Math.round(x * 100) / 100 : x; });
});
function select(i, restart) {
  current = i;
  buttons.forEach((b, j) => b.classList.toggle('sel', j === i));
  document.getElementById('desc').textContent = D.variants[i].description;
  const labels = D.variants[i].labels;
  bulbs.forEach((b, j) => { b.parentNode.label.textContent = 'ch ' + (j + 1) +
    (labels ? ' ' + labels[j] : (D.dead[j] ? ' (off)' : '')); });
  if (restart) { audio.currentTime = 0; audio.play(); }
}
function setMode(m) {
  mode = m;
  document.getElementById('m-clip').classList.toggle('sel', m === 'clip');
  document.getElementById('m-full').classList.toggle('sel', m === 'full');
  audio.src = D.audio[m]; audio.loop = false;
}
document.getElementById('m-clip').onclick = () => { setMode('clip'); audio.play(); };
document.getElementById('m-full').onclick = () => { setMode('full'); audio.play(); };
audio.addEventListener('ended', () => {
  if (document.getElementById('cycle').checked) select((current + 1) % D.variants.length, true);
  else if (document.getElementById('loop').checked) { audio.currentTime = 0; audio.play(); }
});
function isOn(spans, t) {
  let lo = 0, hi = spans.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (t < spans[mid][0]) hi = mid - 1; else if (t >= spans[mid][1]) lo = mid + 1; else return true;
  }
  return false;
}
function frame() {
  const t = audio.currentTime, spans = D.variants[current].spans[mode];
  spans.forEach((s, i) => bulbs[i].classList.toggle('on', isOn(s, t)));
  document.getElementById('t').textContent = t.toFixed(1) + ' s';
  requestAnimationFrame(frame);
}
setMode('clip'); select(0, false); requestAnimationFrame(frame);
</script></body></html>
"""


def write_bundle_preview(path: str, title: str, subtitle: str, audio: Dict[str, str],
                         dead: List[bool], variants: List[dict]) -> None:
    """
    One page for a whole bundle. ``variants``: dicts with name, title,
    description, spans {"clip": [...], "full": [...]} and metrics {"full": {...}}.
    ``audio``: {"clip": relative path, "full": relative path}.
    """
    def clean(m):
        return {k: (v if not isinstance(v, float) or np.isfinite(v) else None) for k, v in m.items()}

    data = {
        "audio": {k: quote(v) for k, v in audio.items()},
        "dead": dead,
        "variants": [{**v, "metrics": {k: clean(m) for k, m in v["metrics"].items()}}
                     for v in variants],
    }
    page = (BUNDLE_PAGE.replace("__TITLE__", html.escape(title))
            .replace("__SUBTITLE__", html.escape(subtitle))
            .replace("__DATA__", json.dumps(data, allow_nan=False, default=float)
                     .replace("</", "<\\/")))
    with open(path, "w") as f:
        f.write(page)
