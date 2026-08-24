// Pure presentation helpers for the Smart Lock Manager panel: they map API
// payload shapes to display metadata (icons, colors, labels) with no instance
// state. Verbatim extractions of the original class method bodies; the class
// keeps thin delegating methods so every call site and name is preserved.

// Build display fields for a lock slot from its backend slot_details.
//   - Inputs: lock (lock entity), slot (slot number).
//   - Outputs: { title, status, color, statusName, description }.
export function slotDisplayInfo(lock, slot) {
  const details = lock?.attributes?.slot_details?.[`slot_${slot}`];

  // All logic is done in backend - frontend just displays
  return {
    title: details?.display_title || `Slot ${slot}:`,
    status: details?.status?.label || details?.slot_status || 'Click to configure',
    color: details?.status?.color || details?.status_color || '#9e9e9e',
    statusName: details?.status?.name || 'UNKNOWN',
    description: details?.status?.description || details?.status_reason || ''
  };
}

// Build the icon + color + attribution for a single access-log entry.
//   - Inputs: entry (access-log object).
//   - Outputs: { icon, color, attr }.
export function accessLogPresentation(entry) {
  const action = entry?.action || 'unknown';
  const source = entry?.source || '';

  let icon = 'mdi:help-circle-outline';
  let color = '#9e9e9e';
  if (action === 'locked') { icon = 'mdi:lock'; color = '#4a7c2a'; }
  else if (action === 'unlocked') { icon = 'mdi:lock-open'; color = '#cc3333'; }
  else if (action === 'jammed') { icon = 'mdi:lock-alert'; color = '#f44336'; }

  // Human-friendly attribution / source label.
  let attr;
  if (entry?.user_name) {
    attr = entry.slot != null ? `${entry.user_name} (slot ${entry.slot})` : entry.user_name;
  } else if (source === 'manual') {
    attr = 'Thumbturn';
  } else if (source === 'rf') {
    attr = 'App/Remote';
  } else if (source === 'auto') {
    attr = 'Auto-lock';
  } else if (source === 'keypad') {
    attr = 'Keypad';
  } else {
    attr = source || '—';
  }

  return { icon, color, attr };
}

// Map a developer/system alert to severity icon/color/label.
//   - Inputs: alert (alert object).
//   - Outputs: { icon, color, label }.
export function devAlertPresentation(alert) {
  if (alert.is_recovery) {
    return { icon: 'mdi:check-circle', color: '#4a7c2a', label: 'Recovery' };
  }
  const sev = (alert.severity || '').toUpperCase();
  if (sev === 'CRIT') {
    return { icon: 'mdi:alert-octagon', color: '#cc3333', label: 'Critical' };
  }
  return { icon: 'mdi:alert', color: '#f0a020', label: 'Warning' };
}

// Map a zone code-slot (from the zones API) to display color/title/status,
// mirroring the backend color hierarchy: grey disabled -> green active.
//   - Inputs: slot (zone code_slot object).
//   - Outputs: { color, title, status }.
export function zoneSlotDisplay(slot) {
  const name = slot.user_name || '';
  const title = `Slot ${slot.slot_number}${name ? ': ' + name : ''}`;
  let color = '#9e9e9e';
  let status = 'Click to configure';
  if (slot.has_code) {
    // sync_status (synced/pending/error) is the authoritative live field
    // derived from member locks; fall back to is_synced for older payloads.
    const syncStatus = slot.sync_status
      || (slot.is_synced ? 'synced' : 'pending');
    if (!slot.is_active) {
      color = '#9e9e9e';
      status = 'Disabled';
    } else if (syncStatus === 'error') {
      color = '#cc3333';
      status = 'Sync failed';
    } else if (syncStatus === 'pending') {
      color = '#f0a020';
      status = 'Syncing…';
    } else {
      color = '#4a7c2a';
      status = 'Active';
    }
    if (slot.max_uses != null && slot.max_uses >= 0) {
      status += ` · ${slot.remaining_uses}/${slot.max_uses} left`;
    }
  }
  return { color, title, status };
}
