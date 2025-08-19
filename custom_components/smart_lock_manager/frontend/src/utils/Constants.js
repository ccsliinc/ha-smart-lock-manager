// Shared constants for Smart Lock Manager frontend

export const SLOT_COUNT = 10;

export const SLOT_COLORS = {
  DISABLED: '#9e9e9e',
  OUTSIDE_HOURS: '#2196f3',
  SYNC_ERROR: '#f44336',
  SYNCHRONIZED: '#4caf50',
  SYNCING: '#ff9800'
};

export const SLOT_STATUS = {
  DISABLED: 'disabled',
  OUTSIDE_HOURS: 'outside_hours',
  SYNC_ERROR: 'sync_error',
  SYNCHRONIZED: 'synchronized',
  SYNCING: 'syncing'
};

export const SERVICES = {
  DOMAIN: 'smart_lock_manager',
  SET_CODE_ADVANCED: 'set_code_advanced',
  CLEAR_CODE: 'clear_code',
  ENABLE_SLOT: 'enable_slot',
  DISABLE_SLOT: 'disable_slot',
  UPDATE_LOCK_SETTINGS: 'update_lock_settings',
  SYNC_CHILD_LOCKS: 'sync_child_locks',
  READ_CODES: 'read_codes'
};

export const DAYS_OF_WEEK = [
  { value: 0, label: 'Monday' },
  { value: 1, label: 'Tuesday' },
  { value: 2, label: 'Wednesday' },
  { value: 3, label: 'Thursday' },
  { value: 4, label: 'Friday' },
  { value: 5, label: 'Saturday' },
  { value: 6, label: 'Sunday' }
];

export const HOURS_OF_DAY = Array.from({ length: 24 }, (_, i) => ({
  value: i,
  label: `${i.toString().padStart(2, '0')}:00`
}));

export const PIN_VALIDATION = {
  MIN_LENGTH: 4,
  MAX_LENGTH: 8,
  PATTERN: /^\d*$/
};

export const EVENT_TYPES = {
  CODES_READ: 'smart_lock_manager_codes_read'
};