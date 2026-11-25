import { useState, useEffect, useMemo } from 'react';
import { configFiles } from '../services/api';
import { lineDiff, withContext, diffStats } from '../services/lineDiff';
import '../styles/ConfigEditor.css';

function DiffView({ oldText, newText, emptyMessage = 'No changes.' }) {
  const diff = useMemo(() => lineDiff(oldText, newText), [oldText, newText]);
  const stats = diffStats(diff);
  if (!stats.added && !stats.removed) {
    return <div className="no-data">{emptyMessage}</div>;
  }
  return (
    <div className="diff-view">
      <div className="diff-stats">
        <span className="diff-added">+{stats.added}</span>{' '}
        <span className="diff-removed">-{stats.removed}</span>
      </div>
      <pre className="diff-lines">
        {withContext(diff).map((d, i) =>
          d.type === 'skip' ? (
            <div key={i} className="diff-skip">… {d.count} unchanged lines …</div>
          ) : (
            <div key={i} className={`diff-line diff-${d.type}`}>
              <span className="diff-no">{d.type === 'add' ? '' : d.oldNo}</span>
              <span className="diff-no">{d.type === 'del' ? '' : d.newNo}</span>
              <span className="diff-mark">
                {d.type === 'add' ? '+' : d.type === 'del' ? '-' : ' '}
              </span>
              {d.text || ' '}
            </div>
          )
        )}
      </pre>
    </div>
  );
}

function EffectiveView() {
  const [data, setData] = useState(null);
  const [filter, setFilter] = useState('');
  const [onlyOverrides, setOnlyOverrides] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    configFiles.effective().then(setData).catch((err) => setError(err.message));
  }, []);

  if (error) return <div className="error-banner">{error}</div>;
  if (!data) return <div className="loading">Loading effective config...</div>;

  const f = filter.trim().toLowerCase();
  const sections = Object.entries(data.sections)
    .map(([section, keys]) => [
      section,
      Object.entries(keys).filter(([key, v]) =>
        (!onlyOverrides || v.source === 'overrides') &&
        (!f || section.toLowerCase().includes(f) || key.toLowerCase().includes(f) ||
          String(v.value ?? '').toLowerCase().includes(f))
      )
    ])
    .filter(([, keys]) => keys.length);

  return (
    <div className="effective-view">
      <p className="hint">
        What the next song will read: defaults.cfg with overrides.cfg on top.
        Secrets are masked.
      </p>
      <div className="effective-filters">
        <input
          type="search"
          placeholder="Filter sections, keys, values"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <label>
          <input
            type="checkbox"
            checked={onlyOverrides}
            onChange={(e) => setOnlyOverrides(e.target.checked)}
          />{' '}
          Only overridden
        </label>
      </div>
      {sections.map(([section, keys]) => (
        <div key={section} className="effective-section">
          <h3>[{section}]</h3>
          <table>
            <tbody>
              {keys.map(([key, v]) => (
                <tr key={key} className={v.source === 'overrides' ? 'overridden' : ''}>
                  <td className="eff-key">{key}</td>
                  <td className="eff-value">{v.value ?? <em>(no value)</em>}</td>
                  <td className="eff-source">{v.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      {Object.entries(data.led_configs || {}).map(([name, led]) => (
        <div key={name} className="effective-section">
          <h3>{name}{led.exists ? '' : ' (missing)'}</h3>
          {Object.entries(led.sections).map(([section, keys]) => (
            <table key={section}>
              <tbody>
                {Object.entries(keys).map(([key, value]) => (
                  <tr key={key}>
                    <td className="eff-key">[{section}] {key}</td>
                    <td className="eff-value">{value ?? <em>(no value)</em>}</td>
                    <td />
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
        </div>
      ))}
    </div>
  );
}

export default function ConfigEditor({ onBack, onLogout }) {
  const [files, setFiles] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loaded, setLoaded] = useState(null); // {content, sha256, read_only, exists}
  const [draft, setDraft] = useState('');
  const [warnings, setWarnings] = useState([]);
  const [parseError, setParseError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reviewing, setReviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [backups, setBackups] = useState([]);
  const [backupPreview, setBackupPreview] = useState(null); // {id, content}
  const [view, setView] = useState('edit'); // edit | effective

  const dirty = loaded && draft !== loaded.content;

  useEffect(() => {
    configFiles
      .list()
      .then((list) => {
        setFiles(list);
        if (list.length) openFile(list[0].name);
      })
      .catch((err) => setError(err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // reload = true after a save/restore: no "discard changes?" prompt, keep the notice
  const openFile = async (name, reload = false) => {
    if (!reload && dirty && !confirm('Discard unsaved changes?')) return;
    try {
      const data = await configFiles.get(name);
      setSelected(name);
      setLoaded(data);
      setDraft(data.content);
      setWarnings([]);
      setParseError('');
      setReviewing(false);
      setBackupPreview(null);
      if (!reload) setNotice('');
      setError('');
      setBackups(data.read_only ? [] : await configFiles.backups(name));
    } catch (err) {
      setError(err.message);
    }
  };

  const handleValidate = async () => {
    try {
      const res = await configFiles.validate(selected, draft, loaded.sha256);
      setParseError(res.ok ? '' : res.error);
      setWarnings(res.warnings || []);
      return res.ok;
    } catch (err) {
      setError(err.message);
      return false;
    }
  };

  const handleReview = async () => {
    if (await handleValidate()) setReviewing(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await configFiles.save(selected, draft, loaded.sha256);
      const warnText = res.warnings?.length ? ` (${res.warnings.length} warning(s))` : '';
      await openFile(selected, true);
      setWarnings(res.warnings || []);
      setNotice(res.note + warnText);
      setFiles(await configFiles.list());
    } catch (err) {
      if (err.status === 409) {
        setError(`${err.message} Your edits are still in the editor; copy them before reloading.`);
      } else {
        setError(err.message);
      }
    } finally {
      setSaving(false);
      setReviewing(false);
    }
  };

  const handlePreviewBackup = async (id) => {
    try {
      const data = await configFiles.getBackup(selected, id);
      setBackupPreview({ id, content: data.content });
    } catch (err) {
      setError(err.message);
    }
  };

  const handleRestore = async (id) => {
    if (!confirm(`Restore ${selected} from ${id}? The current file is backed up first.`)) return;
    try {
      const res = await configFiles.restore(selected, id);
      await openFile(selected, true);
      setNotice(`Restored from ${id}. ${res.note}`);
    } catch (err) {
      setError(err.message);
    }
  };

  const current = files.find((f) => f.name === selected);

  return (
    <div className="dashboard config-editor">
      <header className="dashboard-header">
        <h1>Config Editor</h1>
        <div className="header-actions">
          <button
            onClick={() => {
              if (!dirty || confirm('Discard unsaved changes?')) onBack();
            }}
            className="btn btn-secondary"
          >
            ← Back
          </button>
          <button onClick={onLogout} className="btn btn-secondary">
            Logout
          </button>
        </div>
      </header>

      <div className="warning-banner">
        Show settings apply from the next song (each song is a new process).
        API settings ([api], [buttons], [devices]) need an API restart.
      </div>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      <div className="config-tabs">
        <button
          className={`btn ${view === 'edit' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setView('edit')}
        >
          Files
        </button>
        <button
          className={`btn ${view === 'effective' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setView('effective')}
        >
          Effective config
        </button>
      </div>

      {view === 'effective' ? (
        <div className="card"><EffectiveView /></div>
      ) : (
        <div className="config-layout">
          <aside className="card config-files">
            <h2>Files</h2>
            <ul>
              {files.map((f) => (
                <li key={f.name}>
                  <button
                    className={`file-button ${f.name === selected ? 'selected' : ''}`}
                    onClick={() => openFile(f.name)}
                  >
                    <span className="file-name">{f.name}</span>
                    <span className="file-meta">
                      {f.read_only ? 'read-only' : f.exists ? f.kind : 'new'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>

          <section className="card config-main">
            {!loaded ? (
              <div className="loading">Loading...</div>
            ) : (
              <>
                <div className="card-header">
                  <h2>
                    {selected}
                    {dirty && <span className="dirty-mark"> (modified)</span>}
                  </h2>
                  {!loaded.read_only && (
                    <div className="editor-actions">
                      <button className="btn btn-secondary btn-sm" onClick={handleValidate}>
                        Check
                      </button>
                      <button
                        className="btn btn-secondary btn-sm"
                        disabled={!dirty}
                        onClick={() => {
                          setDraft(loaded.content);
                          setReviewing(false);
                        }}
                      >
                        Revert
                      </button>
                      <button
                        className="btn btn-primary btn-sm"
                        disabled={!dirty || saving}
                        onClick={handleReview}
                      >
                        Review &amp; save
                      </button>
                    </div>
                  )}
                </div>

                {loaded.read_only && (
                  <p className="hint">
                    Read-only reference. Put changes in overrides.cfg (or your LED config).
                  </p>
                )}
                {!loaded.exists && (
                  <p className="hint">This file doesn't exist yet; saving creates it.</p>
                )}
                {current?.kind === 'led' && (
                  <p className="hint">LED config: keys follow led1.defaults.cfg.</p>
                )}

                {parseError && (
                  <div className="error-banner">Does not parse: {parseError}</div>
                )}
                {warnings.length > 0 && (
                  <div className="config-warnings">
                    <strong>Warnings</strong> (saved anyway; the show ignores unknown keys):
                    <ul>
                      {warnings.map((w) => <li key={w}>{w}</li>)}
                    </ul>
                  </div>
                )}

                {reviewing ? (
                  <div className="review">
                    <h3>Review changes</h3>
                    <DiffView oldText={loaded.content} newText={draft} />
                    <div className="editor-actions">
                      <button className="btn btn-secondary" onClick={() => setReviewing(false)}>
                        Keep editing
                      </button>
                      <button className="btn btn-success" disabled={saving} onClick={handleSave}>
                        {saving ? 'Saving...' : 'Save'}
                      </button>
                    </div>
                  </div>
                ) : (
                  <textarea
                    className="config-textarea"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    readOnly={loaded.read_only}
                    spellCheck={false}
                    autoCapitalize="off"
                    autoCorrect="off"
                  />
                )}

                {!loaded.read_only && (
                  <div className="backups">
                    <h3>Backups</h3>
                    {backups.length === 0 ? (
                      <div className="no-data">No backups yet. Each save keeps one (last 20).</div>
                    ) : (
                      <ul>
                        {backups.map((b) => (
                          <li key={b.id}>
                            <span className="backup-time">
                              {new Date(b.created).toLocaleString()}
                            </span>
                            <button
                              className="btn btn-secondary btn-sm"
                              onClick={() => handlePreviewBackup(b.id)}
                            >
                              Diff
                            </button>
                            <button
                              className="btn btn-danger btn-sm"
                              onClick={() => handleRestore(b.id)}
                            >
                              Restore
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    {backupPreview && (
                      <div className="review">
                        <h4>
                          Restoring {backupPreview.id} would change the current file:
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => setBackupPreview(null)}
                          >
                            Close
                          </button>
                        </h4>
                        <DiffView
                          oldText={loaded.content}
                          newText={backupPreview.content}
                          emptyMessage="Identical to the current file."
                        />
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
