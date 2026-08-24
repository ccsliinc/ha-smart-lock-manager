// Panel stylesheet. Extracted verbatim from the original render()
// <style> block. The single dynamic value (modal overlay display) is passed
// through as the panel instance so the rendered CSS is byte-for-byte identical.
//   - Inputs: self (SmartLockManagerPanel instance).
//   - Outputs: CSS string for injection between <style></style>.
export function panelStyles(self) {
  return `
        :host {
          display: block;
          padding: 16px;
          background: var(--primary-background-color);
          color: var(--primary-text-color);
          font-family: var(--paper-font-body1_-_font-family);
        }

        .header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 24px;
          padding-bottom: 16px;
          border-bottom: 1px solid var(--divider-color);
        }

        .header-left {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .refresh-container {
          display: flex;
          align-items: center;
          margin-right: 16px;
        }

        .refresh-link {
          display: flex;
          align-items: center;
          gap: 2px;
          color: var(--primary-text-color);
          cursor: pointer;
          font-size: 14px;
          opacity: 0.7;
          transition: opacity 0.2s;
          height: 20px;
        }

        .refresh-link span {
          height: 20px;
          line-height: 20px;
          display: flex;
          align-items: center;
        }

        .refresh-link:hover {
          opacity: 1;
        }

        .lock-title-section {
          display: flex;
          align-items: flex-start;
          gap: 12px;
          width: 100%;
        }

        .lock-status-column {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 24px;
          min-width: 24px;
          flex-shrink: 0;
        }

        .lock-header-column {
          flex: 1;
          min-width: 0;
        }

        .lock-title-row {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .lock-connection-status {
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .header ha-icon {
          margin-right: 16px;
          color: var(--primary-color);
          font-size: 24px;
        }

        .header h1 {
          margin: 0;
          font-size: 24px;
          font-weight: 400;
        }

        .locks-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 20px;
          margin-bottom: 24px;
        }

        .lock-card {
          background: var(--card-background-color);
          border-radius: 12px;
          padding: 20px;
          box-shadow: var(--ha-card-box-shadow);
          border: 1px solid var(--divider-color);
        }

        .lock-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
          padding-bottom: 12px;
          border-bottom: 1px solid var(--divider-color);
        }

        .lock-title {
          font-size: 18px;
          font-weight: 500;
          margin: 0;
          color: var(--primary-text-color);
        }

        .lock-header-right {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .lock-status {
          padding: 4px 8px;
          border-radius: 4px;
          font-size: 12px;
          font-weight: 500;
          background: #6b8e6b;
          color: white;
        }

        .settings-btn {
          background: none;
          border: none;
          cursor: pointer;
          padding: 4px;
          border-radius: 4px;
          color: var(--secondary-text-color);
          transition: all 0.2s;
        }

        .settings-btn:hover {
          background: var(--primary-background-color);
          color: var(--primary-color);
        }

        .settings-btn ha-icon {
          font-size: 16px;
        }

        /* Saving Spinner */
        .saving-spinner {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          font-size: 12px;
          color: var(--primary-color);
          opacity: 0.8;
        }

        .spinner {
          width: 14px;
          height: 14px;
          border: 2px solid var(--divider-color);
          border-top: 2px solid var(--primary-color);
          border-radius: 50%;
          animation: spin 1s linear infinite;
        }

        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }

        /* ha-icon hover animations - properly target the SVG inside */
        @keyframes gearRotate {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }

        @keyframes broomSweep {
          0% { transform: rotate(-15deg); }
          50% { transform: rotate(15deg); }
          100% { transform: rotate(-15deg); }
        }

        @keyframes lockBounce {
          0% { transform: scale(1) rotate(0deg); filter: hue-rotate(0deg); }
          50% { transform: scale(1.2) rotate(90deg); filter: hue-rotate(-60deg); }
          100% { transform: scale(1) rotate(0deg); filter: hue-rotate(0deg); }
        }

        @keyframes lockBounceReverse {
          0% { transform: scale(1) rotate(0deg); filter: hue-rotate(0deg); }
          50% { transform: scale(1.2) rotate(-90deg); filter: hue-rotate(-60deg); }
          100% { transform: scale(1) rotate(0deg); filter: hue-rotate(0deg); }
        }

        @keyframes refreshSpin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }

        /* Target ha-icon elements directly for animations */
        .settings-btn:hover ha-icon {
          animation: gearRotate 3s linear infinite !important;
          transform-origin: center center !important;
        }

        .clear-all-btn:hover ha-icon {
          animation: broomSweep 0.8s ease-in-out infinite !important;
          transform-origin: center center !important;
        }

        .lock-toggle-btn:hover ha-icon[icon="mdi:lock"] {
          animation: lockBounce 1.2s ease-in-out infinite !important;
          transform-origin: center center !important;
        }

        .lock-toggle-btn:hover ha-icon[icon="mdi:lock-open"] {
          animation: lockBounceReverse 1.2s ease-in-out infinite !important;
          transform-origin: center center !important;
        }

        .refresh-btn:hover {
          opacity: 1 !important;
        }

        .refresh-btn:hover ha-icon {
          animation: refreshSpin 2s linear infinite !important;
          transform-origin: center center !important;
        }

        /* Override header ha-icon styles for buttons */
        .refresh-btn ha-icon {
          margin-right: 0 !important;
          font-size: inherit !important;
          color: inherit !important;
        }

        /* Ensure ha-icon elements can be transformed and centered */
        ha-icon {
          display: inline-block !important;
          transform-origin: center center !important;
        }

        /* ===== Zone card: Locks section ===== */
        .zone-card .lock-title {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          max-width: 100%;
        }

        .zone-title-wrap {
          display: flex;
          align-items: center;
          gap: 8px;
          min-width: 0;
          flex: 1;
        }

        .zone-gear-wrap { position: relative; }

        .zone-sync-warning {
          display: flex;
          align-items: center;
          gap: 8px;
          margin: 12px 0 0 0;
          padding: 8px 12px;
          border-radius: 8px;
          background: rgba(240, 160, 32, 0.12);
          border: 1px solid rgba(240, 160, 32, 0.55);
          color: #b9770e;
          font-size: 13px;
          font-weight: 500;
          line-height: 1.3;
        }
        .zone-sync-warning ha-icon {
          color: #f0a020;
        }
        /* Amber pill marking a zone (or global) alert snooze. */
        .zone-snooze-badge {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          font-size: 12px;
          font-weight: 500;
          padding: 2px 8px;
          border-radius: 10px;
          background: rgba(240, 160, 32, 0.12);
          border: 1px solid rgba(240, 160, 32, 0.55);
          color: #b9770e;
          white-space: nowrap;
        }
        /* Snooze duration popover anchored under the zone header bell. */
        .zone-snooze-menu { top: 36px; right: 0; }
        /* Global "Pause all" popover anchored under the header button. */
        .pause-all-menu { top: 38px; right: 0; }
        /* Resume button inside the global snooze banner. */
        .global-snooze-resume {
          margin-left: auto;
          background: none;
          border: 1px solid rgba(240, 160, 32, 0.55);
          color: #b9770e;
          cursor: pointer;
          padding: 3px 10px;
          border-radius: 6px;
          font-size: 12px;
          font-weight: 600;
        }
        .zone-locks-section {
          position: relative;
          margin: 12px 0 16px 0;
          padding: 10px 12px;
          background: var(--primary-background-color);
          border-radius: 8px;
          border: 1px solid var(--divider-color);
        }

        .zone-section-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 8px;
          cursor: pointer;
          user-select: none;
        }
        .zone-section-head:hover .zone-section-title {
          opacity: 1;
        }

        .zone-section-head-right {
          display: flex;
          align-items: center;
          gap: 6px;
          color: var(--secondary-text-color);
        }

        /* Both mdi chevron glyphs (up + down) sit ~3px low within their own
           viewBox, so nudge up to optically center them on the header row,
           matching the dead-centered + button next to them. */
        .zone-section-chevron {
          transform: translateY(-3px);
        }

        /* Slots section wrapper mirrors the locks section framing so its
           collapse header lines up visually. */
        .zone-slots-section {
          position: relative;
          margin: 12px 0 16px 0;
          padding: 10px 12px;
          background: var(--primary-background-color);
          border-radius: 8px;
          border: 1px solid var(--divider-color);
        }

        .zone-section-title {
          font-size: 13px;
          font-weight: 600;
          color: var(--primary-text-color);
          text-transform: uppercase;
          letter-spacing: 0.04em;
          opacity: 0.85;
        }

        .zone-add-lock-btn {
          background: none;
          border: 1px solid var(--divider-color);
          cursor: pointer;
          padding: 0;
          width: 26px;
          height: 26px;
          border-radius: 6px;
          color: var(--primary-color);
          display: inline-flex;
          align-items: center;
          justify-content: center;
          line-height: 0;
          transition: all 0.15s;
        }
        .zone-add-lock-btn ha-icon {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 18px;
          height: 18px;
          --mdc-icon-size: 18px;
          line-height: 0;
        }
        .zone-add-lock-btn:hover {
          background: var(--card-background-color);
          border-color: var(--primary-color);
        }

        /* Fixed-height list: ~2 rows visible (each row 44px), scroll beyond.
           Uniform across every zone card regardless of member count. */
        .zone-locks-list {
          height: 96px;
          overflow-y: auto;
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding-right: 2px;
        }

        .zone-lock-row {
          position: relative;
          display: flex;
          align-items: center;
          gap: 10px;
          height: 42px;
          min-height: 42px;
          padding: 0 8px;
          background: var(--card-background-color);
          border: 1px solid var(--divider-color);
          border-radius: 6px;
        }

        .zone-lock-action {
          background: none;
          border: none;
          cursor: pointer;
          padding: 2px;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
          border-radius: 4px;
          line-height: 0;
          transition: filter 0.15s;
        }
        .zone-lock-action ha-icon { --mdc-icon-size: 24px; display: flex; }
        .zone-lock-action:hover { filter: brightness(1.25); }
        .zone-lock-action.is-offline { opacity: 0.55; }
        .zone-lock-action.is-jammed { animation: jamPulse 1.4s ease-in-out infinite; }

        @keyframes jamPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.45; }
        }

        .zone-lock-name {
          flex: 1;
          min-width: 0;
          font-size: 13px;
          font-weight: 500;
          color: var(--primary-text-color);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          display: flex;
          flex-direction: column;
          justify-content: center;
          line-height: 1.2;
        }

        .zone-lock-substate {
          font-size: 10px;
          font-weight: 400;
          color: var(--secondary-text-color);
          opacity: 0.85;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .zone-lock-gear {
          background: none;
          border: none;
          cursor: pointer;
          padding: 0;
          width: 28px;
          height: 28px;
          border-radius: 4px;
          color: var(--secondary-text-color);
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
          line-height: 0;
          align-self: center;
          transition: color 0.15s;
        }
        .zone-lock-gear ha-icon { --mdc-icon-size: 18px; display: flex; }
        .zone-lock-gear:hover { color: var(--primary-color); }

        .zone-locks-empty {
          font-size: 12px;
          color: var(--secondary-text-color);
          padding: 12px 4px;
          text-align: center;
        }

        /* ===== Floating menus (zone gear, per-lock gear, + picker) ===== */
        .zone-menu {
          position: absolute;
          z-index: 30;
          background: var(--card-background-color);
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          box-shadow: 0 6px 20px rgba(0,0,0,0.28);
          padding: 6px;
          min-width: 190px;
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .zone-header-menu { top: 36px; right: 0; }
        /* Fixed so it escapes the locks-list overflow clip; coords set inline
           from the gear button's viewport rect. */
        .zone-lock-menu { position: fixed; z-index: 40; }
        .zone-picker {
          right: 12px;
          top: 38px;
          max-height: 220px;
          overflow-y: auto;
        }

        .zone-menu-item, .zone-picker-item {
          display: flex;
          align-items: center;
          gap: 8px;
          width: 100%;
          background: none;
          border: none;
          cursor: pointer;
          padding: 7px 9px;
          border-radius: 6px;
          font-size: 13px;
          color: var(--primary-text-color);
          text-align: left;
          transition: background 0.12s;
        }
        .zone-menu-item:hover, .zone-picker-item:hover {
          background: var(--primary-background-color);
        }
        .zone-menu-item.danger { color: var(--error-color, #f44336); }
        .zone-menu-item.danger:hover { background: rgba(244,67,54,0.12); }
        .zone-menu-item ha-icon, .zone-picker-item ha-icon { flex-shrink: 0; }
        .zone-picker-item span {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .zone-menu-sep {
          height: 1px;
          background: var(--divider-color);
          margin: 4px 2px;
        }

        .zone-picker-empty {
          font-size: 12px;
          color: var(--secondary-text-color);
          padding: 10px;
          text-align: center;
        }

        .lock-stats {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 8px;
          margin-bottom: 16px;
        }

        .stat-item {
          text-align: center;
          padding: 8px;
          background: var(--primary-background-color);
          border-radius: 6px;
        }

        .stat-value {
          font-size: 16px;
          font-weight: bold;
          color: var(--primary-color);
        }

        .stat-label {
          font-size: 10px;
          color: var(--secondary-text-color);
          margin-top: 2px;
        }

        .slots-container {
          max-height: 200px;
          overflow-y: auto;
          margin-bottom: 12px;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
        }

        .slot-row {
          display: flex;
          align-items: center;
          padding: 8px 12px;
          border-bottom: 1px solid var(--divider-color);
          cursor: pointer;
          transition: background-color 0.2s;
        }

        .slot-row:last-child {
          border-bottom: none;
        }

        .slot-row:hover {
          background: var(--primary-background-color);
        }

        .slot-indicator {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          margin-right: 12px;
          flex-shrink: 0;
        }

        .slot-info {
          flex-grow: 1;
          min-width: 0;
        }

        .slot-name {
          font-size: 14px;
          font-weight: 500;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .slot-details {
          font-size: 11px;
          color: var(--secondary-text-color);
          margin-top: 2px;
        }

        .slot-actions {
          display: flex;
          gap: 4px;
          opacity: 0;
          transition: opacity 0.2s;
        }

        .slot-row:hover .slot-actions {
          opacity: 1;
        }

        .slot-action-btn {
          width: 20px;
          height: 20px;
          border-radius: 3px;
          border: none;
          font-size: 11px;
          cursor: pointer;
          color: white;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .clear-btn { background: #f44336; }
        .reset-btn { background: #ff9800; }

        .lock-actions {
          display: flex;
          gap: 8px;
        }

        /* Access Log section */
        .access-log-section {
          margin-top: 12px;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          overflow: hidden;
        }

        .access-log-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 8px 12px;
          cursor: pointer;
          background: var(--secondary-background-color);
          font-size: 13px;
          font-weight: 500;
          user-select: none;
        }

        .access-log-header:hover {
          background: var(--primary-background-color);
        }

        .access-log-body {
          max-height: 220px;
          overflow-y: auto;
        }

        .access-log-empty {
          padding: 12px;
          font-size: 12px;
          color: var(--secondary-text-color);
          text-align: center;
        }

        /* Uniform fixed-height, single-line rows. Long lock/user names
           truncate with ellipsis; full value lives in the row's title. */
        .access-log-row {
          display: flex;
          align-items: center;
          gap: 8px;
          height: 30px;
          min-height: 30px;
          padding: 0 12px;
          border-top: 1px solid var(--divider-color);
          font-size: 12px;
          overflow: hidden;
        }

        .access-log-row .al-icon {
          flex-shrink: 0;
        }

        .access-log-row .al-lock-badge {
          flex-shrink: 1;
          min-width: 0;
          max-width: 38%;
          padding: 1px 7px;
          border-radius: 10px;
          font-size: 10px;
          font-weight: 600;
          text-transform: none;
          background: var(--primary-color);
          color: var(--text-primary-color);
          opacity: 0.85;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .access-log-row .al-attr-text {
          flex: 1;
          min-width: 0;
          color: var(--secondary-text-color);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .access-log-row .al-time {
          color: var(--secondary-text-color);
          font-size: 11px;
          white-space: nowrap;
          flex-shrink: 0;
        }

        /* Alerts section. */
        .dev-alerts-banner {
          padding: 5px 12px;
          font-size: 11px;
          font-style: italic;
          color: var(--secondary-text-color);
          background: var(--secondary-background-color);
          border-top: 1px solid var(--divider-color);
        }

        .dev-alert-row .al-attr-text {
          color: var(--primary-text-color);
        }

        .dev-alert-row.dev-alert-recovery {
          opacity: 0.7;
        }

        .dev-alert-row.dev-alert-recovery .al-attr-text {
          font-style: italic;
          color: var(--secondary-text-color);
        }

        /* DRY-RUN "would-notify" sub-line under a dev alert row. */
        .dev-alert-intents {
          padding: 1px 12px 4px 36px;
          font-size: 10.5px;
          color: var(--secondary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .btn {
          padding: 6px 12px;
          background: var(--primary-color);
          color: var(--text-primary-color);
          border: none;
          border-radius: 4px;
          cursor: pointer;
          font-size: 12px;
          transition: background-color 0.2s;
        }

        .btn:hover {
          background: var(--primary-color-dark);
        }

        .btn.secondary {
          background: var(--secondary-color);
        }


        .modal {
          position: fixed;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          background: rgba(0,0,0,0.5);
          display: ${self._modalOpen ? 'flex' : 'none'};
          align-items: center;
          justify-content: center;
          z-index: 1000;
        }

        .modal-content {
          background: var(--card-background-color);
          border-radius: 8px;
          padding: 24px;
          width: 90%;
          max-width: 500px;
          max-height: 80vh;
          overflow-y: auto;
          overflow-x: hidden;
          box-sizing: border-box;
        }

        .form-container {
          width: 100%;
          display: flex;
          flex-direction: column;
          align-items: center;
          padding: 0 20px; /* Center the form content with side margins */
          box-sizing: border-box;
        }

        #settings-form {
          width: 100%;
          max-width: none;
        }

        /* CLEAN CONTROL LAYOUT - Container-constrained width */
        .form-group {
          margin-bottom: 16px;
          width: 100%;
          box-sizing: border-box;
        }

        .form-group label {
          display: block;
          margin-bottom: 6px;
          font-weight: 500;
          color: var(--primary-text-color);
        }

        .form-group input,
        .form-group select {
          width: 100%;
          padding: 8px 12px;
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          background: var(--card-background-color);
          color: var(--primary-text-color);
          font-size: 14px;
          box-sizing: border-box;
        }

        /* Slider specific styling - full width within container */
        .form-group input[type="range"] {
          width: 100%;
          padding: 0;
          margin: 10px 0;
          height: 6px;
          border-radius: 3px;
          background: var(--disabled-text-color);
          outline: none;
          opacity: 0.7;
          transition: opacity 0.2s;
        }

        .slider-labels {
          display: flex;
          justify-content: space-between;
          font-size: 12px;
          color: var(--secondary-text-color);
          width: 100%;
        }

        .form-group small {
          font-size: 11px;
          color: var(--secondary-text-color);
          margin-top: 4px;
          display: block;
          width: 100%;
        }

        /* Enhanced toggle styling */
        .toggle-label {
          display: flex;
          align-items: center;
          gap: 8px;
          font-weight: 500;
          color: var(--primary-text-color);
        }

        .toggle-text {
          color: var(--primary-text-color);
        }

        /* Toggle button styling */
        .toggle-btn {
          font-size: 14px;
          min-width: 24px;
          border-radius: 4px;
          transition: all 0.2s ease;
        }

        .toggle-btn.active {
          background: var(--success-color, #4caf50);
          color: white;
        }

        .toggle-btn.inactive {
          background: var(--warning-color, #ff9800);
          color: white;
        }

        .toggle-btn:hover {
          /* Removed hover effects */
        }

        /* Removed obsolete container CSS - using clean form-group CSS now */

        .slider:hover {
          opacity: 1;
        }

        .slider::-webkit-slider-thumb {
          appearance: none;
          width: 20px;
          height: 20px;
          border-radius: 50%;
          background: var(--primary-color);
          cursor: pointer;
        }

        .slider::-moz-range-thumb {
          width: 20px;
          height: 20px;
          border-radius: 50%;
          background: var(--primary-color);
          cursor: pointer;
          border: none;
        }

        .slider-labels {
          display: flex;
          justify-content: space-between;
          font-size: 12px;
          color: var(--secondary-text-color);
          margin-top: -5px;
        }

        .modal-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 20px;
          padding-bottom: 12px;
          border-bottom: 1px solid var(--divider-color);
        }

        .modal-title {
          font-size: 18px;
          font-weight: 500;
          margin: 0;
        }

        .close-btn {
          background: none;
          border: none;
          font-size: 24px;
          cursor: pointer;
          color: var(--secondary-text-color);
          padding: 4px;
        }

        .form-group {
          margin-bottom: 16px;
          display: flex;
          flex-direction: column;
          align-items: flex-start;
        }

        .form-group label {
          display: block;
          margin-bottom: 6px;
          font-weight: 500;
          color: var(--primary-text-color);
        }

        .form-group input,
        .form-group select {
          width: 100%;
          padding: 8px 12px;
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          background: var(--card-background-color);
          color: var(--primary-text-color);
          font-size: 14px;
          box-sizing: border-box;
        }

        /* PIN validation styles */
        .form-group input.pin-error {
          border-color: #f44336;
          background: rgba(244, 67, 54, 0.1);
        }

        .form-group input.pin-valid {
          border-color: #4caf50;
          background: rgba(76, 175, 80, 0.1);
        }

        .pin-validation-message {
          font-size: 12px;
          margin-top: 4px;
          display: block;
          transition: opacity 0.2s;
        }

        .pin-validation-message.error {
          color: #f44336;
        }

        .pin-validation-message.success {
          color: #4caf50;
        }

        /* Date validation styles */
        .form-group input.date-error {
          border-color: #f44336;
          background: rgba(244, 67, 54, 0.1);
        }
        .form-group input.date-valid {
          border-color: #4caf50;
          background: rgba(76, 175, 80, 0.1);
        }
        .form-group input.date-warning {
          border-color: #ff9800;
          background: rgba(255, 152, 0, 0.1);
        }

        /* Date range input styling */
        .form-group input[type="datetime-local"] {
          font-family: inherit;
          cursor: pointer;
          min-width: 200px;
        }

        .form-group input[type="datetime-local"]:focus {
          outline: 2px solid var(--primary-color);
          outline-offset: 1px;
        }

        /* Date range section styling */
        .date-range-section {
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          padding: 16px;
          margin: 12px 0;
          background: var(--card-background-color);
        }

        .date-range-header {
          font-weight: 500;
          margin-bottom: 8px;
          color: var(--primary-text-color);
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .date-range-header::before {
          content: "📅";
          font-size: 16px;
        }

        /* Removed - using container-based width control now */

        /* Removed obsolete container CSS - no longer using containers */

        .form-group select[multiple] {
          min-height: 120px;
          padding: 4px;
        }

        .form-group select[multiple] option {
          padding: 4px 8px;
          margin: 2px 0;
          border-radius: 3px;
        }

        .form-group select[multiple] option:checked {
          background: var(--primary-color);
          color: var(--text-primary-color);
        }

        .form-group input[type="checkbox"] {
          width: auto;
          margin-right: 8px;
        }

        /* Removed - using consistent container-based width now */

        /* Removed duplicate - using container-based width control now */

        .form-actions {
          display: flex;
          justify-content: flex-end;
          gap: 12px;
          margin-top: 20px;
          padding-top: 12px;
          border-top: 1px solid var(--divider-color);
          width: 100%;
        }

        .no-locks {
          text-align: center;
          padding: 40px;
          color: var(--secondary-text-color);
        }

          background: rgba(244, 67, 54, 0.1);
          color: #F44336;
        }

        /* --- Zone Settings modal (Phase 4a) --- */
        .zone-settings-modal { max-width: 560px; }

        .zs-form {
          align-items: stretch;
          padding: 0 8px;
        }

        .zs-section {
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          padding: 12px 14px;
          margin-bottom: 16px;
        }

        .zs-section-title {
          margin: 0 0 10px;
          font-size: 13px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          color: var(--primary-text-color);
          border-bottom: 1px solid var(--divider-color);
          padding-bottom: 6px;
        }

        .zs-toggle {
          display: flex;
          align-items: center;
          gap: 8px;
          font-weight: 500;
          color: var(--primary-text-color);
          margin: 8px 0;
          cursor: pointer;
        }
        .zs-toggle input[type="checkbox"] { width: 16px; height: 16px; cursor: pointer; }

        .zs-row {
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
        }
        .zs-row .form-group { flex: 1 1 120px; margin-bottom: 12px; }

        .zs-days {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }
        .zs-day {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          font-size: 12px;
          font-weight: 500;
          color: var(--primary-text-color);
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          padding: 4px 8px;
          cursor: pointer;
        }
        .zs-day input[type="checkbox"] { width: 14px; height: 14px; cursor: pointer; }

        .zs-alert-block {
          padding: 8px 0;
          border-bottom: 1px dashed var(--divider-color);
        }
        .zs-alert-block:last-child { border-bottom: none; }
        .zs-alert-block .form-group { margin-bottom: 4px; }
      `;
}
