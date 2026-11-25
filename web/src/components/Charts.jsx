/**
 * Small hand-rolled charts for the Insights page (no chart library).
 *
 * Colours come from CSS variables in styles/Insights.css (--viz-*), a
 * validated categorical order for the dark card surface. Text always uses the
 * text colours; colour only marks identity, and every chart has a legend or
 * a title naming its one series, plus a hover tooltip.
 */
import { useState, useRef } from 'react';

/** Floating tooltip inside a position:relative wrapper. */
function useTooltip() {
  const ref = useRef(null);
  const [tip, setTip] = useState(null);
  const show = (event, content) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    setTip({ x: event.clientX - box.left, y: event.clientY - box.top, content, width: box.width });
  };
  const hide = () => setTip(null);
  const node = tip ? (
    <div
      className="viz-tooltip"
      role="status"
      style={{
        left: Math.min(Math.max(tip.x, 80), tip.width - 80),
        top: tip.y
      }}
    >
      {tip.content}
    </div>
  ) : null;
  return { ref, show, hide, node };
}

export function Legend({ items }) {
  return (
    <ul className="viz-legend">
      {items.map((item) => (
        <li key={item.label}>
          <span className="viz-swatch" style={{ background: item.color }} />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

export function StatTile({ label, value, note }) {
  return (
    <div className="stat-tile">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

/**
 * Horizontal stacked bars: one row per item, segments by outcome.
 *
 * rows: [{ key, label, total, segments: { [segKey]: count } }]
 * segments: [{ key, label, color }]
 */
export function StackedBars({ rows, segments, unit = 'plays' }) {
  const tip = useTooltip();
  const max = Math.max(1, ...rows.map((r) => r.total));
  return (
    <div className="viz-wrap" ref={tip.ref} onMouseLeave={tip.hide}>
      <Legend items={segments} />
      <div className="stacked-bars">
        {rows.map((row) => {
          const parts = segments
            .map((s) => ({ ...s, count: row.segments[s.key] || 0 }))
            .filter((p) => p.count > 0);
          return (
            <div className="stacked-row" key={row.key}>
              <div className="stacked-label" title={row.label}>{row.label}</div>
              <div className="stacked-track">
                <div className="stacked-bar" style={{ width: `${(row.total / max) * 100}%` }}>
                  {parts.map((p) => (
                    <div
                      key={p.key}
                      className="stacked-seg"
                      style={{ flexGrow: p.count, background: p.color }}
                      onMouseMove={(e) => tip.show(e, (
                        <>
                          <strong>{row.label}</strong>
                          <div>{p.label}: {p.count} of {row.total} ({Math.round((p.count / row.total) * 100)}%)</div>
                        </>
                      ))}
                    />
                  ))}
                </div>
                <span className="stacked-value">{row.total}</span>
              </div>
            </div>
          );
        })}
      </div>
      <span className="sr-only">Bar length is {unit}; segments split by outcome. The table below lists every value.</span>
      {tip.node}
    </div>
  );
}

/** Clean axis maximum and ticks (0, step, 2*step, ...). */
function niceTicks(maxValue, count = 4) {
  if (maxValue <= 0) return { max: 1, ticks: [0, 1] };
  const rough = maxValue / count;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= rough) || 10 * mag;
  const max = Math.ceil(maxValue / step) * step;
  const ticks = [];
  for (let v = 0; v <= max + 1e-9; v += step) ticks.push(Math.round(v * 100) / 100);
  return { max, ticks };
}

/** SVG path for a column with 4px rounded top, square at the baseline. */
function columnPath(x, y, w, h, r = 4) {
  if (h <= 0) return '';
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
}

/**
 * Grouped column chart (one or two series) with hover per category.
 *
 * rows: [{ label, [seriesKey]: number }]
 * series: [{ key, label, color }]
 */
export function ColumnChart({ rows, series, height = 200, labelEvery = 1, title }) {
  const tip = useTooltip();
  const width = 720;
  const pad = { top: 12, right: 8, bottom: 26, left: 36 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const maxValue = Math.max(0, ...rows.flatMap((r) => series.map((s) => r[s.key] || 0)));
  const { max, ticks } = niceTicks(maxValue);
  const band = innerW / Math.max(1, rows.length);
  const gap = 2;
  const barW = Math.min(24, (band * 0.7 - gap * (series.length - 1)) / series.length);
  const groupW = barW * series.length + gap * (series.length - 1);
  const y = (v) => pad.top + innerH - (v / max) * innerH;

  return (
    <div className="viz-wrap" ref={tip.ref} onMouseLeave={tip.hide}>
      {series.length > 1 && <Legend items={series} />}
      <svg viewBox={`0 0 ${width} ${height}`} className="column-chart" role="img"
           aria-label={title || series.map((s) => s.label).join(' and ')}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} className="viz-grid" />
            <text x={pad.left - 6} y={y(t)} className="viz-axis" textAnchor="end" dominantBaseline="middle">
              {t >= 10000 ? `${Math.round(t / 1000)}K` : t.toLocaleString()}
            </text>
          </g>
        ))}
        {rows.map((row, i) => {
          const x0 = pad.left + i * band + (band - groupW) / 2;
          return (
            <g key={row.label}>
              {series.map((s, j) => {
                const v = row[s.key] || 0;
                return (
                  <path key={s.key} d={columnPath(x0 + j * (barW + gap), y(v), barW, y(0) - y(v))}
                        fill={s.color} />
                );
              })}
              {i % labelEvery === 0 && (
                <text x={pad.left + i * band + band / 2} y={height - 8} className="viz-axis" textAnchor="middle">
                  {row.label}
                </text>
              )}
              {/* Hit target: the whole band, taller than the marks */}
              <rect
                x={pad.left + i * band}
                y={pad.top}
                width={band}
                height={innerH}
                className="viz-hit"
                onMouseMove={(e) => tip.show(e, (
                  <>
                    <strong>{row.title || row.label}</strong>
                    {series.map((s) => (
                      <div key={s.key}>
                        <span className="viz-swatch" style={{ background: s.color }} /> {s.label}: {row[s.key] || 0}
                      </div>
                    ))}
                  </>
                ))}
              />
            </g>
          );
        })}
        <line x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} className="viz-baseline" />
      </svg>
      {tip.node}
    </div>
  );
}
