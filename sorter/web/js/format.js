/** Formatting for displayed values (sizes, dates, durations). */

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

export function bytes(n) {
  if (!n) return '0 B';
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), UNITS.length - 1);
  return (n / 1024 ** i).toFixed(i ? 1 : 0) + ' ' + UNITS[i];
}

export const fmtDate = ts => new Date(ts * 1000).toLocaleDateString('en-GB',
  { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

export const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString('en-GB',
  { hour: '2-digit', minute: '2-digit' });

export function fmtDuration(seconds) {
  if (!isFinite(seconds)) return '';
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
