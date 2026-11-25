import { useState, useEffect } from 'react';
import { songs as songsApi } from '../services/api';
import '../styles/Songs.css';

// Per-song [custom_lightshow] keys edited as plain text (sequence has its own editor)
const TEXT_FIELDS = [
  { key: 'always_on_channels', label: 'Always on channels', hint: 'e.g. 1,4' },
  { key: 'always_off_channels', label: 'Always off channels', hint: 'e.g. 2' },
  { key: 'invert_channels', label: 'Inverted channels', hint: 'e.g. 3' },
  { key: 'attenuate_pct', label: 'Attenuate %', hint: '0-100' },
  { key: 'network_always_on', label: 'Network always on', hint: 'client channels' },
  { key: 'network_always_off', label: 'Network always off', hint: 'client channels' },
  { key: 'network_invert', label: 'Network inverted', hint: 'client channels' },
  { key: 'preshow_configuration', label: 'Preshow configuration', hint: 'JSON' },
  { key: 'preshow_script', label: 'Preshow script', hint: 'path' },
  { key: 'postshow_configuration', label: 'Postshow configuration', hint: 'JSON' },
  { key: 'postshow_script', label: 'Postshow script', hint: 'path' },
];

/** Split a "sequence" value into { mode, variant, selected }. */
export function parseSequence(value) {
  const text = (value || '').trim();
  if (!text) return { mode: 'default', variant: '', selected: [] };
  const colon = text.indexOf(':');
  if (colon >= 0) {
    const mode = text.slice(0, colon).trim().toLowerCase();
    const selected = text.slice(colon + 1).split(',').map((s) => s.trim()).filter(Boolean);
    return { mode, variant: '', selected };
  }
  if (text === 'fft' || text === 'csv') return { mode: text, variant: '', selected: [] };
  return { mode: 'variant', variant: text, selected: [] };
}

/** Build a "sequence" value ('' = no key, the legacy behaviour). */
export function buildSequence({ mode, variant, selected }) {
  if (mode === 'default') return '';
  if (mode === 'fft' || mode === 'csv') return mode;
  if (mode === 'variant') return variant;
  return selected.length ? `${mode}:${selected.join(',')}` : '';
}

function describeSequence(song) {
  const { mode, variant, selected } = parseSequence(song.sequence);
  switch (mode) {
    case 'default': return song.legacy_csv ? 'Default (CSV)' : 'Default (FFT)';
    case 'fft': return 'FFT';
    case 'csv': return 'Legacy CSV';
    case 'variant': return variant;
    default: return `${mode}: ${selected.join(', ')}`;
  }
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '';
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function SequenceEditor({ value, onChange, variants, legacyCsv }) {
  const parsed = parseSequence(value);
  const options = ['fft', ...(legacyCsv ? ['csv'] : []), ...variants];
  const update = (changes) => onChange(buildSequence({ ...parsed, ...changes }));

  const toggle = (name) => {
    const selected = parsed.selected.includes(name)
      ? parsed.selected.filter((n) => n !== name)
      : [...parsed.selected, name];
    update({ selected });
  };

  return (
    <div className="sequence-editor">
      <select
        value={parsed.mode}
        onChange={(e) => {
          const mode = e.target.value;
          update({
            mode,
            variant: mode === 'variant' ? (parsed.variant || variants[0] || '') : parsed.variant,
            selected: parsed.selected.length ? parsed.selected : options.slice(0, 2),
          });
        }}
      >
        <option value="default">Default ({legacyCsv ? 'legacy CSV' : 'FFT'})</option>
        <option value="fft">FFT</option>
        {legacyCsv && <option value="csv">Legacy CSV beside the song</option>}
        {variants.length > 0 && <option value="variant">One variant</option>}
        <option value="rotate">Rotate across…</option>
        <option value="random">Random from…</option>
      </select>

      {parsed.mode === 'variant' && (
        <select value={parsed.variant} onChange={(e) => update({ variant: e.target.value })}>
          {variants.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      )}

      {(parsed.mode === 'rotate' || parsed.mode === 'random') && (
        <div className="sequence-choices">
          {options.map((name) => (
            <label key={name} className="sequence-choice">
              <input
                type="checkbox"
                checked={parsed.selected.includes(name)}
                onChange={() => toggle(name)}
              />
              {name === 'csv' ? 'legacy csv' : name}
            </label>
          ))}
          {parsed.mode === 'rotate' && parsed.selected.length > 0 && (
            <div className="sequence-order">Order: {parsed.selected.join(' → ')}</div>
          )}
        </div>
      )}
      {variants.length === 0 && (
        <div className="field-hint">No variant CSVs found in the sequences folder for this song.</div>
      )}
    </div>
  );
}

function SettingsPanel({ song, onClose, onSaved }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({});
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    songsApi.getSettings(song.index)
      .then((res) => { setData(res); setForm(res.settings); })
      .catch((err) => setError(err.message));
  }, [song.index]);

  const save = async () => {
    setSaving(true);
    try {
      await songsApi.saveSettings(song.index, form);
      setError('');
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (!data) {
    return <div className="song-settings">{error || 'Loading settings…'}</div>;
  }

  return (
    <div className="song-settings">
      <div className="song-settings-header">
        <strong>Settings for {song.name}</strong>
        <span className="field-hint">{data.config_path}{data.exists ? '' : ' (new)'}</span>
      </div>
      {error && <div className="error-banner">{error}</div>}

      <div className="settings-field">
        <label>Sequence</label>
        <SequenceEditor
          value={form.sequence}
          onChange={(sequence) => setForm({ ...form, sequence })}
          variants={data.variants}
          legacyCsv={data.legacy_csv}
        />
      </div>

      <div className="settings-grid">
        {TEXT_FIELDS.map(({ key, label, hint }) => (
          <div className="settings-field" key={key}>
            <label htmlFor={`song-${key}`}>{label}</label>
            <input
              id={`song-${key}`}
              type="text"
              value={form[key] || ''}
              placeholder={hint}
              onChange={(e) => setForm({ ...form, [key]: e.target.value })}
            />
          </div>
        ))}
      </div>

      <div className="field-hint">Blank removes a setting. Changes apply from the song's next play.</div>
      <div className="song-settings-actions">
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button className="btn btn-secondary" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

export default function Songs({ onBack, onLogout }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(null);

  const load = async () => {
    try {
      setData(await songsApi.list());
      setError('');
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => { load(); }, []);

  // Run a change that returns the new list
  const run = async (fn, okMessage = '') => {
    setBusy(true);
    try {
      const result = await fn();
      if (result && result.songs) setData(result);
      else await load();
      setError('');
      setMessage(okMessage);
    } catch (err) {
      setError(err.message);
      setMessage('');
    } finally {
      setBusy(false);
    }
  };

  const move = (index, delta) => {
    const order = data.songs.map((s) => s.index);
    const target = index + delta;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    setEditing(null);
    run(() => songsApi.reorder(order));
  };

  const rename = (song, disabled = false) => {
    const name = window.prompt('Song name', song.name);
    if (name === null || name.trim() === song.name) return;
    run(() => (disabled
      ? songsApi.updateDisabled(song.index, { name })
      : songsApi.update(song.index, { name })));
  };

  const disable = (song) => {
    if (!window.confirm(`Take "${song.name}" out of the show? You can enable it again below.`)) return;
    setEditing(null);
    run(() => songsApi.update(song.index, { enabled: false }), `${song.name} disabled`);
  };

  const enable = (song) => {
    run(() => songsApi.updateDisabled(song.index, { enabled: true }), `${song.name} added to the end of the playlist`);
  };

  const play = (song) => {
    run(async () => { await songsApi.play(song.index); return null; },
      `${song.name} plays next (the current song stops now)`);
  };

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>🎵 Songs</h1>
        <div className="header-actions">
          <button onClick={onBack} className="btn btn-secondary">← Back</button>
          <button onClick={onLogout} className="btn btn-secondary">Logout</button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {message && <div className="songs-message">{message}</div>}

      <div className="dashboard-content">
        <div className="card">
          <div className="card-header">
            <h2>Playlist</h2>
            <button className="btn btn-secondary btn-sm" onClick={load} disabled={busy}>Reload</button>
          </div>
          {!data ? (
            <div className="loading">Loading songs…</div>
          ) : (
            <>
              <div className="field-hint songs-path">{data.playlist_path}</div>
              <div className="songs-table-wrap">
                <table className="songs-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Song</th>
                      <th>Length</th>
                      <th>Sequence</th>
                      <th aria-label="Actions"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.songs.map((song, i) => (
                      <SongRow
                        key={`${song.index}-${song.path}`}
                        song={song}
                        position={i}
                        count={data.songs.length}
                        busy={busy}
                        editing={editing === song.index}
                        onMove={move}
                        onRename={() => rename(song)}
                        onDisable={() => disable(song)}
                        onPlay={() => play(song)}
                        onEdit={() => setEditing(editing === song.index ? null : song.index)}
                        onSaved={() => { setEditing(null); load(); setMessage(`Saved settings for ${song.name}`); }}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        {data && data.disabled.length > 0 && (
          <div className="card">
            <div className="card-header">
              <h2>Disabled songs</h2>
            </div>
            <ul className="disabled-songs">
              {data.disabled.map((song) => (
                <li key={`${song.index}-${song.path}`}>
                  <span>
                    {song.name} <span className="field-hint">{song.path}</span>
                  </span>
                  <span className="song-actions">
                    <button className="btn btn-sm btn-secondary" onClick={() => rename(song, true)} disabled={busy}>Rename</button>
                    <button className="btn btn-sm btn-primary" onClick={() => enable(song)} disabled={busy}>Enable</button>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function SongRow({ song, position, count, busy, editing, onMove, onRename, onDisable, onPlay, onEdit, onSaved }) {
  return (
    <>
      <tr className={song.exists ? '' : 'song-missing'}>
        <td>{position + 1}</td>
        <td>
          <div className="song-name">{song.name}</div>
          <div className="field-hint">
            {song.path}{song.exists ? '' : ' (file missing)'}
          </div>
        </td>
        <td>{formatDuration(song.duration)}</td>
        <td>
          <span className="sequence-badge">{describeSequence(song)}</span>
          {song.variants.length > 0 && (
            <div className="field-hint">{song.variants.length} variant{song.variants.length === 1 ? '' : 's'}</div>
          )}
        </td>
        <td className="song-actions">
          <button className="btn btn-sm btn-secondary" title="Move up" onClick={() => onMove(position, -1)} disabled={busy || position === 0}>↑</button>
          <button className="btn btn-sm btn-secondary" title="Move down" onClick={() => onMove(position, 1)} disabled={busy || position === count - 1}>↓</button>
          <button className="btn btn-sm btn-primary" onClick={onPlay} disabled={busy}>▶ Play</button>
          <button className="btn btn-sm btn-secondary" onClick={onEdit}>{editing ? 'Close' : 'Settings'}</button>
          <button className="btn btn-sm btn-secondary" onClick={onRename} disabled={busy}>Rename</button>
          <button className="btn btn-sm btn-danger" onClick={onDisable} disabled={busy || count <= 1}>Disable</button>
        </td>
      </tr>
      {editing && (
        <tr className="song-settings-row">
          <td colSpan={5}>
            <SettingsPanel song={song} onClose={onEdit} onSaved={onSaved} />
          </td>
        </tr>
      )}
    </>
  );
}
