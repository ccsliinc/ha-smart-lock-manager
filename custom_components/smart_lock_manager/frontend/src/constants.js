// Shared, behavior-inert constants for the Smart Lock Manager panel.
// Extracted verbatim from the original single-file bundle so the built output
// is byte-equivalent in meaning. Nothing here reads instance state.

// Day labels for the Mon..Sun (0..6) checkbox rows used by Business Hours and
// Scheduled Auto-Lock. Centralized so both blocks render identically.
export const ZS_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

// Lovelace custom-card registration metadata (window.customCards entry).
export const CUSTOM_CARD_META = {
  type: 'smart-lock-manager-panel',
  name: 'Smart Lock Manager Panel',
  description: 'Advanced panel for managing smart locks with scheduling and usage tracking'
};
