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
