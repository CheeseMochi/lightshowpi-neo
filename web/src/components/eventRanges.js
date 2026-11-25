/**
 * Time ranges and formatting shared by the Activity and Insights pages.
 */

export const RANGES = [
  { value: '1', label: 'Last 24 hours' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: 'all', label: 'All time' }
];

/** ISO start time for a range value ('all' -> undefined). */
export function rangeSince(value) {
  if (!value || value === 'all') return undefined;
  const days = Number(value);
  return new Date(Date.now() - days * 24 * 3600 * 1000).toISOString();
}

/** "Sat 12 Dec, 19:05" in the browser's locale. */
export function formatTs(ts) {
  if (ts === null || ts === undefined) return '';
  return new Date(ts * 1000).toLocaleString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit'
  });
}

/** Seconds -> "3:05". */
export function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return '';
  const s = Math.max(0, Math.round(Number(seconds)));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** 0.234 -> "23%"; null -> "–". */
export function formatRate(rate) {
  return rate === null || rate === undefined ? '–' : `${Math.round(rate * 100)}%`;
}
