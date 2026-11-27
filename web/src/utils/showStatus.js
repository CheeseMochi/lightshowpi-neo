/**
 * Pure helpers for the Home page: turning the public schedule into a
 * "next show" line, and formatting times. Times in the schedule are the Pi's
 * local time; visitors are standing outside, so their phone's clock is the
 * same time zone.
 */

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const DAY_LONG = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

/** "17:30" -> minutes after midnight, or null. */
export function toMinutes(hhmm) {
  const match = /^(\d{1,2}):(\d{2})/.exec(hhmm || '');
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

/** "17:30" -> "5:30 PM" (or "5 PM" on the hour). */
export function formatTime(hhmm) {
  const minutes = toMinutes(hhmm);
  if (minutes === null) return hhmm || '';
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  const suffix = h < 12 ? 'AM' : 'PM';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m === 0 ? `${h12} ${suffix}` : `${h12}:${String(m).padStart(2, '0')} ${suffix}`;
}

/** "3:07" from seconds. */
export function formatClock(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '';
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function inWindow(nowMin, start, stop) {
  if (start === null || stop === null) return false;
  if (stop > start) return nowMin >= start && nowMin < stop;
  return nowMin >= start || nowMin < stop; // runs past midnight
}

/**
 * What to say when the lights are off.
 *
 * @param {object|null} schedule  /api/public/schedule response
 * @param {Date} now
 * @returns {{headline: string, detail: string|null}}
 */
export function offMessage(schedule, now = new Date()) {
  const shows = (schedule && schedule.shows) || [];
  const note = (schedule && schedule.note) || null;
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const today = now.getDay();

  const showsOn = (day) => shows.filter((s) => (s.days || []).includes(DAY_NAMES[day]));
  const earliest = (list) =>
    list.slice().sort((a, b) => (toMinutes(a.start) ?? 0) - (toMinutes(b.start) ?? 0))[0];

  // Today: still to come, or inside a window (the show should be starting)
  const todays = showsOn(today);
  const upcoming = earliest(todays.filter((s) => (toMinutes(s.start) ?? -1) > nowMin));
  const current = todays.find((s) => inWindow(nowMin, toMinutes(s.start), toMinutes(s.stop)));
  if (current) {
    return { headline: 'The show starts any moment', detail: note };
  }
  if (upcoming) {
    return { headline: `Next show tonight at ${formatTime(upcoming.start)}`, detail: note };
  }

  // Later this week
  for (let offset = 1; offset <= 7; offset += 1) {
    const day = (today + offset) % 7;
    const next = earliest(showsOn(day));
    if (next) {
      const when = offset === 1 ? 'tomorrow' : DAY_LONG[day];
      const over = todays.length > 0 ? "Show's over for tonight. " : '';
      return { headline: `${over}See you ${when} at ${formatTime(next.start)}`, detail: note };
    }
  }

  if (note) {
    return { headline: 'The lights are resting', detail: note };
  }
  return { headline: 'The lights are resting', detail: 'Check back after dark!' };
}

/**
 * "Artist · Album · 2013" from whichever of the song's tag details are known;
 * '' when there are none.
 */
export function songByline(song, { album = true } = {}) {
  if (!song) return '';
  return [song.artist, album ? song.album : null, song.year].filter(Boolean).join(' · ');
}

function times(n) {
  return n === 1 ? 'once' : `${n} times`;
}

/**
 * Short, friendly lines for the current song's play counts (all time). The
 * count includes the play happening now. Returns [] when there are no stats.
 */
export function statLines(stats) {
  if (!stats || !(stats.plays > 0)) return [];
  if (stats.plays === 1) return [{ icon: '🌟', text: 'First time on the show!' }];
  const lines = [{ icon: '🎵', text: `Played ${stats.plays} times` }];
  if (stats.completions > 0) lines.push({ icon: '🎉', text: `Heard to the end ${times(stats.completions)}` });
  lines.push(stats.skips > 0
    ? { icon: '⏭️', text: `Skipped ${times(stats.skips)}` }
    : { icon: '💯', text: 'Never skipped' });
  return lines;
}
