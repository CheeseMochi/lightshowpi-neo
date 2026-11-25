import { useCallback, useEffect, useRef, useState } from 'react';
import { publicApi } from '../services/api';
import { ADMIN_URL, SHOW_NAME } from '../config';
import { formatClock, offMessage } from '../utils/showStatus';
import '../styles/Home.css';

// Now-playing changes with every song; times and the playlist rarely do.
// The Pi rate-limits public calls per IP (shared by phones behind one NAT).
const NOW_PLAYING_MS = 5000;
const SLOW_MS = 60000;

const BULB_COLORS = ['red', 'gold', 'green', 'blue'];
const BULBS = Array.from({ length: 18 }, (_, i) => BULB_COLORS[i % BULB_COLORS.length]);

// Fixed pseudo-random flakes so the layout is stable between renders
const FLAKES = Array.from({ length: 28 }, (_, i) => ({
  left: (i * 37) % 100,
  size: 4 + ((i * 7) % 6),
  duration: 9 + ((i * 13) % 10),
  delay: -((i * 11) % 17),
  drift: ((i % 5) - 2) * 12,
}));

/**
 * Call `fn` now and every `ms` while the tab is visible; stop when hidden
 * (phones in pockets shouldn't keep polling) and refresh on return.
 */
function useVisiblePolling(fn, ms) {
  const saved = useRef(fn);
  saved.current = fn;

  useEffect(() => {
    let timer = null;
    const tick = () => saved.current();
    const start = () => {
      if (timer === null) {
        tick();
        timer = setInterval(tick, ms);
      }
    };
    const stop = () => {
      clearInterval(timer);
      timer = null;
    };
    const onVisibility = () => (document.hidden ? stop() : start());

    if (!document.hidden) start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [ms]);
}

function Gear() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">
      <path
        fill="currentColor"
        d="M19.14 12.94a7.1 7.1 0 0 0 .05-.94 7.1 7.1 0 0 0-.05-.94l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7 7 0 0 0-1.63-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54a7 7 0 0 0-1.63.94l-2.39-.96a.5.5 0 0 0-.6.22L2.71 8.84a.5.5 0 0 0 .12.64l2.03 1.58a7.1 7.1 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.64l1.92 3.32a.5.5 0 0 0 .6.22l2.39-.96c.5.39 1.05.7 1.63.94l.36 2.54a.5.5 0 0 0 .5.42h3.84a.5.5 0 0 0 .5-.42l.36-2.54a7 7 0 0 0 1.63-.94l2.39.96a.5.5 0 0 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.58ZM12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7Z"
      />
    </svg>
  );
}

function StatusCard({ nowPlaying, fetchedAt, schedule, offline }) {
  const [, setTick] = useState(0);
  const playing = nowPlaying && nowPlaying.state === 'playing';
  const hasProgress = playing && nowPlaying.elapsed_s != null && nowPlaying.duration_s > 0;

  // Advance the progress bar every second between polls
  useEffect(() => {
    if (!hasProgress) return undefined;
    const timer = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(timer);
  }, [hasProgress]);

  if (!nowPlaying) {
    return (
      <section className="home-card home-status" aria-live="polite">
        <p className="home-status-label">{offline ? 'Can’t reach the show right now' : 'Checking the lights…'}</p>
      </section>
    );
  }

  if (playing) {
    let elapsed = null;
    let pct = 0;
    if (hasProgress) {
      elapsed = Math.min(nowPlaying.duration_s,
        nowPlaying.elapsed_s + (Date.now() - fetchedAt) / 1000);
      pct = Math.max(0, Math.min(100, (elapsed / nowPlaying.duration_s) * 100));
    }
    return (
      <section className="home-card home-status is-on" aria-live="polite">
        <p className="home-status-label"><span className="home-live-dot" aria-hidden="true" /> The show is on!</p>
        <p className="home-now-label">Now playing</p>
        <h2 className="home-now-title">{nowPlaying.title || 'Live music'}</h2>
        {hasProgress && (
          <div className="home-progress">
            <div className="home-progress-track" role="progressbar" aria-label="Song progress"
              aria-valuemin={0} aria-valuemax={Math.round(nowPlaying.duration_s)}
              aria-valuenow={Math.round(elapsed)}>
              <div className="home-progress-fill" style={{ width: `${pct}%` }} />
            </div>
            <div className="home-progress-times">
              <span>{formatClock(elapsed)}</span>
              <span>{formatClock(nowPlaying.duration_s)}</span>
            </div>
          </div>
        )}
      </section>
    );
  }

  if (nowPlaying.state === 'ambient') {
    return (
      <section className="home-card home-status is-on" aria-live="polite">
        <p className="home-status-label"><span className="home-live-dot" aria-hidden="true" /> The lights are on</p>
        <h2 className="home-now-title">Enjoy the glow</h2>
        {schedule && schedule.note && <p className="home-status-detail">{schedule.note}</p>}
      </section>
    );
  }

  const message = offMessage(schedule);
  return (
    <section className="home-card home-status" aria-live="polite">
      <h2 className="home-now-title">{message.headline}</h2>
      {message.detail && <p className="home-status-detail">{message.detail}</p>}
    </section>
  );
}

function Playlist({ playlist, isPlaying }) {
  const songs = (playlist && playlist.songs) || [];
  if (songs.length === 0) return null;
  const current = isPlaying ? playlist.current : null;
  return (
    <section className="home-card home-playlist">
      <h2 className="home-section-title">Tonight’s songs</h2>
      <ol className="home-songs">
        {songs.map((title, i) => (
          <li key={`${i}-${title}`} className={i === current ? 'is-current' : undefined}
            aria-current={i === current ? 'true' : undefined}>
            <span className="home-song-num">{i + 1}</span>
            <span className="home-song-title">{title}</span>
            {i === current && <span className="home-song-badge">Playing</span>}
          </li>
        ))}
      </ol>
    </section>
  );
}

/**
 * The public show page: no login, safe for anyone on the street to open.
 * Data comes from the public API (the Pi, or the cloud copy of it).
 */
export default function Home() {
  const [nowPlaying, setNowPlaying] = useState(null);
  const [fetchedAt, setFetchedAt] = useState(0);
  const [schedule, setSchedule] = useState(null);
  const [playlist, setPlaylist] = useState(null);
  const [offline, setOffline] = useState(false);
  const lastTitle = useRef(undefined);

  const loadSlow = useCallback(async () => {
    const [sched, songs] = await Promise.allSettled([publicApi.schedule(), publicApi.playlist()]);
    if (sched.status === 'fulfilled') setSchedule(sched.value);
    if (songs.status === 'fulfilled') setPlaylist(songs.value);
  }, []);

  const loadNowPlaying = useCallback(async () => {
    try {
      const data = await publicApi.nowPlaying();
      setNowPlaying(data);
      setFetchedAt(Date.now());
      setOffline(false);
      // A new song: refresh the playlist highlight now rather than in a minute
      const key = `${data.state}|${data.title}`;
      if (lastTitle.current !== undefined && lastTitle.current !== key) {
        publicApi.playlist().then(setPlaylist).catch(() => {});
      }
      lastTitle.current = key;
    } catch {
      setOffline(true);
    }
  }, []);

  useVisiblePolling(loadNowPlaying, NOW_PLAYING_MS);
  useVisiblePolling(loadSlow, SLOW_MS);

  useEffect(() => {
    document.title = SHOW_NAME;
  }, []);

  const isPlaying = nowPlaying && nowPlaying.state === 'playing';

  return (
    <div className="home">
      <div className="home-snow" aria-hidden="true">
        {FLAKES.map((f, i) => (
          <span key={i} className="home-flake" style={{
            left: `${f.left}%`, width: f.size, height: f.size,
            animationDuration: `${f.duration}s`, animationDelay: `${f.delay}s`,
            '--drift': `${f.drift}px`,
          }} />
        ))}
      </div>

      <header className="home-header">
        <div className="home-lights" aria-hidden="true">
          {BULBS.map((color, i) => (
            <span key={i} className={`home-bulb home-bulb-${color}`}
              style={{ animationDelay: `${(i % 6) * 0.35}s` }} />
          ))}
        </div>
        <a className="home-gear" href={ADMIN_URL} aria-label="Admin" title="Admin">
          <Gear />
        </a>
        <h1 className="home-title">{SHOW_NAME}</h1>
        <p className="home-subtitle">Lights synced to music</p>
      </header>

      <main className="home-main">
        <StatusCard nowPlaying={nowPlaying} fetchedAt={fetchedAt}
          schedule={schedule} offline={offline} />
        <Playlist playlist={playlist} isPlaying={isPlaying} />
      </main>

      <footer className="home-footer">Happy holidays!</footer>
    </div>
  );
}
