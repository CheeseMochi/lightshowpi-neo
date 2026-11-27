import { useState, useEffect, useCallback } from 'react';
import { events as eventsApi } from '../services/api';
import { RANGES, rangeSince, formatTs, formatSeconds } from './eventRanges';
import '../styles/Activity.css';

const PAGE_SIZE = 50;

// Filter choices -> event types sent to /api/events
const TYPE_FILTERS = [
  { value: 'all', label: 'All events', types: null },
  { value: 'show', label: 'Show start / stop', types: ['show_start', 'show_stop'] },
  { value: 'songs', label: 'Songs', types: ['song_start', 'song_end'] },
  { value: 'skips', label: 'Skips', types: ['skip'] },
  { value: 'buttons', label: 'Button presses', types: ['button_press'] },
  { value: 'admin', label: 'Admin actions', types: ['api_action', 'config_change', 'sequence_upload', 'sequence_delete'] },
  { value: 'test', label: 'Test mode', types: ['test_mode'] },
  { value: 'errors', label: 'Errors', types: ['error'] }
];

const TYPE_LABELS = {
  show_start: 'Show start',
  show_stop: 'Show stop',
  song_start: 'Song start',
  song_end: 'Song end',
  skip: 'Skip',
  button_press: 'Button',
  api_action: 'Admin action',
  config_change: 'Config change',
  sequence_upload: 'Sequence upload',
  sequence_delete: 'Sequence delete',
  test_mode: 'Test mode',
  error: 'Error'
};

/** One-line summary of an event's details. */
function describe(event) {
  const d = event.details || {};
  switch (event.type) {
    case 'song_end': {
      const heard = formatSeconds(d.elapsed_s);
      const of = d.duration_s ? ` of ${formatSeconds(d.duration_s)}` : '';
      return `${d.reason || 'ended'} after ${heard}${of}`;
    }
    case 'song_start':
      return d.duration_s ? `length ${formatSeconds(d.duration_s)}` : '';
    case 'show_start':
      return [d.mode, d.playlist && d.playlist.split('/').pop()].filter(Boolean).join(' · ');
    case 'button_press':
      return [d.name, d.mode && `(${d.mode} mode)`].filter(Boolean).join(' ');
    case 'api_action':
      return `${d.method || ''} ${d.route || d.path || ''}`.trim();
    case 'test_mode':
      return [d.action, d.reason].filter(Boolean).join(' · ');
    case 'sequence_upload':
      return [d.user, d.replaced ? 'replaced' : 'new', d.rows && `${d.rows} rows`].filter(Boolean).join(' · ');
    case 'sequence_delete':
      return d.user || '';
    case 'error':
      return [d.action, d.message].filter(Boolean).join(': ');
    default:
      return '';
  }
}

export default function Activity({ onBack }) {
  const [filter, setFilter] = useState('all');
  const [range, setRange] = useState('7');
  const [page, setPage] = useState(0);
  const [data, setData] = useState({ events: [], total: 0, enabled: true });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const types = TYPE_FILTERS.find((f) => f.value === filter)?.types;
      const result = await eventsApi.list({
        types,
        since: rangeSince(range),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE
      });
      setData(result);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [filter, range, page]);

  useEffect(() => {
    load();
  }, [load]);

  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  return (
    <div className="activity">
      <header className="activity-header">
        <h1>Activity</h1>
        <button onClick={onBack} className="btn btn-secondary">← Back</button>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {!data.enabled && (
        <div className="activity-note">
          Event logging is off (<code>[events] enabled = false</code>). Older events are still shown.
        </div>
      )}

      <div className="activity-filters">
        <label>
          <span className="sr-only">Event type</span>
          <select value={filter} onChange={(e) => { setFilter(e.target.value); setPage(0); }}>
            {TYPE_FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </label>
        <label>
          <span className="sr-only">Time range</span>
          <select value={range} onChange={(e) => { setRange(e.target.value); setPage(0); }}>
            {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
        <button onClick={load} className="btn btn-secondary" disabled={loading}>Refresh</button>
        <span className="activity-count">{data.total} event{data.total === 1 ? '' : 's'}</span>
      </div>

      <div className="card activity-table-card">
        {data.events.length === 0 ? (
          <p className="activity-empty">{loading ? 'Loading…' : 'No events in this range.'}</p>
        ) : (
          <div className="activity-table-wrap">
            <table className="activity-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>By</th>
                  <th>Song</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((e) => (
                  <tr key={e.id} className={`event-${e.type}`}>
                    <td className="nowrap">{formatTs(e.ts)}</td>
                    <td className="nowrap">
                      <span className={`event-badge badge-${e.type}`}>{TYPE_LABELS[e.type] || e.type}</span>
                    </td>
                    <td className="nowrap">
                      {e.source || ''}
                      {e.details?.user ? ` · ${e.details.user}` : ''}
                    </td>
                    <td>
                      {e.song || ''}
                      {e.variant ? <span className="variant"> ({e.variant})</span> : null}
                    </td>
                    <td className="details">{describe(e)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {pages > 1 && (
        <div className="activity-pager">
          <button className="btn btn-secondary" disabled={page === 0 || loading}
                  onClick={() => setPage((p) => p - 1)}>Newer</button>
          <span>Page {page + 1} of {pages}</span>
          <button className="btn btn-secondary" disabled={page + 1 >= pages || loading}
                  onClick={() => setPage((p) => p + 1)}>Older</button>
        </div>
      )}
    </div>
  );
}
