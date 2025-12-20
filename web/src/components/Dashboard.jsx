import { useState, useEffect } from 'react';
import { lightshow, schedules as schedulesApi, buttons } from '../services/api';
import ScheduleManager from './ScheduleManager';
import '../styles/Dashboard.css';

export default function Dashboard({ onLogout }) {
  const [status, setStatus] = useState(null);
  const [upcomingEvents, setUpcomingEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showSchedules, setShowSchedules] = useState(false);
  const [buttonStatus, setButtonStatus] = useState(null);
  const [buttonHealth, setButtonHealth] = useState(null);

  // Auto-refresh status every 2 seconds
  useEffect(() => {
    fetchStatus();
    fetchUpcomingEvents();
    fetchButtonStatus();
    fetchButtonHealth();

    const interval = setInterval(() => {
      fetchStatus();
      fetchUpcomingEvents();
      fetchButtonStatus();
      fetchButtonHealth();
    }, 2000);

    return () => clearInterval(interval);
  }, []);

  const fetchStatus = async () => {
    try {
      const data = await lightshow.getStatus();
      setStatus(data);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const fetchUpcomingEvents = async () => {
    try {
      const events = await schedulesApi.getUpcoming(5);
      setUpcomingEvents(events);
    } catch (err) {
      console.error('Failed to fetch upcoming events:', err);
    }
  };

  const fetchButtonStatus = async () => {
    try {
      const data = await buttons.getStatus();
      setButtonStatus(data);
    } catch (err) {
      console.error('Failed to fetch button status:', err);
    }
  };

  const fetchButtonHealth = async () => {
    try {
      const data = await buttons.getHealth();
      setButtonHealth(data);
    } catch (err) {
      console.error('Failed to fetch button health:', err);
    }
  };

  const handleStart = async () => {
    try {
      await lightshow.start();
      await fetchStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleStop = async () => {
    try {
      await lightshow.stop('pause');
      await fetchStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleSkip = async () => {
    try {
      await lightshow.skip();
      await fetchStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleButtonSkip = async () => {
    try {
      await buttons.skip();
      await fetchButtonStatus();
      await fetchStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleRepeatToggle = async () => {
    try {
      await buttons.repeatToggle();
      await fetchButtonStatus();
      await fetchStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleAudioToggle = async () => {
    try {
      await buttons.audioToggle();
      await fetchButtonStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  if (loading) {
    return <div className="dashboard-loading">Loading...</div>;
  }

  if (showSchedules) {
    return (
      <ScheduleManager
        onBack={() => setShowSchedules(false)}
        onLogout={onLogout}
      />
    );
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>🎄 LightShowPi Neo</h1>
        <button onClick={onLogout} className="btn btn-secondary">
          Logout
        </button>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {buttonHealth && !buttonHealth.healthy && (
        <div className="warning-banner">
          ⚠ {buttonHealth.warning}
        </div>
      )}

      <div className="dashboard-content">
        {/* Status Panel */}
        <div className="card status-card">
          <h2>Status</h2>
          <div className="status-grid">
            <div className="status-item">
              <span className="label">State:</span>
              <span className={`status-badge status-${status?.state || 'idle'}`}>
                {status?.state || 'Unknown'}
              </span>
            </div>

            {status?.current_song && (
              <div className="status-item">
                <span className="label">Current Song:</span>
                <span className="value">{status.current_song}</span>
              </div>
            )}

            {status?.elapsed_time !== null && status?.total_time && (
              <div className="status-item">
                <span className="label">Progress:</span>
                <span className="value">
                  {formatTime(status.elapsed_time)} / {formatTime(status.total_time)}
                </span>
              </div>
            )}

            <div className="status-item">
              <span className="label">Schedule:</span>
              <span className="value">
                {status?.schedule_enabled ? '✓ Enabled' : '✗ Disabled'}
              </span>
            </div>
          </div>
        </div>

        {/* Controls */}
        <div className="card controls-card">
          <h2>Controls</h2>
          <div className="controls-grid">
            <button
              onClick={handleStart}
              className="btn btn-success btn-large"
              disabled={status?.state === 'playing'}
            >
              ▶ Start
            </button>

            <button
              onClick={handleStop}
              className="btn btn-danger btn-large"
              disabled={status?.state !== 'playing'}
            >
              ■ Stop
            </button>

            <button
              onClick={handleSkip}
              className="btn btn-primary btn-large"
              disabled={status?.state !== 'playing'}
            >
              ⏭ Skip
            </button>

            <button
              onClick={() => setShowSchedules(true)}
              className="btn btn-secondary btn-large"
            >
              📅 Schedules
            </button>
          </div>
        </div>

        {/* Button Controls */}
        {buttonStatus && buttonStatus.enabled && (
          <div className="card button-controls-card">
            <h2>Button Controls</h2>
            <div className="button-status-grid">
              <div className="button-status-item">
                <span className="label">Repeat Mode:</span>
                <span className={`status-badge ${buttonStatus.repeat_mode ? 'status-active' : 'status-inactive'}`}>
                  {buttonStatus.repeat_mode ? '✓ ON' : '✗ OFF'}
                </span>
              </div>
              <div className="button-status-item">
                <span className="label">Audio Output:</span>
                <span className={`status-badge ${buttonStatus.audio_on ? 'status-active' : 'status-inactive'}`}>
                  {buttonStatus.audio_on ? '✓ ON' : '✗ OFF'}
                </span>
              </div>
              {buttonStatus.last_action && (
                <div className="button-status-item">
                  <span className="label">Last Action:</span>
                  <span className="value">{buttonStatus.last_action}</span>
                </div>
              )}
            </div>
            <div className="controls-grid">
              <button
                onClick={handleButtonSkip}
                className="btn btn-primary btn-large"
              >
                ⏭ Skip
              </button>

              <button
                onClick={handleRepeatToggle}
                className={`btn btn-large ${buttonStatus.repeat_mode ? 'btn-warning' : 'btn-success'}`}
              >
                🔁 Repeat {buttonStatus.repeat_mode ? 'OFF' : 'ON'}
              </button>

              <button
                onClick={handleAudioToggle}
                className={`btn btn-large ${buttonStatus.audio_on ? 'btn-danger' : 'btn-success'}`}
              >
                🔊 Audio {buttonStatus.audio_on ? 'OFF' : 'ON'}
              </button>
            </div>
          </div>
        )}

        {/* Upcoming Events */}
        <div className="card events-card">
          <h2>Upcoming Events</h2>
          {upcomingEvents.length === 0 ? (
            <p className="no-events">No scheduled events</p>
          ) : (
            <div className="events-list">
              {upcomingEvents.map((event, index) => (
                <div key={index} className="event-item">
                  <span className={`event-action event-${event.action}`}>
                    {event.action === 'start' ? '▶' : '■'} {event.action.toUpperCase()}
                  </span>
                  <span className="event-time">
                    {formatEventTime(event.time)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatTime(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatEventTime(isoString) {
  const date = new Date(isoString);
  const now = new Date();
  const diff = date - now;

  // If within 24 hours, show relative time
  if (diff < 24 * 60 * 60 * 1000 && diff > 0) {
    const hours = Math.floor(diff / (60 * 60 * 1000));
    const minutes = Math.floor((diff % (60 * 60 * 1000)) / (60 * 1000));

    if (hours > 0) {
      return `in ${hours}h ${minutes}m`;
    } else {
      return `in ${minutes}m`;
    }
  }

  // Otherwise show time
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
