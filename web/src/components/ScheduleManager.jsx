import { useState, useEffect } from 'react';
import { schedules as schedulesApi } from '../services/api';
import '../styles/ScheduleManager.css';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

export default function ScheduleManager({ onBack, onLogout }) {
  const [schedules, setSchedules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState(null);

  useEffect(() => {
    fetchSchedules();
  }, []);

  const fetchSchedules = async () => {
    try {
      const data = await schedulesApi.list();
      setSchedules(data);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id) => {
    if (!confirm('Are you sure you want to delete this schedule?')) {
      return;
    }

    try {
      await schedulesApi.delete(id);
      await fetchSchedules();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleToggle = async (schedule) => {
    try {
      await schedulesApi.update(schedule.id, {
        enabled: !schedule.enabled
      });
      await fetchSchedules();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleEdit = (schedule) => {
    setEditingSchedule(schedule);
    setShowForm(true);
  };

  const handleAdd = () => {
    setEditingSchedule(null);
    setShowForm(true);
  };

  const handleFormClose = async () => {
    setShowForm(false);
    setEditingSchedule(null);
    await fetchSchedules();
  };

  if (showForm) {
    return (
      <ScheduleForm
        schedule={editingSchedule}
        onClose={handleFormClose}
        onLogout={onLogout}
      />
    );
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>📅 Schedule Manager</h1>
        <div className="header-actions">
          <button onClick={onBack} className="btn btn-secondary">
            ← Back
          </button>
          <button onClick={onLogout} className="btn btn-secondary">
            Logout
          </button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <div className="dashboard-content">
        <div className="card">
          <div className="card-header">
            <h2>Schedules</h2>
            <button onClick={handleAdd} className="btn btn-primary">
              + Add Schedule
            </button>
          </div>

          {loading ? (
            <div className="loading">Loading schedules...</div>
          ) : schedules.length === 0 ? (
            <div className="no-data">
              No schedules configured. Click "Add Schedule" to create one.
            </div>
          ) : (
            <div className="schedules-list">
              {schedules.map((schedule) => (
                <div key={schedule.id} className="schedule-item">
                  <div className="schedule-info">
                    <div className="schedule-times">
                      <span className="time-badge">{schedule.start_time}</span>
                      <span className="separator">→</span>
                      <span className="time-badge">{schedule.stop_time}</span>
                    </div>

                    <div className="schedule-meta">
                      <div className="schedule-days">
                        {schedule.days_of_week.map((day) => (
                          <span key={day} className="day-badge">
                            {DAYS[day]}
                          </span>
                        ))}
                      </div>
                      <div className="schedule-mode">
                        Mode: <span className="mode-value">{schedule.mode || 'playlist'}</span>
                      </div>
                    </div>
                  </div>

                  <div className="schedule-actions">
                    <label className="toggle-switch">
                      <input
                        type="checkbox"
                        checked={schedule.enabled}
                        onChange={() => handleToggle(schedule)}
                      />
                      <span className="toggle-slider"></span>
                    </label>

                    <button
                      onClick={() => handleEdit(schedule)}
                      className="btn btn-sm btn-secondary"
                    >
                      Edit
                    </button>

                    <button
                      onClick={() => handleDelete(schedule.id)}
                      className="btn btn-sm btn-danger"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ScheduleForm({ schedule, onClose, onLogout }) {
  const [startTime, setStartTime] = useState(schedule?.start_time || '18:00');
  const [stopTime, setStopTime] = useState(schedule?.stop_time || '22:00');
  const [mode, setMode] = useState(schedule?.mode || 'playlist');
  const [daysOfWeek, setDaysOfWeek] = useState(
    schedule?.days_of_week || [0, 1, 2, 3, 4, 5, 6]
  );
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const toggleDay = (day) => {
    setDaysOfWeek((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort()
    );
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setSaving(true);

    try {
      const data = {
        start_time: startTime,
        stop_time: stopTime,
        mode: mode,
        days_of_week: daysOfWeek,
        enabled
      };

      if (schedule) {
        await schedulesApi.update(schedule.id, data);
      } else {
        await schedulesApi.create(data);
      }

      onClose();
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>{schedule ? 'Edit Schedule' : 'New Schedule'}</h1>
        <div className="header-actions">
          <button onClick={onClose} className="btn btn-secondary">
            ← Cancel
          </button>
          <button onClick={onLogout} className="btn btn-secondary">
            Logout
          </button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <div className="dashboard-content">
        <div className="card">
          <form onSubmit={handleSubmit} className="schedule-form">
            <div className="form-group">
              <label htmlFor="start-time">Start Time</label>
              <input
                id="start-time"
                type="time"
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="stop-time">Stop Time</label>
              <input
                id="stop-time"
                type="time"
                value={stopTime}
                onChange={(e) => setStopTime(e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="mode">Mode</label>
              <select
                id="mode"
                value={mode}
                onChange={(e) => setMode(e.target.value)}
                className="mode-select"
              >
                <option value="playlist">Playlist</option>
                <option value="ambient">Ambient</option>
                <option value="audio-in">Audio In</option>
                <option value="stream-in">Stream In</option>
              </select>
            </div>

            <div className="form-group">
              <label>Days of Week</label>
              <div className="days-selector">
                {DAYS.map((day, index) => (
                  <button
                    key={index}
                    type="button"
                    className={`day-button ${
                      daysOfWeek.includes(index) ? 'active' : ''
                    }`}
                    onClick={() => toggleDay(index)}
                  >
                    {day}
                  </button>
                ))}
              </div>
            </div>

            <div className="form-group">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                />
                <span>Enabled</span>
              </label>
            </div>

            <div className="form-actions">
              <button
                type="button"
                onClick={onClose}
                className="btn btn-secondary"
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={saving || daysOfWeek.length === 0}
              >
                {saving ? 'Saving...' : 'Save Schedule'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
