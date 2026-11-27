import { env } from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import worker, {
  MAX_BODY_BYTES,
  OFF,
  SNAPSHOT_KEY,
  STALE_AFTER_S,
  isAuthorized,
  nowPlayingFrom,
  resetMemo,
  validatePayload,
} from '../src/index.js';

const KEY = env.INGEST_KEY;
const T0 = 1_800_000_000; // epoch seconds

function payload(overrides = {}) {
  return {
    now_playing: { state: 'playing', title: 'Carol of the Bells', elapsed_s: 20, duration_s: 120 },
    song_started_at: T0 - 20,
    schedule: { note: 'Nightly', tonight: { days: ['Fri'], start: '17:30', stop: '22:30' },
      shows: [{ days: ['Fri'], start: '17:30', stop: '22:30' }] },
    playlist: { songs: ['Carol of the Bells', 'Deck the Halls'], current: 0 },
    sent_at: T0,
    ...overrides,
  };
}

function call(path, init = {}, bindings = {}) {
  const assets = { fetch: vi.fn(async () => new Response('<html>home</html>', { status: 200 })) };
  const testEnv = { ...env, ASSETS: assets, ...bindings };
  return worker.fetch(new Request(`https://show.example.com${path}`, init), testEnv).then((res) => {
    res.assets = assets;
    return res;
  });
}

function ingest(body, { key = KEY, headers = {} } = {}, bindings = {}) {
  return call('/ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(key !== null ? { Authorization: `Bearer ${key}` } : {}), ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  }, bindings);
}

beforeEach(async () => {
  resetMemo();
  await env.SHOW_KV.delete(SNAPSHOT_KEY);
  vi.useFakeTimers({ toFake: ['Date'] }); // only the clock; KV I/O keeps real timers
  vi.setSystemTime(T0 * 1000);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('ingest auth', () => {
  it('accepts the right key and stores the snapshot', async () => {
    const res = await ingest(payload());
    expect(res.status).toBe(204);
    const stored = await env.SHOW_KV.get(SNAPSHOT_KEY, 'json');
    expect(stored.now_playing.title).toBe('Carol of the Bells');
    expect(stored.received_at).toBeCloseTo(T0, 0);
  });

  it.each([
    ['no header', null],
    ['wrong key', 'nope'],
    ['empty bearer', ''],
    ['key prefix', KEY.slice(0, 10)],
  ])('rejects %s', async (_name, key) => {
    const res = await ingest(payload(), { key });
    expect(res.status).toBe(401);
    expect(await env.SHOW_KV.get(SNAPSHOT_KEY)).toBeNull();
  });

  it('rejects everything when INGEST_KEY is unset', async () => {
    expect((await ingest(payload(), { key: '' }, { INGEST_KEY: '' })).status).toBe(401);
    expect((await ingest(payload(), { key: 'x' }, { INGEST_KEY: undefined })).status).toBe(401);
    expect(await isAuthorized(new Request('https://x', { headers: { Authorization: 'Bearer ' } }), '')).toBe(false);
  });

  it('only allows POST', async () => {
    expect((await call('/ingest')).status).toBe(405);
  });
});

describe('ingest schema', () => {
  it.each([
    ['unknown top-level key', payload({ song_path: '/home/pi/music/x.mp3' })],
    ['unknown now_playing key', payload({ now_playing: { state: 'playing', title: 'x', path: '/x' } })],
    ['bad state', payload({ now_playing: { state: 'exploded' } })],
    ['unknown schedule key', payload({ schedule: { note: null, shows: [], admin: 'x' } })],
    ['unknown show key', payload({ schedule: { shows: [{ days: ['Fri'], start: '17:30', stop: '22:30', id: 1 }] } })],
    ['bad day', payload({ schedule: { shows: [{ days: ['Funday'], start: '17:30', stop: '22:30' }] } })],
    ['bad time', payload({ schedule: { shows: [{ days: ['Fri'], start: '5pm', stop: '22:30' }] } })],
    ['non-string song', payload({ playlist: { songs: [1] } })],
    ['current out of range', payload({ playlist: { songs: ['a'], current: 3 } })],
    ['unknown playlist key', payload({ playlist: { songs: [], paths: ['/x'] } })],
    ['missing playlist', (() => { const p = payload(); delete p.playlist; return p; })()],
    ['negative elapsed', payload({ now_playing: { state: 'playing', elapsed_s: -1 } })],
    ['array body', []],
    ['long artist', payload({ now_playing: { state: 'playing', artist: 'x'.repeat(500) } })],
    ['numeric year', payload({ now_playing: { state: 'playing', year: 2013 } })],
    ['unknown stats key', payload({ now_playing: { state: 'playing', stats: { plays: 1, path: '/x' } } })],
    ['negative count', payload({ now_playing: { state: 'playing', stats: { plays: -1 } } })],
    ['fractional count', payload({ now_playing: { state: 'playing', stats: { skips: 1.5 } } })],
    ['unknown detail key', payload({ playlist: { songs: ['a'], details: [{ artist: 'x', path: '/x' }] } })],
    ['more details than songs', payload({ playlist: { songs: ['a'], details: [{}, {}] } })],
  ])('rejects %s', async (_name, body) => {
    const res = await ingest(body);
    expect(res.status).toBe(400);
    expect(await env.SHOW_KV.get(SNAPSHOT_KEY)).toBeNull();
  });

  it('rejects non-JSON', async () => {
    expect((await ingest('not json')).status).toBe(400);
  });

  it('rejects oversized bodies', async () => {
    const big = payload({ schedule: { note: 'x'.repeat(MAX_BODY_BYTES), shows: [] } });
    expect((await ingest(big)).status).toBe(413);
  });

  it('normalizes missing optional fields', () => {
    const value = validatePayload({
      now_playing: { state: 'off' }, schedule: {}, playlist: {}, sent_at: T0,
    });
    expect(value.now_playing).toEqual({ ...OFF });
    expect(value.schedule).toEqual({ note: null, tonight: null, shows: [] });
    expect(value.playlist).toEqual({ songs: [], current: null, details: [] });
    expect(value.song_started_at).toBeNull();
  });
});

describe('song details', () => {
  const detailed = () => payload({
    now_playing: { state: 'playing', title: 'Carol of the Bells', elapsed_s: 20, duration_s: 120,
      artist: 'EvilWezil', album: 'XMAS Compilation 2010', year: '2013', pattern: 'Onset bands',
      stats: { plays: 12, completions: 10, skips: 2 } },
    playlist: { songs: ['Carol of the Bells', 'Deck the Halls'], current: 0,
      details: [{ artist: 'EvilWezil', year: '2013' }, { artist: null, year: null }] },
  });

  it('stores and serves artist, album, year, pattern and stats', async () => {
    expect((await ingest(detailed())).status).toBe(204);
    const np = await (await call('/api/public/now-playing')).json();
    expect(np).toEqual({
      state: 'playing', title: 'Carol of the Bells', elapsed_s: 20, duration_s: 120,
      artist: 'EvilWezil', album: 'XMAS Compilation 2010', year: '2013', pattern: 'Onset bands',
      stats: { plays: 12, completions: 10, skips: 2 },
    });
    const playlist = await (await call('/api/public/playlist')).json();
    expect(playlist.details).toEqual([{ artist: 'EvilWezil', year: '2013' }, { artist: null, year: null }]);
  });

  it('drops the details when the show is off', async () => {
    await ingest(detailed());
    vi.setSystemTime((T0 + STALE_AFTER_S + 1) * 1000);
    resetMemo();
    expect(await (await call('/api/public/now-playing')).json()).toEqual({ ...OFF });
  });

  it('still accepts a Pi that sends no details (older code)', async () => {
    expect((await ingest(payload())).status).toBe(204);
    const np = await (await call('/api/public/now-playing')).json();
    expect(np.artist).toBeNull();
    expect(np.stats).toBeNull();
  });

  it('serves details as [] for a snapshot stored before details existed', async () => {
    await env.SHOW_KV.put(SNAPSHOT_KEY, JSON.stringify({
      ...payload(), playlist: { songs: ['a'], current: null }, received_at: T0 }));
    resetMemo();
    expect(await (await call('/api/public/playlist')).json()).toEqual({ songs: ['a'], current: null, details: [] });
  });

  it('fills missing stats counts with 0', () => {
    const value = validatePayload(payload({ now_playing: { state: 'playing', stats: { plays: 3 } } }));
    expect(value.now_playing.stats).toEqual({ plays: 3, completions: 0, skips: 0 });
  });
});

describe('public endpoints', () => {
  it('serve the same shapes as the Pi when nothing was pushed', async () => {
    const np = await (await call('/api/public/now-playing')).json();
    expect(np).toEqual({ ...OFF });
    expect(await (await call('/api/public/schedule')).json()).toEqual({ note: null, tonight: null, shows: [] });
    expect(await (await call('/api/public/playlist')).json()).toEqual({ songs: [], current: null, details: [] });
  });

  it('serve the latest snapshot with a short cache', async () => {
    await ingest(payload());
    const res = await call('/api/public/now-playing');
    expect(res.headers.get('Cache-Control')).toBe('public, max-age=5');
    expect(await res.json()).toEqual(
      { ...OFF, state: 'playing', title: 'Carol of the Bells', elapsed_s: 20, duration_s: 120 });
    const playlist = await (await call('/api/public/playlist')).json();
    expect(playlist).toEqual({ songs: ['Carol of the Bells', 'Deck the Halls'], current: 0, details: [] });
    const schedule = await (await call('/api/public/schedule')).json();
    expect(Object.keys(schedule).sort()).toEqual(['note', 'shows', 'tonight']);
  });

  it('read from KV when the isolate has no copy', async () => {
    await ingest(payload());
    resetMemo();
    expect((await (await call('/api/public/now-playing')).json()).state).toBe('playing');
  });

  it('advance elapsed time between pushes', async () => {
    await ingest(payload());
    vi.setSystemTime((T0 + 30) * 1000);
    resetMemo();
    const np = await (await call('/api/public/now-playing')).json();
    expect(np.elapsed_s).toBe(50);
  });

  it('unknown api paths are 404 JSON, not the app', async () => {
    const res = await call('/api/public/secrets');
    expect(res.status).toBe(404);
    expect(res.assets.fetch).not.toHaveBeenCalled();
  });
});

describe('staleness', () => {
  it('reports off when heartbeats stop for 15 minutes', async () => {
    await ingest(payload({ now_playing: { state: 'ambient' }, song_started_at: null }));
    vi.setSystemTime((T0 + STALE_AFTER_S - 1) * 1000);
    resetMemo();
    expect((await (await call('/api/public/now-playing')).json()).state).toBe('ambient');
    vi.setSystemTime((T0 + STALE_AFTER_S + 1) * 1000);
    resetMemo();
    expect(await (await call('/api/public/now-playing')).json()).toEqual({ ...OFF });
    // times and songs are still worth showing
    expect((await (await call('/api/public/playlist')).json()).songs).toHaveLength(2);
  });

  it('reports off once the song should have ended (Pi died mid-song)', () => {
    const snap = { ...validatePayload(payload()), received_at: T0 };
    expect(nowPlayingFrom(snap, T0 + 100).elapsed_s).toBe(120);    // clamped to duration
    expect(nowPlayingFrom(snap, T0 + 100 + 61).state).toBe('off');
  });

  it('corrects for the Pi clock being off', () => {
    // Pi clock 300 s fast: it sent at T0+300 and the song started 20 s before that
    const snap = { ...validatePayload(payload({ sent_at: T0 + 300, song_started_at: T0 + 280 })),
      received_at: T0 };
    expect(nowPlayingFrom(snap, T0 + 5).elapsed_s).toBe(25);
  });

  it('keeps the elapsed the Pi sent when there is no start time', () => {
    const snap = { ...validatePayload(payload({ song_started_at: null,
      now_playing: { state: 'playing', title: null, elapsed_s: null, duration_s: null } })), received_at: T0 };
    expect(nowPlayingFrom(snap, T0 + 5)).toEqual({ ...OFF, state: 'playing' });
  });
});

describe('routing', () => {
  it('redirects /admin to the admin host', async () => {
    const res = await call('/admin/settings?x=1');
    expect(res.status).toBe(302);
    expect(res.headers.get('Location')).toBe('https://lights.example.com/admin/settings?x=1');
    expect((await call('/admin')).headers.get('Location')).toBe('https://lights.example.com/admin');
  });

  it('builds the admin URL from ADMIN_HOST', async () => {
    const res = await call('/admin', {}, { ADMIN_URL: '', ADMIN_HOST: 'admin.example.org' });
    expect(res.headers.get('Location')).toBe('https://admin.example.org/admin');
  });

  it('404s /admin when no admin host is configured', async () => {
    expect((await call('/admin', {}, { ADMIN_URL: '', ADMIN_HOST: '' })).status).toBe(404);
  });

  it('serves everything else from the static assets', async () => {
    const res = await call('/');
    expect(await res.text()).toContain('home');
    expect(res.assets.fetch).toHaveBeenCalledOnce();
  });
});
