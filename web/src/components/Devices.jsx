import { useState, useEffect, useRef } from 'react';
import { devices as devicesApi, testMode } from '../services/api';
import '../styles/ScheduleManager.css'; // toggle switch styles
import '../styles/Devices.css';

const REFRESH_MS = 15000;

const GROUPS = [
  { kind: 'pi', title: 'Pis' },
  { kind: 'output', title: 'Outputs' },
  { kind: 'relay', title: 'Relay channels' }
];

function ago(iso) {
  if (!iso) return 'never';
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(iso).toLocaleString();
}

function duration(seconds) {
  if (seconds === null || seconds === undefined) return null;
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

function statusOf(device) {
  if (!device.enabled) return { cls: 'dot-off', label: 'disabled' };
  if (device.last_ok === true) return { cls: 'dot-ok', label: 'reachable' };
  if (device.last_ok === false) return { cls: 'dot-bad', label: device.last_error || 'unreachable' };
  return { cls: 'dot-unknown', label: device.kind === 'relay' ? 'not probed' : 'not checked yet' };
}

function DeviceRow({ device, onSave, onDelete, onToggle, test }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(device.name);
  const [notes, setNotes] = useState(device.notes || '');
  const [address, setAddress] = useState(device.address || '');
  const [port, setPort] = useState(device.port || '');
  const st = statusOf(device);
  const manual = !device.config_key;

  const startEdit = () => {
    setName(device.name);
    setNotes(device.notes || '');
    setAddress(device.address || '');
    setPort(device.port || '');
    setEditing(true);
  };

  const save = async () => {
    const changes = { name, notes };
    if (manual) {
      changes.address = address || null;
      changes.port = port ? Number(port) : null;
    }
    if (await onSave(device, changes)) setEditing(false);
  };

  const info = device.info || {};
  const level = test?.channels?.[device.channel];

  return (
    <div className={`device-row ${device.enabled ? '' : 'device-disabled'}`}>
      <span className={`status-dot ${st.cls}`} title={st.label} />
      {editing ? (
        <div className="device-edit">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" />
          {manual && (
            <>
              <input
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="Address (IP or hostname)"
              />
              <input
                value={port}
                onChange={(e) => setPort(e.target.value.replace(/\D/g, ''))}
                placeholder="Port (22)"
                className="port-input"
              />
            </>
          )}
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes"
            className="notes-input"
          />
          <button className="btn btn-primary btn-sm" onClick={save}>Save</button>
          <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="device-info">
          <div className="device-title">
            <strong>{device.name}</strong>
            {device.kind === 'relay' && <span className="tag">ch {device.channel}</span>}
            {device.role && <span className="tag">{device.role}</span>}
            {device.config_key === 'self' && <span className="tag tag-self">this Pi</span>}
            {!device.in_config && <span className="tag tag-warn">not in config</span>}
          </div>
          <div className="device-meta">
            {device.address && (
              <span>{device.address}{device.port ? `:${device.port}` : ''}</span>
            )}
            {device.kind !== 'relay' && (
              <span title={device.last_checked || ''}>seen {ago(device.last_seen)}</span>
            )}
            {device.last_ok === false && device.enabled && (
              <span className="device-error">{device.last_error}</span>
            )}
            {info.version && <span>v{info.version}</span>}
            {info.host_uptime_s != null && <span>up {duration(info.host_uptime_s)}</span>}
            {info.api_uptime_s != null && <span>API up {duration(info.api_uptime_s)}</span>}
          </div>
          {device.notes && <div className="device-notes">{device.notes}</div>}
        </div>
      )}

      {!editing && (
        <div className="device-actions">
          {device.kind === 'relay' && test?.active && (
            <>
              <button
                className={`btn btn-sm ${level > 0 ? 'btn-success' : 'btn-secondary'}`}
                onClick={() => test.set(device.channel, true)}
              >
                On
              </button>
              <button className="btn btn-sm btn-secondary" onClick={() => test.set(device.channel, false)}>
                Off
              </button>
            </>
          )}
          <label className="toggle-switch" title={
            device.config_key?.startsWith('output:')
              ? 'Writes enabled= to overrides.cfg (applies from the next song)'
              : 'Turn status checks on or off'
          }>
            <input type="checkbox" checked={device.enabled} onChange={() => onToggle(device)} />
            <span className="toggle-slider" />
          </label>
          <button className="btn btn-secondary btn-sm" onClick={startEdit}>
            Edit
          </button>
          {(manual || !device.in_config) && (
            <button className="btn btn-danger btn-sm" onClick={() => onDelete(device)}>
              Delete
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function AddPiForm({ onAdd, onCancel }) {
  const [name, setName] = useState('');
  const [address, setAddress] = useState('');
  const [port, setPort] = useState('');
  const [notes, setNotes] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    const ok = await onAdd({
      name,
      kind: 'pi',
      address: address || null,
      port: port ? Number(port) : null,
      role: 'client',
      notes: notes || null
    });
    if (ok) onCancel();
  };

  return (
    <form className="device-add" onSubmit={submit}>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. piclient)" required />
      <input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Address" />
      <input
        value={port}
        onChange={(e) => setPort(e.target.value.replace(/\D/g, ''))}
        placeholder="Port (22)"
        className="port-input"
      />
      <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes" className="notes-input" />
      <button type="submit" className="btn btn-primary btn-sm">Add</button>
      <button type="button" className="btn btn-secondary btn-sm" onClick={onCancel}>Cancel</button>
    </form>
  );
}

export default function Devices({ onBack, onLogout }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [test, setTest] = useState(null); // test mode status
  const enteredTest = useRef(false);

  const load = async () => {
    try {
      setData(await devicesApi.status());
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    load();
    testMode.status().then(setTest).catch(() => {});
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      clearInterval(timer);
      // Leaving the page: don't leave relays on or the show blocked
      if (enteredTest.current) testMode.exit().catch(() => {});
    };
  }, []);

  const run = async (fn, okMessage) => {
    setBusy(true);
    setError('');
    try {
      const res = await fn();
      if (okMessage) setNotice(typeof okMessage === 'function' ? okMessage(res) : okMessage);
      await load();
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleSync = () =>
    run(devicesApi.sync, (r) =>
      `Synced from config: ${r.added} added, ${r.updated} updated, ${r.missing} no longer in config.`);

  const handleProbe = () => run(devicesApi.probe, 'Checked all devices.');

  const handleSave = (device, changes) => run(() => devicesApi.update(device.id, changes), 'Saved.');

  const handleToggle = (device) => {
    const enabling = !device.enabled;
    if (device.config_key?.startsWith('output:') &&
        !confirm(`${enabling ? 'Enable' : 'Disable'} ${device.name}? This edits overrides.cfg ` +
                 '(backed up) and applies from the next song.')) {
      return;
    }
    run(() => devicesApi.update(device.id, { enabled: enabling }),
      device.config_key?.startsWith('output:')
        ? `${device.name} ${enabling ? 'enabled' : 'disabled'} in overrides.cfg; applies from the next song.`
        : null);
  };

  const handleDelete = (device) => {
    if (!confirm(`Delete ${device.name}?`)) return;
    run(() => devicesApi.delete(device.id), `Deleted ${device.name}.`);
  };

  const enterTest = async () => {
    setError('');
    try {
      setTest(await testMode.enter());
      enteredTest.current = true;
    } catch (err) {
      setError(err.status === 409
        ? `Can't enter test mode: ${err.message} (stop the show first)`
        : err.message);
    }
  };

  const exitTest = async () => {
    try {
      setTest(await testMode.exit());
    } catch (err) {
      setError(err.message);
    } finally {
      enteredTest.current = false;
    }
  };

  const setChannel = async (channel, state) => {
    try {
      setTest(await testMode.channel(channel, state));
    } catch (err) {
      setError(err.message);
      if (err.status === 409) setTest(await testMode.status().catch(() => null));
    }
  };

  const testCtl = test ? { ...test, set: setChannel } : null;
  const byKind = (kind) => (data?.devices || []).filter((d) => d.kind === kind);

  return (
    <div className="dashboard devices-page">
      <header className="dashboard-header">
        <h1>Devices</h1>
        <div className="header-actions">
          <button onClick={onBack} className="btn btn-secondary">← Back</button>
          <button onClick={onLogout} className="btn btn-secondary">Logout</button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      <div className="devices-toolbar">
        <button className="btn btn-secondary" disabled={busy} onClick={handleSync}>
          Sync from config
        </button>
        <button className="btn btn-secondary" disabled={busy} onClick={handleProbe}>
          Check now
        </button>
        <span className="monitor-state">
          {data?.monitor?.enabled
            ? `Checked every ${Math.round(data.monitor.interval_s)}s` +
              (data.monitor.last_run ? `, last ${ago(data.monitor.last_run)}` : '')
            : 'Background checks off ([devices] enabled = false)'}
        </span>
      </div>

      {!data ? (
        <div className="loading">Loading devices...</div>
      ) : (
        GROUPS.map(({ kind, title }) => (
          <div key={kind} className="card device-group">
            <div className="card-header">
              <h2>{title}</h2>
              {kind === 'pi' && !adding && (
                <button className="btn btn-primary btn-sm" onClick={() => setAdding(true)}>
                  + Add Pi
                </button>
              )}
              {kind === 'relay' && (
                test?.active ? (
                  <div className="test-controls">
                    <span className="tag tag-warn">
                      Test mode
                      {test.seconds_remaining != null &&
                        ` (auto-exit in ${Math.round(test.seconds_remaining / 60)}m)`}
                    </span>
                    <button className="btn btn-danger btn-sm" onClick={exitTest}>
                      Exit test mode
                    </button>
                  </div>
                ) : (
                  <button className="btn btn-secondary btn-sm" onClick={enterTest}
                    title="Lights off, then switch relays one by one. Blocks the show until you exit.">
                    Test relays
                  </button>
                )
              )}
            </div>
            {kind === 'pi' && adding && (
              <AddPiForm
                onAdd={(dev) => run(() => devicesApi.create(dev), `Added ${dev.name}.`)}
                onCancel={() => setAdding(false)}
              />
            )}
            {kind === 'relay' && (
              <p className="hint">
                Name each channel after what it drives (letter, snowflake...). Names are
                kept when you sync.
              </p>
            )}
            {byKind(kind).length === 0 ? (
              <div className="no-data">
                {kind === 'output'
                  ? 'No outputs. Add [output:<name>] sections or LED configs, then sync.'
                  : 'None yet. Click "Sync from config".'}
              </div>
            ) : (
              byKind(kind).map((d) => (
                <DeviceRow
                  key={d.id}
                  device={d}
                  onSave={handleSave}
                  onDelete={handleDelete}
                  onToggle={handleToggle}
                  test={kind === 'relay' ? testCtl : null}
                />
              ))
            )}
          </div>
        ))
      )}
    </div>
  );
}
