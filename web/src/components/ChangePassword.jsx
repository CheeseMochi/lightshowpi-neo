import { useState } from 'react';
import { auth } from '../services/api';

const MIN_LENGTH = 8;

export default function ChangePassword({ onDone }) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setMessage('');
    if (next.length < MIN_LENGTH) {
      setError(`New password must be at least ${MIN_LENGTH} characters`);
      return;
    }
    if (next !== repeat) {
      setError("New passwords don't match");
      return;
    }
    setSaving(true);
    try {
      await auth.changePassword(current, next);
      setCurrent('');
      setNext('');
      setRepeat('');
      setMessage(
        'Password changed. If the physical buttons log in as this user, ' +
        'update /etc/lightshowpi/buttons.env on the Pi too.'
      );
    } catch (err) {
      setError(err.message || 'Could not change password');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card password-card">
      <h2>Change password</h2>
      {error && <div className="error-banner">{error}</div>}
      {message && <div className="success-banner">{message}</div>}
      <form onSubmit={handleSubmit} className="password-form">
        <input
          type="password"
          autoComplete="current-password"
          placeholder="Current password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
        />
        <input
          type="password"
          autoComplete="new-password"
          placeholder={`New password (${MIN_LENGTH}+ characters)`}
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
        />
        <input
          type="password"
          autoComplete="new-password"
          placeholder="Repeat new password"
          value={repeat}
          onChange={(e) => setRepeat(e.target.value)}
          required
        />
        <div className="password-actions">
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? 'Saving...' : 'Change password'}
          </button>
          <button type="button" className="btn btn-secondary" onClick={onDone}>
            Close
          </button>
        </div>
      </form>
    </div>
  );
}
