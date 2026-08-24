// Pure formatting/escaping helpers for the Smart Lock Manager panel.
// Each function is a verbatim extraction of the original class method body
// (which referenced no instance state). The class keeps thin methods that
// delegate here, so every original call site and method name is preserved.

// HTML-escape an arbitrary value for safe interpolation into markup.
//   - Inputs: value (any).
//   - Outputs: escaped string ('' for null/undefined).
export function escHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Format an ISO timestamp into a local, human-friendly date+time string.
//   - Inputs: iso (str|null).
//   - Outputs: local string; the original iso on parse failure; '' if falsy.
export function formatAccessLogTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString([], {
      month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit'
    });
  } catch (e) {
    return iso;
  }
}

// Format an ISO timestamp as a local short time, e.g. "1:20 PM".
//   - Inputs: iso (str|null).
//   - Outputs: short local time string; '' if falsy or unparseable.
export function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

// Compute hours from now until the next occurrence of a zone's open time,
// for the "Until morning" snooze, clamped to [0.25, 24].
//   - Inputs: zone (zone obj).
//   - Outputs: float hours.
export function untilMorningHours(zone) {
  const bh = zone && zone.settings && zone.settings.business_hours;
  let open = bh && typeof bh.open_time === 'string' && bh.open_time
    ? bh.open_time : '08:30';
  const parts = String(open).split(':');
  const hh = parseInt(parts[0], 10) || 0;
  const mm = parseInt(parts[1], 10) || 0;
  const now = new Date();
  const target = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hh, mm, 0, 0);
  if (target <= now) target.setDate(target.getDate() + 1);
  let hours = (target - now) / 3600000;
  if (hours > 24) hours = 24;
  if (hours < 0.25) hours = 0.25;
  return hours;
}
