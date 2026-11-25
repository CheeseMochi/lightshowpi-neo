/**
 * LightShowPi Neo public site on Cloudflare Workers.
 *
 * Serves the built web UI (web/dist, as Workers static assets) and a cloud
 * copy of the Pi's public API, so visitors' phones never reach the Pi:
 *
 *   GET  /api/public/now-playing | schedule | playlist   latest snapshot from KV
 *   POST /ingest                                          the Pi pushes a snapshot
 *   /admin, /admin/*                                      redirect to the admin host
 *
 * The Pi (api/services/cloud_publisher.py) pushes when something visible
 * changes and every 5 minutes. If pushes stop for 15 minutes, now-playing
 * says "off" instead of freezing on the last song. Response bodies match the
 * Pi's /api/public/* models exactly.
 *
 * Bindings (wrangler.toml): SHOW_KV (KV namespace), ASSETS (static assets),
 * INGEST_KEY (secret), ADMIN_URL or ADMIN_HOST (vars).
 */

export const SNAPSHOT_KEY = 'snapshot';
export const STALE_AFTER_S = 15 * 60;   // no heartbeat for this long: the show is off
export const STALE_GRACE_S = 60;        // same grace as the Pi after a song should have ended
export const MAX_BODY_BYTES = 64 * 1024;
export const CACHE_SECONDS = 5;
const MEMO_MS = CACHE_SECONDS * 1000;   // per-isolate copy of the snapshot (saves KV reads)

const STATES = new Set(['playing', 'ambient', 'off']);
const DAYS = new Set(['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']);
const TIME_RE = /^\d{1,2}:\d{2}(:\d{2})?$/;
const MAX_TITLE = 200;
const MAX_NOTE = 500;
const MAX_SONGS = 1000;
const MAX_SHOWS = 50;

export const OFF = Object.freeze({ state: 'off', title: null, elapsed_s: null, duration_s: null });
export const EMPTY_SCHEDULE = Object.freeze({ note: null, tonight: null, shows: [] });
export const EMPTY_PLAYLIST = Object.freeze({ songs: [], current: null });

// ---------------------------------------------------------------------------
// Payload validation: only the exact public shapes are stored
// ---------------------------------------------------------------------------

class Invalid extends Error {}

function isObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function onlyKeys(obj, allowed, where) {
  if (!isObject(obj)) throw new Invalid(`${where} must be an object`);
  for (const key of Object.keys(obj)) {
    if (!allowed.includes(key)) throw new Invalid(`${where}: unknown key ${key}`);
  }
}

function optNumber(v, where) {
  if (v === undefined || v === null) return null;
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) throw new Invalid(`${where} must be a number`);
  return v;
}

function optString(v, max, where) {
  if (v === undefined || v === null) return null;
  if (typeof v !== 'string' || v.length > max) throw new Invalid(`${where} must be a string up to ${max}`);
  return v;
}

function showTime(v, where) {
  onlyKeys(v, ['days', 'start', 'stop'], where);
  if (!Array.isArray(v.days) || v.days.length > 7 || !v.days.every((d) => DAYS.has(d))) {
    throw new Invalid(`${where}.days must be day names`);
  }
  for (const key of ['start', 'stop']) {
    if (typeof v[key] !== 'string' || !TIME_RE.test(v[key])) throw new Invalid(`${where}.${key} must be HH:MM`);
  }
  return { days: [...v.days], start: v.start, stop: v.stop };
}

/**
 * Check an ingest body and return a normalized copy, or throw Invalid.
 * Unknown keys anywhere are rejected, so nothing beyond the public views can
 * be stored or served.
 */
export function validatePayload(body) {
  onlyKeys(body, ['now_playing', 'song_started_at', 'schedule', 'playlist', 'sent_at'], 'body');
  for (const key of ['now_playing', 'schedule', 'playlist', 'sent_at']) {
    if (!(key in body)) throw new Invalid(`body: missing ${key}`);
  }

  const np = body.now_playing;
  onlyKeys(np, ['state', 'title', 'elapsed_s', 'duration_s'], 'now_playing');
  if (!STATES.has(np.state)) throw new Invalid('now_playing.state must be playing, ambient or off');
  const nowPlaying = {
    state: np.state,
    title: optString(np.title, MAX_TITLE, 'now_playing.title'),
    elapsed_s: optNumber(np.elapsed_s, 'now_playing.elapsed_s'),
    duration_s: optNumber(np.duration_s, 'now_playing.duration_s'),
  };

  const sentAt = optNumber(body.sent_at, 'sent_at');
  if (sentAt === null) throw new Invalid('sent_at must be a number');

  const sc = body.schedule;
  onlyKeys(sc, ['note', 'tonight', 'shows'], 'schedule');
  const shows = sc.shows === undefined ? [] : sc.shows;
  if (!Array.isArray(shows) || shows.length > MAX_SHOWS) throw new Invalid('schedule.shows must be a list');
  const schedule = {
    note: optString(sc.note, MAX_NOTE, 'schedule.note'),
    tonight: sc.tonight == null ? null : showTime(sc.tonight, 'schedule.tonight'),
    shows: shows.map((s, i) => showTime(s, `schedule.shows[${i}]`)),
  };

  const pl = body.playlist;
  onlyKeys(pl, ['songs', 'current'], 'playlist');
  const songs = pl.songs === undefined ? [] : pl.songs;
  if (!Array.isArray(songs) || songs.length > MAX_SONGS) throw new Invalid('playlist.songs must be a list');
  songs.forEach((s, i) => {
    if (typeof s !== 'string' || s.length > MAX_TITLE) {
      throw new Invalid(`playlist.songs[${i}] must be a string up to ${MAX_TITLE}`);
    }
  });
  let current = null;
  if (pl.current !== undefined && pl.current !== null) {
    if (!Number.isInteger(pl.current) || pl.current < 0 || pl.current >= songs.length) {
      throw new Invalid('playlist.current must be an index into songs');
    }
    current = pl.current;
  }

  return {
    now_playing: nowPlaying,
    song_started_at: optNumber(body.song_started_at, 'song_started_at'),
    schedule,
    playlist: { songs: [...songs], current },
    sent_at: sentAt,
  };
}

// ---------------------------------------------------------------------------
// Views computed at read time
// ---------------------------------------------------------------------------

function round1(x) {
  return Math.round(x * 10) / 10;
}

/**
 * Now-playing from the stored snapshot, live at ``nowS`` (epoch seconds).
 *
 * Elapsed time advances from the song's start between pushes. The Pi's clock
 * may differ from Cloudflare's, so the start is shifted by the difference
 * between when the Pi sent the snapshot and when the Worker received it.
 */
export function nowPlayingFrom(snapshot, nowS) {
  if (!snapshot || typeof snapshot.received_at !== 'number') return { ...OFF };
  if (nowS - snapshot.received_at > STALE_AFTER_S) return { ...OFF };
  const np = snapshot.now_playing || OFF;
  if (np.state !== 'playing') return { ...OFF, state: np.state };

  let elapsed = np.elapsed_s;
  const duration = np.duration_s;
  if (typeof snapshot.song_started_at === 'number') {
    const skew = snapshot.received_at - (snapshot.sent_at ?? snapshot.received_at);
    elapsed = nowS - (snapshot.song_started_at + skew);
    if (duration != null && elapsed > duration + STALE_GRACE_S) return { ...OFF };
    elapsed = Math.max(0, elapsed);
    if (duration != null) elapsed = Math.min(elapsed, duration);
  }
  return {
    state: 'playing',
    title: np.title ?? null,
    elapsed_s: elapsed == null ? null : round1(elapsed),
    duration_s: duration == null ? null : round1(duration),
  };
}

export function scheduleFrom(snapshot) {
  return (snapshot && snapshot.schedule) || { ...EMPTY_SCHEDULE, shows: [] };
}

export function playlistFrom(snapshot) {
  return (snapshot && snapshot.playlist) || { ...EMPTY_PLAYLIST, songs: [] };
}

// ---------------------------------------------------------------------------
// Auth: constant-time bearer check (both sides hashed so lengths match)
// ---------------------------------------------------------------------------

async function sha256(text) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)));
}

function equalBytes(a, b) {
  if (crypto.subtle.timingSafeEqual) return crypto.subtle.timingSafeEqual(a, b);
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.min(a.length, b.length); i += 1) diff |= a[i] ^ b[i];
  return diff === 0;
}

/** True only when INGEST_KEY is set and the request carries it as a bearer token. */
export async function isAuthorized(request, ingestKey) {
  if (typeof ingestKey !== 'string' || ingestKey.length === 0) return false;
  const header = request.headers.get('Authorization') || '';
  const match = /^Bearer (.+)$/.exec(header);
  if (!match) return false;
  const [given, expected] = await Promise.all([sha256(match[1]), sha256(ingestKey)]);
  return equalBytes(given, expected);
}

// ---------------------------------------------------------------------------
// Snapshot storage (one KV key; a short per-isolate memo saves reads)
// ---------------------------------------------------------------------------

let memo = { at: 0, value: undefined };

/** Forget the per-isolate copy (tests). */
export function resetMemo() {
  memo = { at: 0, value: undefined };
}

async function loadSnapshot(env) {
  const now = Date.now();
  if (memo.value !== undefined && now - memo.at < MEMO_MS) return memo.value;
  let value = null;
  try {
    value = await env.SHOW_KV.get(SNAPSHOT_KEY, 'json');
  } catch (e) {
    console.error('KV read failed', e);
  }
  memo = { at: now, value };
  return value;
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

const PUBLIC_HEADERS = { 'Cache-Control': `public, max-age=${CACHE_SECONDS}` };
const NO_STORE = { 'Cache-Control': 'no-store' };

function adminUrl(env) {
  if (env.ADMIN_URL) return env.ADMIN_URL.replace(/\/$/, '');
  if (env.ADMIN_HOST) return `https://${env.ADMIN_HOST}/admin`;
  return '';
}

async function handleIngest(request, env) {
  if (request.method !== 'POST') return json({ detail: 'Method not allowed' }, 405, { Allow: 'POST', ...NO_STORE });
  if (!(await isAuthorized(request, env.INGEST_KEY))) {
    return json({ detail: 'Unauthorized' }, 401, { 'WWW-Authenticate': 'Bearer', ...NO_STORE });
  }
  const length = Number(request.headers.get('Content-Length') || 0);
  if (length > MAX_BODY_BYTES) return json({ detail: 'Payload too large' }, 413, NO_STORE);
  const text = await request.text();
  if (new TextEncoder().encode(text).length > MAX_BODY_BYTES) {
    return json({ detail: 'Payload too large' }, 413, NO_STORE);
  }

  let snapshot;
  try {
    snapshot = validatePayload(JSON.parse(text));
  } catch (e) {
    const detail = e instanceof Invalid ? e.message : 'Body must be JSON';
    return json({ detail }, 400, NO_STORE);
  }
  snapshot.received_at = Date.now() / 1000;
  await env.SHOW_KV.put(SNAPSHOT_KEY, JSON.stringify(snapshot));
  memo = { at: Date.now(), value: snapshot };
  return new Response(null, { status: 204, headers: NO_STORE });
}

async function handlePublic(request, env, name) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return json({ detail: 'Method not allowed' }, 405, { Allow: 'GET, HEAD' });
  }
  const snapshot = await loadSnapshot(env);
  let body;
  if (name === 'now-playing') body = nowPlayingFrom(snapshot, Date.now() / 1000);
  else if (name === 'schedule') body = scheduleFrom(snapshot);
  else body = playlistFrom(snapshot);
  return json(body, 200, PUBLIC_HEADERS);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    const publicMatch = /^\/api\/public\/(now-playing|schedule|playlist)\/?$/.exec(path);
    if (publicMatch) return handlePublic(request, env, publicMatch[1]);
    if (path === '/ingest') return handleIngest(request, env);
    if (path === '/api' || path.startsWith('/api/')) return json({ detail: 'Not Found' }, 404);

    if (path === '/admin' || path.startsWith('/admin/')) {
      const target = adminUrl(env);
      if (!target) return json({ detail: 'Not Found' }, 404);
      return Response.redirect(`${target}${path.slice('/admin'.length)}${url.search}`, 302);
    }

    if (env.ASSETS) return env.ASSETS.fetch(request);
    return json({ detail: 'Not Found' }, 404);
  },
};
