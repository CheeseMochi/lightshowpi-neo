import { useState, useEffect, useCallback } from 'react';
import { analytics } from '../services/api';
import { RANGES, rangeSince, formatRate, formatTs } from './eventRanges';
import { StatTile, StackedBars, ColumnChart } from './Charts';
import '../styles/Activity.css';
import '../styles/Insights.css';

const OUTCOMES = [
  { key: 'completions', label: 'Completed', color: 'var(--viz-1)' },
  { key: 'skips', label: 'Skipped', color: 'var(--viz-2)' },
  { key: 'stops', label: 'Stopped', color: 'var(--viz-3)' },
  { key: 'unknown', label: 'No end recorded', color: 'var(--viz-neutral)' }
];

const PLAY_SKIP = [
  { key: 'plays', label: 'Plays', color: 'var(--viz-1)' },
  { key: 'skips', label: 'Skips', color: 'var(--viz-2)' }
];

const TOP_SONGS = 15;

function songLabel(row) {
  return row.variant ? `${row.song} · ${row.variant}` : row.song;
}

function hourLabel(hour) {
  const h = hour % 12 === 0 ? 12 : hour % 12;
  return `${h}${hour < 12 ? 'a' : 'p'}`;
}

export default function Insights({ onBack }) {
  const [range, setRange] = useState('all');
  const [byVariant, setByVariant] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [summary, setSummary] = useState(null);
  const [songs, setSongs] = useState([]);
  const [hours, setHours] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    const since = rangeSince(range);
    try {
      const [s, songData, h] = await Promise.all([
        analytics.summary({ since }),
        analytics.songs({ since, byVariant }),
        analytics.hours({ since })
      ]);
      setSummary(s);
      setSongs(songData.songs);
      setHours(h);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [range, byVariant]);

  useEffect(() => {
    load();
  }, [load]);

  const songRows = (showAll ? songs : songs.slice(0, TOP_SONGS)).map((r) => ({
    key: `${r.song}|${r.variant || ''}`,
    label: songLabel(r),
    total: r.plays,
    segments: {
      completions: r.completions,
      skips: r.skips,
      stops: r.stops,
      unknown: Math.max(0, r.plays - r.completions - r.skips - r.stops)
    }
  }));

  // Hours with no plays at all are left out so the evening show fills the chart
  const hourRows = (hours?.by_hour || [])
    .filter((h) => h.plays > 0 || h.skips > 0)
    .map((h) => ({ label: hourLabel(h.hour), title: `${h.hour}:00–${h.hour}:59`, plays: h.plays, skips: h.skips }));
  const dayRows = (hours?.by_weekday || []).map((d) => ({ label: d.name, plays: d.plays, skips: d.skips }));

  const hasData = summary && summary.plays > 0;

  return (
    <div className="insights">
      <header className="insights-header">
        <h1>Insights</h1>
        <button onClick={onBack} className="btn btn-secondary">← Back</button>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {summary && !summary.enabled && (
        <div className="activity-note">
          Event logging is off (<code>[events] enabled = false</code>), so new plays aren't counted.
        </div>
      )}

      <div className="insights-filters">
        <label>
          <span className="sr-only">Time range</span>
          <select value={range} onChange={(e) => setRange(e.target.value)}>
            {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
        <label className="insights-check">
          <input type="checkbox" checked={byVariant} onChange={(e) => setByVariant(e.target.checked)} />
          Split songs by light sequence
        </label>
        <button onClick={load} className="btn btn-secondary" disabled={loading}>Refresh</button>
      </div>

      {summary && (
        <div className="stat-row">
          <StatTile label="Songs played" value={summary.plays.toLocaleString()}
                    note={summary.nights ? `over ${summary.nights} night${summary.nights === 1 ? '' : 's'}` : null} />
          <StatTile label="Played to the end" value={formatRate(summary.completion_rate)} />
          <StatTile label="Skipped" value={formatRate(summary.skip_rate)}
                    note={summary.most_skipped_song ? `most: ${summary.most_skipped_song}` : null} />
          <StatTile label="Average listened"
                    value={summary.avg_listen_pct === null ? '–' : `${Math.round(summary.avg_listen_pct)}%`} />
          <StatTile label="Hours of music" value={summary.listened_hours.toLocaleString()} />
          <StatTile label="Button presses" value={summary.button_presses.toLocaleString()}
                    note={summary.errors ? `${summary.errors} error${summary.errors === 1 ? '' : 's'}` : null} />
        </div>
      )}

      {!loading && !hasData && (
        <div className="card"><p className="activity-empty">No songs played in this range yet.</p></div>
      )}

      {hasData && (
        <>
          <div className="card">
            <h2>How each song ends</h2>
            <p className="viz-sub">Plays per song, split by whether it was played out, skipped or stopped.</p>
            <StackedBars rows={songRows} segments={OUTCOMES} />
            {songs.length > TOP_SONGS && (
              <button className="btn btn-secondary viz-more" onClick={() => setShowAll((v) => !v)}>
                {showAll ? `Show top ${TOP_SONGS}` : `Show all ${songs.length} songs`}
              </button>
            )}
          </div>

          <div className="insights-grid">
            <div className="card">
              <h2>By hour of day</h2>
              <p className="viz-sub">Plays and skips, the Pi's local time.</p>
              <ColumnChart rows={hourRows} series={PLAY_SKIP} labelEvery={hourRows.length > 12 ? 2 : 1} />
            </div>
            <div className="card">
              <h2>By day of week</h2>
              <p className="viz-sub">Plays and skips.</p>
              <ColumnChart rows={dayRows} series={PLAY_SKIP} />
            </div>
          </div>

          <div className="card">
            <h2>Song table</h2>
            <div className="activity-table-wrap">
              <table className="activity-table insights-table">
                <thead>
                  <tr>
                    <th>Song</th>
                    {byVariant && <th>Sequence</th>}
                    <th className="num">Plays</th>
                    <th className="num">Completed</th>
                    <th className="num">Skipped</th>
                    <th className="num">Stopped</th>
                    <th className="num">Skip rate</th>
                    <th className="num">Avg listened</th>
                    <th>Last played</th>
                  </tr>
                </thead>
                <tbody>
                  {songs.map((r) => (
                    <tr key={`${r.song}|${r.variant || ''}`}>
                      <td>{r.song}</td>
                      {byVariant && <td>{r.variant || 'default'}</td>}
                      <td className="num">{r.plays}</td>
                      <td className="num">{r.completions}</td>
                      <td className="num">{r.skips}</td>
                      <td className="num">{r.stops}</td>
                      <td className="num">{formatRate(r.skip_rate)}</td>
                      <td className="num">{r.avg_listen_pct === null ? '–' : `${Math.round(r.avg_listen_pct)}%`}</td>
                      <td className="nowrap">{formatTs(r.last_played)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
