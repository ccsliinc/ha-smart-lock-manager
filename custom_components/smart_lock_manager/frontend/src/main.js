import { panelStyles } from './styles.js';
import { ZS_DAYS, CUSTOM_CARD_META } from './constants.js';
import { escHtml, fmtTime, formatAccessLogTime as formatAccessLogTime_, untilMorningHours } from './utils/format.js';
import {
  slotDisplayInfo,
  accessLogPresentation,
  devAlertPresentation,
  zoneSlotDisplay
} from './utils/presentation.js';

// Smart Lock Manager Advanced Panel v2.1.0
// Enhanced panel with slot management grid, advanced code management, usage
// analytics, and a per-lock Access Log (lock/unlock/jam events with attribution)

// Prevent redefinition if already loaded
if (!window.SmartLockManagerPanel) {

class SmartLockManagerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._narrow = false;
    this._modalOpen = false;
    this._editingSlot = null;
    this._currentLockEntityId = null;
    this._settingsModalOpen = false;
    this._zwaveCodeCache = {}; // Cache for Z-Wave codes by entity_id
    this._zones = [];           // Zone model from /api/smart_lock_manager/zones
    this._unhomedLocks = [];    // Locks in no zone (the "+" picker pool)
    this._observeOnly = false;  // True when an engine is constructed (gates Alerts UI)
    this._alerts = [];          // Recorded alert log
    this._zoneDataLoaded = false;
    this._openZoneMenu = null;  // zone_id whose header gear menu is open
    this._openLockMenu = null;  // entity_id whose per-lock gear menu is open
    this._openPicker = null;    // zone_id whose "+" unhomed picker is open
    this._snooze = null;        // { global_until, zones: {} } alert-pause state from zones API
    this._openSnoozeMenu = null; // zone_id whose per-zone snooze duration popover is open
    this._openPauseAllMenu = false; // true when the global "Pause all" duration popover is open
    this._editLockModalOpen = false; // per-lock Edit (friendly name) modal
    this._editLockEntityId = null;   // entity_id being edited in that modal
    this._zoneSettingsModalOpen = false; // Zone Settings modal (Phase 4a)
    this._zoneSettingsZoneId = null;     // zone_id whose settings are being edited
    // Per-zone, per-section collapse state. Keyed "<zoneId>:<section>" -> bool
    // (true = expanded). Absent key means "use computed default" (see
    // _isSectionExpanded). Survives re-render so a data refresh doesn't reset
    // what the user opened/closed.
    this._sectionState = {};
    this.setupEventListeners();
  }

  set hass(hass) {
    const oldHass = this._hass;
    this._hass = hass;

    // Store reference for global access
    window.smartLockManagerPanel = this;

    // Setup event listeners when hass becomes available
    if (hass && (!oldHass || oldHass.connection !== hass.connection)) {
      this.setupEventListeners();
    }

    // Force reload lock data if states changed
    if (oldHass && hass && oldHass.states !== hass.states) {
      this.loadLockData();
      this.loadZoneData();
    }

    // First time hass becomes available, prime the zone model.
    if (!oldHass && hass) {
      this.loadZoneData();
    }

    // Don't auto-refresh if modal is open to prevent losing user input
    if (!this._modalOpen && !this._settingsModalOpen && !this._editLockModalOpen && !this._zoneSettingsModalOpen) {
      this.requestUpdate();
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    this.requestUpdate();
  }

  connectedCallback() {
    // Store global reference for static methods
    window.smartLockManagerPanel = this;
    this.render();
    this.loadLockData();
    this.loadZoneData();
  }

  disconnectedCallback() {
    // Clean up global reference
    if (window.smartLockManagerPanel === this) {
      window.smartLockManagerPanel = null;
    }
  }

  async loadLockData(bypassCache = false) {
    if (!this._hass) return;

    try {
      // Find Smart Lock Manager entities by looking for sensors with our unique_id pattern
      const entities = Object.keys(this._hass.states).filter(entity_id => {
        if (!entity_id.startsWith('sensor.')) return false;
        const state = this._hass.states[entity_id];
        // Check if this sensor has our component's unique_id pattern or integration
        return state?.attributes?.unique_id?.startsWith('smart_lock_manager_') ||
               (state?.attributes?.icon === 'mdi:lock-smart' &&
                state?.attributes?.lock_entity_id);
      });

      this._locks = [];

      for (const entity_id of entities) {
        let currentState;
        let attributes;

        if (bypassCache) {
          // Fetch fresh state directly from API
          try {
            const freshState = await this._hass.callApi('GET', `states/${entity_id}`);
            attributes = freshState?.attributes || {};
            currentState = freshState;

            // Update cached state for consistency
            if (this._hass.states[entity_id]) {
              this._hass.states[entity_id] = freshState;
            }
          } catch (apiError) {
            currentState = this._hass.states[entity_id];
            attributes = currentState?.attributes || {};
          }
        } else {
          // Use cached state
          currentState = this._hass.states[entity_id];
          attributes = currentState?.attributes || {};
        }

        this._locks.push({
          entity_id,
          state: currentState,
          attributes: attributes
        });
      }

      // Select first lock if none selected
      if (!this._selectedLock && this._locks.length > 0) {
        this._selectedLock = this._locks[0];
      }

      // Don't auto-refresh if modal is open to prevent losing user input
      if (!this._modalOpen && !this._settingsModalOpen && !this._editLockModalOpen && !this._zoneSettingsModalOpen) {
        this.requestUpdate();
      }
    } catch (error) {
    }
  }

  // Fetch the full zone model + unhomed lock pool from the authenticated
  // Phase 3a data endpoint. ``callApi`` injects the HA auth token and prefixes
  // ``/api/`` automatically, so this works for any logged-in panel user.
  // - Inputs: none (reads this._hass).
  // - Outputs: Promise<void>; populates this._zones / this._unhomedLocks then
  //   re-renders. Failures leave the prior data intact and log to console.
  async loadZoneData() {
    if (!this._hass) return;
    try {
      const payload = await this._hass.callApi('GET', 'smart_lock_manager/zones');
      this._zones = Array.isArray(payload?.zones) ? payload.zones : [];
      this._unhomedLocks = Array.isArray(payload?.unhomed_locks) ? payload.unhomed_locks : [];
      // Alert-pause (snooze) state: { global_until: <iso|null>, zones: { <id>: <iso> } }.
      // Per-zone snoozed_until is also carried on each zone object.
      this._snooze = payload?.snooze || null;
      // Recorded alert log. ``observe_only`` is true whenever an engine
      // is constructed (dev OR observe); the Alerts section renders then.
      this._observeOnly = !!payload?.observe_only;
      this._alerts = Array.isArray(payload?.alerts) ? payload.alerts : [];
      this._zoneDataLoaded = true;
      if (!this._modalOpen && !this._settingsModalOpen && !this._editLockModalOpen && !this._zoneSettingsModalOpen) {
        this.requestUpdate();
      }
    } catch (error) {
      console.error('[SLM] Failed to load zone data:', error);
    }
  }

  requestUpdate() {
    this.render();
  }

  setupEventListeners() {
    // Listen for Z-Wave code reading events
    if (this._hass?.connection) {
      try {
        this._hass.connection.subscribeEvents((event) => {
          if (event.event_type === 'smart_lock_manager_codes_read') {
            this.handleZWaveCodesRead(event.data);
          }
        }, 'smart_lock_manager_codes_read');
      } catch (error) {
      }
    }
  }

  handleZWaveCodesRead(eventData) {
    const { entity_id, codes } = eventData;

    // SECURITY: this event now carries occupancy only (per slot: {slot,
    // in_use, status}) and never the raw PIN value. Presence of a code is
    // derivable from the occupancy map; the code value is intentionally
    // unavailable to the frontend.
    this._zwaveCodeCache[entity_id] = codes;

    // Trigger a re-render to reflect Z-Wave occupancy.
    this.requestUpdate();
  }

  // Automatically refresh Z-Wave codes for sync validation
  refreshZWaveCodes(lockEntityId) {
    if (this._hass && lockEntityId) {
      // Refreshing Z-Wave codes
      this.callService('read_zwave_codes', { entity_id: lockEntityId });
    }
  }

  // Removed getSlotStatusColor and getSlotStatusText methods
  // All logic now handled by backend - frontend just displays

  getSlotDisplayInfo(lock, slot) { return slotDisplayInfo(lock, slot); }

  // Build the icon + meta for a single access-log action/source.
  getAccessLogPresentation(entry) { return accessLogPresentation(entry); }

  // Format an ISO timestamp into a local, human-friendly string.
  formatAccessLogTime(iso) { return formatAccessLogTime_(iso); }

  openSlotModal(slotNumber) {
    this._editingSlot = slotNumber;
    this._modalOpen = true;
    this.requestUpdate();

    // Populate form fields after the modal is rendered
    setTimeout(() => {
      this.populateSlotForm(slotNumber);
    }, 50);
  }

  populateSlotForm(slotNumber) {
    // Find the current lock
    const currentLock = this._locks?.find(l => l.attributes.lock_entity_id === this._currentLockEntityId);
    if (!currentLock) {
      return;
    }

    // Get slot details
    const slotDetails = currentLock.attributes?.slot_details?.[`slot_${slotNumber}`];

    // Get form elements
    const form = this.shadowRoot.querySelector('#slot-form');
    if (!form) return;

    // Populate fields with existing data or defaults
    const userNameField = form.querySelector('#user_name');
    const pinCodeField = form.querySelector('#pin_code');
    const maxUsesField = form.querySelector('#max_uses');
    const allowedHoursField = form.querySelector('#allowed_hours');
    const allowedDaysField = form.querySelector('#allowed_days');
    const isActiveField = form.querySelector('input[name="is_active"]');
    const notifyField = form.querySelector('input[name="notify_on_use"]');

    if (slotDetails) {
      if (userNameField) userNameField.value = slotDetails.user_name || '';
      // SECURITY: the raw PIN is never sent to the browser anymore (the
      // backend only exposes has_pin_code). Leave the field blank and, when a
      // code already exists, tell the user that leaving it blank keeps the
      // current PIN. Typing a value replaces it.
      if (pinCodeField) {
        pinCodeField.value = '';
        pinCodeField.placeholder = slotDetails.has_pin_code
          ? 'PIN set — leave blank to keep it, or type a new one'
          : 'Enter PIN code (4-8 digits)';
      }
      if (maxUsesField) maxUsesField.value = slotDetails.max_uses || -1;
      if (isActiveField) isActiveField.checked = true; // Default to enabled
      if (notifyField) notifyField.checked = slotDetails.notify_on_use || false;

      // Handle multiselect for allowed hours
      if (allowedHoursField && slotDetails.allowed_hours) {
        // Clear all selections first
        Array.from(allowedHoursField.options).forEach(option => option.selected = false);
        // Select the hours that are allowed
        slotDetails.allowed_hours.forEach(hour => {
          const option = allowedHoursField.querySelector(`option[value="${hour}"]`);
          if (option) option.selected = true;
        });
      }

      // Handle multiselect for allowed days
      if (allowedDaysField && slotDetails.allowed_days) {
        // Clear all selections first
        Array.from(allowedDaysField.options).forEach(option => option.selected = false);
        // Select the days that are allowed
        slotDetails.allowed_days.forEach(day => {
          const option = allowedDaysField.querySelector(`option[value="${day}"]`);
          if (option) option.selected = true;
        });
      }

      // Handle date range fields
      const accessFromField = form.querySelector('#access_from');
      const accessToField = form.querySelector('#access_to');
      if (accessFromField && slotDetails.start_date) {
        // Convert backend datetime to local datetime-local format
        const startDate = new Date(slotDetails.start_date);
        if (!isNaN(startDate.getTime())) {
          accessFromField.value = startDate.toISOString().slice(0, 16);
        }
      }
      if (accessToField && slotDetails.end_date) {
        // Convert backend datetime to local datetime-local format
        const endDate = new Date(slotDetails.end_date);
        if (!isNaN(endDate.getTime())) {
          accessToField.value = endDate.toISOString().slice(0, 16);
        }
      }
    } else {
      // Clear form for new slot
      if (userNameField) userNameField.value = '';
      if (pinCodeField) pinCodeField.value = '';
      if (maxUsesField) maxUsesField.value = -1;
      if (isActiveField) isActiveField.checked = true; // Default new slots to enabled
      if (notifyField) notifyField.checked = false;

      // Clear multiselect fields
      if (allowedHoursField) {
        Array.from(allowedHoursField.options).forEach(option => option.selected = false);
      }
      if (allowedDaysField) {
        Array.from(allowedDaysField.options).forEach(option => option.selected = false);
      }
    }
  }

  updateSlotDisplayImmediate(slotNumber, userName, isActive) {
    // Immediately update the slot display for better UX feedback
    try {
      const slotElements = this.shadowRoot.querySelectorAll('.slot-card');
      slotElements.forEach(element => {
        const slotText = element.querySelector('.slot-name');
        if (slotText && slotText.textContent.includes(`Slot ${slotNumber}:`)) {
          slotText.textContent = `Slot ${slotNumber}: ${userName}`;

          // Update visual state
          element.classList.remove('active', 'inactive', 'scheduled');
          if (isActive) {
            element.classList.add('active');
          } else {
            element.classList.add('inactive');
          }

          // Update status text
          const statusElement = element.querySelector('.slot-details');
          if (statusElement) {
            const parts = statusElement.textContent.split(' • ');
            parts[parts.length - 1] = isActive ? 'Active (Syncing...)' : 'Disabled';
            statusElement.textContent = parts.join(' • ');
          }
        }
      });
    } catch (error) {
    }
  }

  closeModal() {
    this._modalOpen = false;
    this._editingSlot = null;
    // Refresh data when modal closes to show any saved changes
    this.loadLockData();
    this.requestUpdate();
  }

  openSettingsModal(lockEntityId) {
    const currentLock = this._locks?.find(l => l.attributes.lock_entity_id === lockEntityId);

    this._currentLockEntityId = lockEntityId;
    this._settingsModalOpen = true;

    this.requestUpdate();
  }

  closeSettingsModal() {
    this._settingsModalOpen = false;
    this._currentLockEntityId = null;

    // Force immediate DOM update to hide modal
    this.requestUpdate();

    // Check if modal is actually hidden in DOM
    setTimeout(() => {
      const modal = this.shadowRoot.querySelector('.modal[style*="flex"]');
      // Modal visibility check
    }, 10);
  }

  async saveSettings() {

    // Show loading spinner immediately
    this.showSaveSpinner();

    const form = this.shadowRoot.querySelector('#settings-form');
    if (!form) {
      this.hideSaveSpinner();
      return;
    }

    const formData = new FormData(form);

    const currentLock = this._locks?.find(l => l.attributes.lock_entity_id === this._currentLockEntityId);
    const currentSlotCount = currentLock?.attributes?.total_slots || 10;
    const newSlotCount = parseInt(formData.get('slot_count')) || 10;

    const friendlyName = formData.get('friendly_name')?.trim();
    // Get current friendly name from attributes, fallback to lock_name
    const currentFriendlyName = currentLock?.attributes?.friendly_name || currentLock?.attributes?.lock_name || '';

    // Build update data
    const updateData = {
      entity_id: this._currentLockEntityId
    };

    let hasUpdates = false;

    // Check if friendly name changed
    if (friendlyName && friendlyName !== currentFriendlyName) {
      updateData.friendly_name = friendlyName;
      hasUpdates = true;
    }

    // Check if slot count changed
    if (newSlotCount !== currentSlotCount) {
      updateData.slot_count = newSlotCount;
      hasUpdates = true;
    }

    // Call the update service if there are changes
    if (hasUpdates) {
      // Save the entity ID before closing modal (closeSettingsModal clears it).
      // Declared OUTSIDE the try so the catch block can still hide the card
      // spinner when the service rejects; a const inside the try was out of
      // scope in catch and threw a ReferenceError, leaving the spinner stuck.
      const entityIdForSpinner = this._currentLockEntityId;
      try {

        // Close modal FIRST before the service call
        this.hideSaveSpinner();
        this.closeSettingsModal();

        // Show card spinner for the background operation
        this.showCardSpinner(entityIdForSpinner, 'Updating...');

        // Now make the service call in background
        const result = await this.callService('update_lock_settings', updateData);

        // Background refresh after modal is already closed
        setTimeout(async () => {
          await this.loadLockData(true);
          // loadLockData() already calls requestUpdate(), so no need to call it again
          // Hide card spinner after refresh completes
          this.hideCardSpinner(entityIdForSpinner);
        }, 500);

      } catch (error) {
        // Modal is already closed, hide card spinner and log the error
        this.hideCardSpinner(entityIdForSpinner);
      }
    } else {
      // Hide spinner and close modal immediately if no updates
      this.hideSaveSpinner();
      this.closeSettingsModal();
    }
  }

  async callService(service, serviceData, domain = 'smart_lock_manager') {
    try {
      const result = await this._hass.callService(domain, service, serviceData);

      // Immediately force refresh of Home Assistant entities
      if (this._hass.callService) {
        // Find the actual sensor entity ID for this lock
        const lockEntityId = serviceData.entity_id;
        const sensorEntities = Object.keys(this._hass.states).filter(id =>
          id.startsWith('sensor.') &&
          this._hass.states[id]?.attributes?.lock_entity_id === lockEntityId
        );


        // Force refresh all related sensors
        for (const sensorId of sensorEntities) {
          try {
            await this._hass.callService('homeassistant', 'update_entity', {
              entity_id: sensorId
            });

            // Wait a moment for the backend to update
            await new Promise(resolve => setTimeout(resolve, 100));

          } catch (err) {
          }
        }
      }

      // Force state refresh by directly querying the API

      // Re-find sensor entities (fix scope issue)
      const lockEntityId = serviceData.entity_id;
      const refreshSensorEntities = Object.keys(this._hass.states).filter(id =>
        id.startsWith('sensor.') &&
        this._hass.states[id]?.attributes?.lock_entity_id === lockEntityId
      );

      // Force refresh the state directly from the API
      try {
        if (refreshSensorEntities.length > 0) {
          for (const sensorId of refreshSensorEntities) {
            const response = await this._hass.callApi('GET', `states/${sensorId}`);

            // Update the cached state directly
            if (this._hass.states[sensorId]) {
              this._hass.states[sensorId] = response;
            }
          }
        }
      } catch (apiError) {
      }

      // Immediately reload data with fresh API calls
      await this.loadLockData(true); // bypassCache = true
      this.requestUpdate();

      // Note: Additional refreshes removed to prevent race conditions
      // Background refresh in saveSettings() handles the cache bypass

    } catch (error) {
      alert(`Service Error: ${error.message}\n\nCheck console for details.`);
    }
  }

  // Call a service (default domain smart_lock_manager) then refresh the zone
  // model. Used by every zone-level action (create/delete/add/remove/apply,
  // clear codes) and the per-member lock toggle so the cards re-render with
  // fresh state. Closes any open gear menu / picker first.
  // - Inputs: service (str), serviceData (obj), domain (str, default SLM).
  // - Outputs: Promise<void>.
  async callZoneService(service, serviceData, domain = 'smart_lock_manager') {
    this._openZoneMenu = null;
    this._openLockMenu = null;
    this._openPicker = null;
    try {
      await this._hass.callService(domain, service, serviceData);
    } catch (error) {
      alert(`Service Error: ${error.message}\n\nCheck console for details.`);
    }
    // Give the backend a beat to persist, then reload zone + sensor data.
    await new Promise(r => setTimeout(r, 250));
    await this.loadZoneData();
    await this.loadLockData(true);
    this.requestUpdate();
  }

  async saveSlotSettings() {
    const form = this.shadowRoot.querySelector('#slot-form');
    const formData = new FormData(form);

    // Get form values
    const isActive = formData.get('is_active') === 'on';
    const pinCode = formData.get('pin_code');
    const userName = formData.get('user_name');

    // Store slot number and entity ID BEFORE closing modal (which clears _editingSlot)
    const slotNumber = parseInt(this._editingSlot);
    const entityIdForSpinner = this._currentLockEntityId;

    // Ensure slotNumber is a valid integer
    if (isNaN(slotNumber) || slotNumber < 1) {
      alert('Error: Invalid slot number. Please try again.');
      return;
    }

    // SECURITY: the raw PIN is no longer sent to the browser, so this form
    // cannot pre-fill an existing code. `hadPin` (from the has_pin_code
    // boolean) tells us whether a code already exists so a blank field is
    // treated as "keep the existing code", never as "clear the slot".
    const priorSlot = this._locks
      ?.find(l => l.attributes.lock_entity_id === entityIdForSpinner)
      ?.attributes?.slot_details?.[`slot_${slotNumber}`];
    const hadPin = !!priorSlot?.has_pin_code;

    // Guard against accidental code loss: if the slot already has a code and
    // the user left the PIN blank while keeping the slot enabled, we cannot
    // re-write the code (we do not have its value) and must not wipe it.
    // Require the PIN to be re-entered to modify an active slot's settings.
    if (isActive && !pinCode && hadPin) {
      alert('This slot already has a PIN. Re-enter the PIN to save changes, '
        + 'or uncheck "Enable" to disable the slot without deleting its code.');
      return;
    }

    // IMMEDIATE VISUAL FEEDBACK - Update slot name immediately
    if (userName) {
      this.updateSlotDisplayImmediate(this._editingSlot, userName, isActive);
    }

    // Close modal immediately and show card spinner
    this.closeModal();
    this.showCardSpinner(entityIdForSpinner, 'Saving...');

    try {
      if (!isActive && (pinCode || hadPin)) {
        // Enable box unchecked but a code exists (either entered now or already
        // stored) - disable the slot WITHOUT deleting the stored code.
        await this.callService('disable_slot', {
          entity_id: entityIdForSpinner,
          code_slot: slotNumber
        });
        return;
      }

      if (!pinCode) {
        // No PIN entered and none stored (hadPin is false here - the active +
        // hadPin case was rejected above) - clear the slot completely.
        await this.callService('clear_code', {
          entity_id: entityIdForSpinner,
          code_slot: slotNumber
        });
        return;
      }

    // Ensure all integer fields are properly converted
    const maxUsesRaw = formData.get('max_uses');
    const maxUses = maxUsesRaw ? parseInt(maxUsesRaw) : -1;

    const serviceData = {
      entity_id: entityIdForSpinner,
      code_slot: slotNumber,
      usercode: pinCode,
      code_slot_name: formData.get('user_name'),
      max_uses: isNaN(maxUses) ? -1 : maxUses,
      notify_on_use: formData.get('notify_on_use') === 'on'
    };


    // Get selected hours from multiselect
    const allowedHoursSelect = form.querySelector('#allowed_hours');
    if (allowedHoursSelect) {
      const selectedHours = Array.from(allowedHoursSelect.selectedOptions).map(option => parseInt(option.value));
      if (selectedHours.length > 0) {
        serviceData.allowed_hours = selectedHours;
      }
    }

    // Get selected days from multiselect
    const allowedDaysSelect = form.querySelector('#allowed_days');
    if (allowedDaysSelect) {
      const selectedDays = Array.from(allowedDaysSelect.selectedOptions).map(option => parseInt(option.value));
      if (selectedDays.length > 0) {
        serviceData.allowed_days = selectedDays;
      }
    }

    // Get date range values
    const accessFromField = form.querySelector('#access_from');
    const accessToField = form.querySelector('#access_to');
    if (accessFromField && accessFromField.value) {
      serviceData.start_date = accessFromField.value;
    }
    if (accessToField && accessToField.value) {
      serviceData.end_date = accessToField.value;
    }

    // SECURITY: the raw PIN is never sent to the browser, so we can no longer
    // compare the entered PIN against a stored value to detect a no-op edit.
    // Reaching this point means a PIN was actually typed into the form (the
    // blank-field cases are handled and returned above), so we always push it
    // to the physical lock. A metadata-only edit keeps the existing code by
    // leaving the PIN blank, which is short-circuited earlier and never lands
    // here.
    await this.callService('set_code_advanced', serviceData);

    // Sync the freshly entered code to the physical Z-Wave lock.
    if (serviceData.usercode && serviceData.usercode.length >= 4) {
      await this.callService('sync_slot_to_zwave', {
        entity_id: serviceData.entity_id,
        code_slot: serviceData.code_slot, // Already converted to int above
        action: 'enable'
      });

      // Read back codes to update status
      setTimeout(() => {
        this.callService('read_zwave_codes', {
          entity_id: serviceData.entity_id
        });
      }, 1000);
    }

    } finally {
      // Hide card spinner when all operations complete
      this.hideCardSpinner(this._currentLockEntityId);

      // Force immediate frontend refresh
      setTimeout(() => {
        this.loadLockData();
      }, 100);
    }
  }

  async clearSlot(slotNumber) {
    if (confirm(`Clear slot ${slotNumber} completely? This will remove the user and code from both Smart Lock Manager and the physical lock.`)) {
      // Show card spinner during clear operation
      this.showCardSpinner(this._currentLockEntityId, 'Clearing...');

      try {
        await this.callService('clear_code', {
          entity_id: this._currentLockEntityId,
          code_slot: parseInt(slotNumber)
        });
      } finally {
        // Hide card spinner when operation completes
        this.hideCardSpinner(this._currentLockEntityId);
      }
    }
  }

  async resetSlotUsage(slotNumber) {
    const confirmed = await this.showCustomConfirm(`Reset usage counter for slot ${slotNumber}?`);
    if (confirmed) {
      // Show slot-specific loading overlay
      this.showSlotSpinner(slotNumber);

      try {
        await this.callService('reset_slot_usage', {
          entity_id: this._currentLockEntityId,
          code_slot: parseInt(slotNumber)
        });
      } finally {
        // Hide slot spinner when operation completes
        this.hideSlotSpinner(slotNumber);

        // Force a refresh to update the UI
        setTimeout(() => {
          this.loadLockData();
        }, 250);
      }
    }
  }

  validatePinCodeInput(input) {
    const pinValue = input.value;
    const messageElement = document.getElementById('pin-validation-message');

    if (!messageElement) return;

    // Clear previous validation styles
    input.classList.remove('pin-error', 'pin-valid');

    if (!pinValue) {
      // Empty field - hide message
      messageElement.style.opacity = '0';
      messageElement.textContent = '';
      return;
    }

    // Check if PIN contains only digits
    if (!/^\d*$/.test(pinValue)) {
      input.classList.add('pin-error');
      messageElement.textContent = 'PIN must contain only digits';
      messageElement.className = 'pin-validation-message error';
      messageElement.style.opacity = '1';
      return;
    }

    // Check PIN length
    if (pinValue.length < 4) {
      input.classList.add('pin-error');
      messageElement.textContent = 'PIN must be at least 4 digits';
      messageElement.className = 'pin-validation-message error';
      messageElement.style.opacity = '1';
    } else if (pinValue.length > 8) {
      input.classList.add('pin-error');
      messageElement.textContent = 'PIN must be 8 digits or less';
      messageElement.className = 'pin-validation-message error';
      messageElement.style.opacity = '1';
    } else {
      // Valid PIN
      input.classList.add('pin-valid');
      messageElement.textContent = 'Valid PIN code';
      messageElement.className = 'pin-validation-message success';
      messageElement.style.opacity = '1';
    }
  }

  validateDateRange() {
    const startInput = document.getElementById('access_from');
    const endInput = document.getElementById('access_to');

    if (!startInput || !endInput) return;

    const startValue = startInput.value;
    const endValue = endInput.value;

    // Clear previous validation styles
    startInput.classList.remove('date-error', 'date-valid', 'date-warning');
    endInput.classList.remove('date-error', 'date-valid', 'date-warning');

    // If both are empty, that's valid (unlimited access)
    if (!startValue && !endValue) {
      this.hideDateRangeError();
      return;
    }

    // If only one is filled, that's also valid
    if (!startValue || !endValue) {
      if (startValue) startInput.classList.add('date-valid');
      if (endValue) endInput.classList.add('date-valid');
      this.hideDateRangeError();
      return;
    }

    // Both are filled - validate range
    const startDate = new Date(startValue);
    const endDate = new Date(endValue);
    const now = new Date();

    // Check if end date is after start date
    if (endDate <= startDate) {
      startInput.classList.add('date-error');
      endInput.classList.add('date-error');
      this.showDateRangeError('End date must be after start date');
      return;
    }

    // Warn if start date is in the past (but allow it)
    if (startDate < now) {
      startInput.classList.add('date-warning');
    } else {
      startInput.classList.add('date-valid');
    }

    // Warn if end date is in the past
    if (endDate < now) {
      endInput.classList.add('date-error');
      this.showDateRangeError('End date cannot be in the past');
      return;
    } else {
      endInput.classList.add('date-valid');
    }

    this.hideDateRangeError();
  }

  showDateRangeError(message) {
    let errorElement = document.getElementById('date-range-error');
    if (!errorElement) {
      errorElement = document.createElement('div');
      errorElement.id = 'date-range-error';
      errorElement.className = 'date-range-error';
      errorElement.style.cssText = `
        color: var(--error-color, #f44336);
        font-size: 12px;
        margin-top: 4px;
        padding: 4px 0;
        font-weight: 500;
      `;

      const dateSection = document.querySelector('.date-range-section');
      if (dateSection) {
        dateSection.appendChild(errorElement);
      }
    }
    errorElement.textContent = message;
    errorElement.style.display = 'block';
  }

  hideDateRangeError() {
    const errorElement = document.getElementById('date-range-error');
    if (errorElement) {
      errorElement.style.display = 'none';
    }
  }

  showCustomConfirm(message) {
    return new Promise((resolve) => {
      // Create modal overlay
      const overlay = document.createElement('div');
      overlay.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.5);
        display: flex;
        justify-content: center;
        align-items: center;
        z-index: 10000;
        font-family: var(--paper-font-body1_-_font-family, 'Roboto', sans-serif);
      `;

      // Create dialog box
      const dialog = document.createElement('div');
      dialog.style.cssText = `
        background: var(--primary-background-color, white);
        border-radius: 8px;
        padding: 24px;
        max-width: 400px;
        min-width: 300px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        text-align: center;
        color: var(--primary-text-color, black);
      `;

      // Create message
      const messageEl = document.createElement('div');
      messageEl.textContent = message;
      messageEl.style.cssText = `
        margin-bottom: 20px;
        font-size: 16px;
        line-height: 1.4;
      `;

      // Create button container
      const buttonContainer = document.createElement('div');
      buttonContainer.style.cssText = `
        display: flex;
        justify-content: center;
        gap: 12px;
      `;

      // Create Cancel button
      const cancelBtn = document.createElement('button');
      cancelBtn.textContent = 'Cancel';
      cancelBtn.style.cssText = `
        padding: 8px 16px;
        border: 1px solid var(--divider-color, #ccc);
        background: var(--secondary-background-color, #f5f5f5);
        color: var(--primary-text-color, black);
        border-radius: 4px;
        cursor: pointer;
        font-size: 14px;
        min-width: 80px;
      `;

      // Create Reset button
      const resetBtn = document.createElement('button');
      resetBtn.textContent = 'Reset';
      resetBtn.style.cssText = `
        padding: 8px 16px;
        border: none;
        background: var(--error-color, #f44336);
        color: white;
        border-radius: 4px;
        cursor: pointer;
        font-size: 14px;
        min-width: 80px;
      `;

      // Add event listeners
      cancelBtn.addEventListener('click', () => {
        document.body.removeChild(overlay);
        resolve(false);
      });

      resetBtn.addEventListener('click', () => {
        document.body.removeChild(overlay);
        resolve(true);
      });

      // Close on overlay click
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
          document.body.removeChild(overlay);
          resolve(false);
        }
      });

      // Assemble dialog
      buttonContainer.appendChild(cancelBtn);
      buttonContainer.appendChild(resetBtn);
      dialog.appendChild(messageEl);
      dialog.appendChild(buttonContainer);
      overlay.appendChild(dialog);
      document.body.appendChild(overlay);

      // Focus the reset button
      resetBtn.focus();
    });
  }

  async toggleSlot(slotNumber) {
    // Find current lock and slot details
    const currentLock = this._locks?.find(l => l.attributes.lock_entity_id === this._currentLockEntityId);
    if (!currentLock) {
      return;
    }

    const slotDetails = currentLock.attributes?.slot_details?.[`slot_${slotNumber}`];
    if (!slotDetails) {
      return;
    }

    const isCurrentlyActive = slotDetails.is_active;
    const action = isCurrentlyActive ? 'disable_slot' : 'enable_slot';
    const actionText = isCurrentlyActive ? 'disable' : 'enable';

    // No confirmation needed - just toggle immediately
    // Show card spinner during enable/disable operation
    const message = isCurrentlyActive ? 'Disabling...' : 'Enabling...';
    this.showCardSpinner(this._currentLockEntityId, message);

    try {
      await this.callService(action, {
        entity_id: this._currentLockEntityId,
        code_slot: parseInt(slotNumber)
      });
    } finally {
      // Hide card spinner when operation completes
      this.hideCardSpinner(this._currentLockEntityId);

      // Force a refresh to update the UI
      setTimeout(() => {
        this.loadLockData();
      }, 500);
    }
  }


  // ----- Zone card rendering helpers (Phase 3b) ---------------------------

  // Escape a string for safe interpolation into single-quoted inline JS
  // handlers and HTML text. Prevents broken markup / handler injection from
  // user-named zones and locks.
  // - Inputs: value (any).
  // - Outputs: HTML/JS-safe string.
  _esc(value) { return escHtml(value); }

  // Map a zone code-slot (from the zones API) to display color/status,
  // mirroring the backend color hierarchy: grey disabled -> green active.
  // - Inputs: slot (zone code_slot object).
  // - Outputs: { color, title, status }.
  _zoneSlotDisplay(slot) { return zoneSlotDisplay(slot); }

  // Resolve whether a collapsible section is currently expanded.
  // Reads the user's stored toggle if present; otherwise falls back to the
  // computed per-zone default (so populated sections start collapsed but the
  // user's explicit choice persists across re-renders/refreshes).
  // - Inputs: zoneId (str), section (str: 'locks'|'slots'|'log'),
  //   defaultExpanded (bool).
  // - Outputs: bool (true = expanded).
  _isSectionExpanded(zoneId, section, defaultExpanded) {
    const key = `${zoneId}:${section}`;
    const stored = this._sectionState[key];
    return (stored === undefined) ? !!defaultExpanded : !!stored;
  }

  // Render the fixed-height locks section for a zone: a clickable collapse
  // header (with chevron + "+" add-lock affordance) over a uniform scroll list
  // of member rows. Collapsed by default when the zone has >= 1 member lock;
  // expanded when empty so the "+" affordance is visible.
  // - Inputs: zone (zone object from the API).
  // - Outputs: HTML string.
  renderLocksSection(zone) {
    const zid = this._esc(zone.zone_id);
    const members = Array.isArray(zone.members) ? zone.members : [];
    const rows = members.length
      ? members.map(m => this.renderLockRow(zone, m)).join('')
      : '<div class="zone-locks-empty">No locks in this zone yet. Use + to add one.</div>';

    const expanded = this._isSectionExpanded(zone.zone_id, 'locks', members.length === 0);
    const chevron = expanded ? 'mdi:chevron-down' : 'mdi:chevron-up';

    return `
      <div class="zone-locks-section">
        <div class="zone-section-head" onclick="SmartLockManagerPanel.toggleSection('${zid}', 'locks')">
          <span class="zone-section-title">Locks${members.length ? ` (${members.length})` : ''}</span>
          <div class="zone-section-head-right">
            <button class="zone-add-lock-btn"
                    onclick="event.stopPropagation(); SmartLockManagerPanel.toggleLockPicker('${zid}')"
                    title="Add an unhomed lock to this zone">
              <ha-icon icon="mdi:plus" style="width:18px;height:18px;"></ha-icon>
            </button>
            <ha-icon icon="${chevron}" class="zone-section-chevron${expanded ? ' expanded' : ''}" style="width:18px;height:18px;"></ha-icon>
          </div>
        </div>
        <div class="zone-locks-body" style="${expanded ? '' : 'display:none;'}">
          <div class="zone-locks-list">${rows}</div>
          ${this._openPicker === zone.zone_id ? this.renderLockPicker(zone) : ''}
        </div>
      </div>
    `;
  }

  // Render one member lock row: [action+status icon | name | per-lock gear].
  // The left icon is ALWAYS clickable (lock/unlock); jammed/offline shown via
  // color + a small indicator but never disabled.
  // - Inputs: zone (zone obj), member (member obj).
  // - Outputs: HTML string.
  renderLockRow(zone, member) {
    const eid = this._esc(member.entity_id);
    const zid = this._esc(zone.zone_id);
    const state = member.lock_state || 'unknown';
    const isLocked = state === 'locked';
    const jammed = !!member.is_jammed;
    const offline = state === 'unavailable' || state === 'unknown';

    // Action+status icon: green locked / red unlocked; jammed overrides to amber.
    let icon = isLocked ? 'mdi:lock' : 'mdi:lock-open';
    let color = isLocked ? '#4a7c2a' : '#cc3333';
    if (jammed) { icon = 'mdi:lock-alert'; color = '#f0a020'; }
    if (offline && !jammed) { color = '#9e9e9e'; }

    const subtle = [];
    if (jammed) subtle.push('jammed');
    if (offline) subtle.push('offline');
    if (member.battery_level != null && member.battery_level <= 15) {
      subtle.push(`battery ${member.battery_level}%`);
    }
    const subtitle = subtle.length
      ? `<span class="zone-lock-substate">${this._esc(subtle.join(' · '))}</span>`
      : '';

    const name = this._esc(member.friendly_name || member.entity_id);

    return `
      <div class="zone-lock-row">
        <button class="zone-lock-action ${offline ? 'is-offline' : ''} ${jammed ? 'is-jammed' : ''}"
                onclick="SmartLockManagerPanel.toggleLock('${eid}', '${this._esc(state)}')"
                title="${isLocked ? 'Click to unlock' : 'Click to lock'}${jammed ? ' (jammed)' : ''}${offline ? ' (offline)' : ''}"
                style="color:${color};">
          <ha-icon icon="${icon}" style="width:24px;height:24px;"></ha-icon>
        </button>
        <div class="zone-lock-name" title="${name}">
          ${name}${subtitle}
        </div>
        <button class="zone-lock-gear"
                onclick="SmartLockManagerPanel.toggleLockMenu('${eid}', event)"
                title="Lock actions">
          <ha-icon icon="mdi:cog" style="width:18px;height:18px;"></ha-icon>
        </button>
        ${this._openLockMenu === member.entity_id ? `
          <div class="zone-menu zone-lock-menu" style="top:${this._lockMenuPos ? this._lockMenuPos.top : 0}px; left:${this._lockMenuPos ? this._lockMenuPos.left : 0}px;">
            <button class="zone-menu-item"
                    onclick="SmartLockManagerPanel.editLock('${eid}')">
              <ha-icon icon="mdi:pencil" style="width:16px;height:16px;"></ha-icon>
              <span>Edit</span>
            </button>
            <div class="zone-menu-sep"></div>
            <button class="zone-menu-item danger"
                    onclick="SmartLockManagerPanel.removeLockFromZone('${zid}', '${eid}')">
              <ha-icon icon="mdi:exit-run" style="width:16px;height:16px;"></ha-icon>
              <span>Remove from zone</span>
            </button>
          </div>
        ` : ''}
      </div>
    `;
  }

  // Render the unhomed-lock picker dropdown for a zone's "+" button.
  // - Inputs: zone (zone obj).
  // - Outputs: HTML string.
  renderLockPicker(zone) {
    const zid = this._esc(zone.zone_id);
    const pool = this._unhomedLocks || [];
    const items = pool.length
      ? pool.map(l => {
          const eid = this._esc(l.entity_id);
          const name = this._esc(l.friendly_name || l.entity_id);
          return `
            <button class="zone-picker-item"
                    onclick="SmartLockManagerPanel.addLockToZone('${zid}', '${eid}')">
              <ha-icon icon="mdi:lock-plus" style="width:18px;height:18px;"></ha-icon>
              <span title="${name}">${name}</span>
            </button>
          `;
        }).join('')
      : '<div class="zone-picker-empty">No unhomed locks available.</div>';
    return `<div class="zone-menu zone-picker">${items}</div>`;
  }

  // Render the zone-level code slot grid from the zone's own code_slots.
  // Re-points the existing slot grid at zone data; visual layout unchanged.
  // - Inputs: zone (zone obj).
  // - Outputs: HTML string.
  renderZoneSlots(zone) {
    const zid = this._esc(zone.zone_id);
    const slots = Array.isArray(zone.code_slots) ? zone.code_slots : [];
    // "Configured" = slots that actually hold a code (has_code), matching the
    // zone stat. Collapse by default only when at least one is configured.
    const configuredCount = slots.filter(s => s.has_code).length;
    const expanded = this._isSectionExpanded(zone.zone_id, 'slots', configuredCount === 0);
    const chevron = expanded ? 'mdi:chevron-down' : 'mdi:chevron-up';

    const header = `
      <div class="zone-section-head" onclick="SmartLockManagerPanel.toggleSection('${zid}', 'slots')">
        <span class="zone-section-title">Slots${configuredCount ? ` (${configuredCount})` : ''}</span>
        <div class="zone-section-head-right">
          <ha-icon icon="${chevron}" class="zone-section-chevron${expanded ? ' expanded' : ''}" style="width:18px;height:18px;"></ha-icon>
        </div>
      </div>
    `;

    if (!slots.length) {
      return `
        <div class="zone-slots-section">
          ${header}
          <div class="zone-slots-body" style="${expanded ? '' : 'display:none;'}">
            <div class="slots-container"><div class="zone-locks-empty">No code slots.</div></div>
          </div>
        </div>
      `;
    }
    return `
      <div class="zone-slots-section">
        ${header}
        <div class="zone-slots-body" style="${expanded ? '' : 'display:none;'}">
      <div class="slots-container">
        ${slots.map(slot => {
          const d = this._zoneSlotDisplay(slot);
          return `
            <div class="slot-row" onclick="SmartLockManagerPanel.openZoneSlot('${zid}', ${slot.slot_number})">
              <div class="slot-indicator" style="background-color: ${d.color};"></div>
              <div class="slot-info">
                <div class="slot-name">${this._esc(d.title)}</div>
                <div class="slot-details">${this._esc(d.status)}</div>
              </div>
              ${slot.has_code ? `
                <div class="slot-actions">
                  <button class="slot-action-btn toggle-btn ${slot.is_active ? 'active' : 'inactive'}"
                          onclick="event.stopPropagation(); SmartLockManagerPanel.toggleZoneSlot('${zid}', ${slot.slot_number})"
                          title="${slot.is_active ? 'Disable this slot' : 'Enable this slot'}">
                    ${slot.is_active ? '⏸' : '▶'}
                  </button>
                  <button class="slot-action-btn clear-btn"
                          onclick="event.stopPropagation(); SmartLockManagerPanel.clearZoneSlot('${zid}', ${slot.slot_number})"
                          title="Clear slot and remove from locks">🗙</button>
                  ${slot.use_count > 0 ? `<button class="slot-action-btn reset-btn"
                          onclick="event.stopPropagation(); SmartLockManagerPanel.resetZoneSlotUsage('${zid}', ${slot.slot_number})"
                          title="Reset Usage">🔄</button>` : ''}
                </div>
              ` : ''}
            </div>
          `;
        }).join('')}
      </div>
        </div>
      </div>
    `;
  }

  // Render a zone's per-door access log with UNIFORM fixed-height, single-line
  // rows (long names truncate with ellipsis; full value in the title tooltip).
  // Merges every member lock's access_log (read from the sensor states) onto
  // one timeline, most-recent first.
  // - Inputs: zone (zone obj).
  // - Outputs: HTML string.
  renderZoneAccessLog(zone) {
    const bodyId = `access-log-body-zone-${this._esc(zone.zone_id)}`;
    const members = Array.isArray(zone.members) ? zone.members : [];
    const multiDoor = members.length > 1;

    // Pull each member's access_log off its SLM summary sensor.
    let combined = [];
    members.forEach(m => {
      const sensor = this._findSensorForLock(m.entity_id);
      const log = sensor?.attributes?.access_log || [];
      const doorName = m.friendly_name || m.entity_id;
      log.forEach(e => combined.push({ ...e, _doorName: e.lock_name || doorName }));
    });

    combined.sort((a, b) => (Date.parse(b.timestamp || '') || 0) - (Date.parse(a.timestamp || '') || 0));
    combined = combined.slice(0, 25);

    const rows = combined.map(entry => {
      const p = this.getAccessLogPresentation(entry);
      const badge = multiDoor
        ? `<span class="al-lock-badge" title="${this._esc(entry._doorName)}">${this._esc(entry._doorName)}</span>`
        : '';
      const attr = this._esc(p.attr);
      // Status word, Title Case (the icon already capitalizes visually).
      const rawStatus = entry.action || 'unknown';
      const statusText = this._esc(rawStatus.charAt(0).toUpperCase() + rawStatus.slice(1));
      // Whole-row tooltip: status - door/zone - event/method (incl. user/slot) - time.
      const tooltipParts = [statusText];
      if (entry._doorName) tooltipParts.push(this._esc(entry._doorName));
      if (attr) tooltipParts.push(attr);
      const ts = this._esc(this.formatAccessLogTime(entry.timestamp));
      if (ts) tooltipParts.push(ts);
      const rowTooltip = tooltipParts.join(' - ');
      return `
        <div class="access-log-row" title="${rowTooltip}">
          <ha-icon class="al-icon" icon="${p.icon}" title="${rowTooltip}" style="width:18px;height:18px;color:${p.color};flex-shrink:0;text-transform:capitalize;"></ha-icon>
          ${badge}
          <span class="al-attr-text">${attr}</span>
          <span class="al-time">${ts}</span>
        </div>
      `;
    }).join('');

    // Access Log is expanded by default.
    const expanded = this._isSectionExpanded(zone.zone_id, 'log', true);
    const chevron = expanded ? 'mdi:chevron-down' : 'mdi:chevron-up';

    return `
      <div class="access-log-section">
        <div class="access-log-header" onclick="SmartLockManagerPanel.toggleSection('${this._esc(zone.zone_id)}', 'log')">
          <span>Access Log${combined.length ? ` (${combined.length})` : ''}</span>
          <ha-icon icon="${chevron}" class="zone-section-chevron${expanded ? ' expanded' : ''}" style="width:18px;height:18px;"></ha-icon>
        </div>
        <div class="access-log-body" id="${bodyId}" style="${expanded ? '' : 'display:none;'}">
          ${combined.length ? rows : '<div class="access-log-empty">No access events recorded yet.</div>'}
        </div>
      </div>
    `;
  }

  // Resolve the severity icon + color + recovery styling for one dev alert.
  // - Inputs: alert (alert record from the zones API).
  // - Outputs: { icon, color, label } for the row marker.
  getDevAlertPresentation(alert) { return devAlertPresentation(alert); }

  // Render a zone's "Alerts" section: a collapsible header over uniform
  // single-line rows (severity marker / door / type / message / time),
  // recovery rows styled distinctly. Only shown when an alert engine is
  // constructed (the API only carries alerts then).
  // - Inputs: zone (zone obj with alerts[]).
  // - Outputs: HTML string ('' when no engine is constructed).
  renderZoneDevAlerts(zone) {
    if (!this._observeOnly) return '';
    const zid = this._esc(zone.zone_id);
    const alerts = Array.isArray(zone.alerts) ? zone.alerts : [];

    const rows = alerts.map(alert => {
      const p = this.getDevAlertPresentation(alert);
      const door = this._esc(alert.door_name || alert.member_entity_id || '');
      const type = this._esc((alert.alert_type || '').replace(/_/g, ' '));
      const msg = this._esc(alert.message || '');
      const ts = this._esc(this.formatAccessLogTime(alert.timestamp));
      const tooltip = [p.label, door, type, msg, ts].filter(Boolean).join(' - ');
      const recoveryCls = alert.is_recovery ? ' dev-alert-recovery' : '';
      const intentLine = this.renderNotifyIntents(alert);
      return `
        <div class="access-log-row dev-alert-row${recoveryCls}" title="${tooltip}">
          <ha-icon class="al-icon" icon="${p.icon}" style="width:18px;height:18px;color:${p.color};flex-shrink:0;"></ha-icon>
          <span class="al-lock-badge" title="${door}">${door}</span>
          <span class="al-attr-text">${type ? `[${type}] ` : ''}${msg}</span>
          <span class="al-time">${ts}</span>
        </div>
        ${intentLine}
      `;
    }).join('');

    // Collapsed by default (secondary diagnostic info).
    const expanded = this._isSectionExpanded(zone.zone_id, 'alerts', false);
    const chevron = expanded ? 'mdi:chevron-down' : 'mdi:chevron-up';

    return `
      <div class="access-log-section dev-alerts-section">
        <div class="access-log-header" onclick="SmartLockManagerPanel.toggleSection('${zid}', 'alerts')">
          <span>Alerts${alerts.length ? ` (${alerts.length})` : ''}</span>
          <ha-icon icon="${chevron}" class="zone-section-chevron${expanded ? ' expanded' : ''}" style="width:18px;height:18px;"></ha-icon>
        </div>
        <div class="dev-alerts-banner" style="${expanded ? '' : 'display:none;'}">
          Observe-only — no notifications sent
        </div>
        <div class="access-log-body" style="${expanded ? '' : 'display:none;'}">
          ${alerts.length ? rows : '<div class="access-log-empty">No alerts recorded yet.</div>'}
        </div>
      </div>
    `;
  }

  // Render the DRY-RUN "would-notify" sub-line for one dev alert. Shows what
  // the notification layer WOULD have sent (email recipients / mobile targets)
  // without anything actually being sent. Recipient emails are non-secret user
  // config (safe to show); PINs are never present in intents.
  // - Inputs: alert (dev alert record with optional notify_intents[]).
  // - Outputs: HTML string ('' when there are no intents).
  renderNotifyIntents(alert) {
    const intents = Array.isArray(alert.notify_intents) ? alert.notify_intents : [];
    if (!intents.length) return '';
    const parts = intents.map(intent => {
      if (intent.channel === 'email') {
        const to = Array.isArray(intent.recipients) ? intent.recipients.join(', ') : '';
        return `✉ would email ${this._esc(to)}`;
      }
      if (intent.channel === 'mobile') {
        const tg = Array.isArray(intent.targets) ? intent.targets.join(', ') : '';
        return `📱 would push ${this._esc(tg)}`;
      }
      return '';
    }).filter(Boolean);
    if (!parts.length) return '';
    return `
      <div class="dev-alert-intents" title="DRY-RUN — nothing actually sent">
        ${parts.join(' &nbsp;·&nbsp; ')}
      </div>
    `;
  }

  // Find the SLM summary sensor object for a given lock entity_id (used to read
  // access_log attributes that the zones API does not carry).
  // - Inputs: lockEntityId (str).
  // - Outputs: { entity_id, attributes } lock object, or undefined.
  _findSensorForLock(lockEntityId) {
    return (this._locks || []).find(l => l.attributes?.lock_entity_id === lockEntityId);
  }

  // Render one complete zone card: header (title + refresh + gear menu),
  // locks section, slots, access log.
  // - Inputs: zone (zone obj).
  // - Outputs: HTML string.
  // Render a compact amber warning banner when a zone has slot(s) whose code
  // genuinely FAILED to sync on a member lock. Pending/in-flight slots never
  // appear here (only error_slots from the API). Returns '' when no errors so
  // healthy zones show no banner.
  // - Inputs: zone (zone object from the API).
  // - Outputs: HTML string (possibly empty).
  // Format an ISO timestamp as a local short time, e.g. "1:20 PM".
  // - Inputs: iso (str|null).
  // - Outputs: short local time string; '' if falsy or unparseable.
  _fmtTime(iso) { return fmtTime(iso); }

  // Resolve the effective alert-snooze for a zone: a future per-zone snooze
  // wins; otherwise a future global pause applies; otherwise none.
  // - Inputs: zone (zone obj).
  // - Outputs: { until: <iso>, global: <bool> } or null.
  _zoneSnoozeUntil(zone) {
    const now = new Date();
    const zu = zone && zone.snoozed_until;
    if (zu && new Date(zu) > now) return { until: zu, global: false };
    const gu = this._snooze && this._snooze.global_until;
    if (gu && new Date(gu) > now) return { until: gu, global: true };
    return null;
  }

  // Compute hours from now until the next occurrence of a zone's open time,
  // for the "Until morning" snooze. Open time = zone.settings.business_hours
  // .open_time (a non-empty "HH:MM"), else "08:30". Build a Date for today at
  // that time; if it's already past, add one day. hours = (target - now) /
  // 3,600,000 ms, clamped to [0.25, 24] to satisfy the service bounds.
  // - Inputs: zone (zone obj).
  // - Outputs: float hours.
  _untilMorningHours(zone) { return untilMorningHours(zone); }

  // Render the global alert-pause banner under the page header when a global
  // pause is active (future global_until). Mirrors the amber sync-warning
  // palette. Offers a "Resume all" action.
  // - Inputs: none (reads this._snooze).
  // - Outputs: HTML string ('' when no active global pause).
  _renderGlobalSnoozeBanner() {
    const gu = this._snooze && this._snooze.global_until;
    if (!gu || !(new Date(gu) > new Date())) return '';
    const t = this._esc(this._fmtTime(gu));
    return `
      <div class="zone-sync-warning global-snooze-banner" role="status">
        <ha-icon icon="mdi:bell-sleep-outline" style="width:18px;height:18px;flex:0 0 auto;"></ha-icon>
        <span>⏸ All alerts paused until ${t}</span>
        <button class="global-snooze-resume"
                onclick="SmartLockManagerPanel.resumeAll()"
                title="Resume all alerts">Resume all</button>
      </div>
    `;
  }

  renderZoneSyncWarning(zone) {
    const errs = Array.isArray(zone.error_slots) ? zone.error_slots : [];
    if (!errs.length) return '';
    const label = errs.length === 1
      ? `Slot ${this._esc(errs[0])} failed to sync`
      : `Slots ${this._esc(errs.join(', '))} failed to sync`;
    return `
      <div class="zone-sync-warning" role="alert">
        <ha-icon icon="mdi:alert" style="width:18px;height:18px;flex:0 0 auto;"></ha-icon>
        <span>${label}</span>
      </div>
    `;
  }

  renderZoneCard(zone) {
    const zid = this._esc(zone.zone_id);
    const name = this._esc(zone.name || 'Zone');
    const menuOpen = this._openZoneMenu === zone.zone_id;
    const snz = this._zoneSnoozeUntil(zone);
    const snoozeMenuOpen = this._openSnoozeMenu === zone.zone_id;
    const snoozeBadge = snz
      ? `<span class="zone-snooze-badge">⏸ ${snz.global
          ? `All alerts paused until ${this._esc(this._fmtTime(snz.until))}`
          : `Snoozed until ${this._esc(this._fmtTime(snz.until))}`}</span>`
      : '';

    return `
      <div class="lock-card zone-card">
        <div class="lock-header zone-header">
          <div class="zone-title-wrap">
            <h3 class="lock-title" title="${name}">${name}</h3>
            ${snoozeBadge}
            <div class="saving-spinner" id="saving-spinner-zone-${zid}" style="display:none;">
              <div class="spinner"></div><span>Saving...</span>
            </div>
          </div>
          <div class="lock-header-right">
            ${snz ? `
            <button class="zone-snooze-btn"
                    onclick="event.stopPropagation(); SmartLockManagerPanel.unsnoozeZone('${zid}')"
                    title="Resume alerts"
                    style="background:none;border:none;cursor:pointer;padding:4px;border-radius:4px;display:flex;align-items:center;color:#f0a020;">
              <ha-icon icon="mdi:bell-ring-outline" style="width:22px;height:22px;"></ha-icon>
            </button>
            ` : `
            <div class="zone-snooze-wrap" style="position:relative;">
              <button class="zone-snooze-btn"
                      onclick="event.stopPropagation(); SmartLockManagerPanel.toggleSnoozeMenu('${zid}')"
                      title="Snooze alerts"
                      style="background:none;border:none;cursor:pointer;padding:4px;border-radius:4px;display:flex;align-items:center;color:var(--secondary-text-color);">
                <ha-icon icon="mdi:bell-sleep-outline" style="width:22px;height:22px;"></ha-icon>
              </button>
              ${snoozeMenuOpen ? `
                <div class="zone-menu zone-snooze-menu">
                  <button class="zone-menu-item" onclick="event.stopPropagation(); SmartLockManagerPanel.snoozeZone('${zid}', 1)">
                    <ha-icon icon="mdi:bell-sleep-outline" style="width:16px;height:16px;"></ha-icon><span>1h</span>
                  </button>
                  <button class="zone-menu-item" onclick="event.stopPropagation(); SmartLockManagerPanel.snoozeZone('${zid}', 2)">
                    <ha-icon icon="mdi:bell-sleep-outline" style="width:16px;height:16px;"></ha-icon><span>2h</span>
                  </button>
                  <button class="zone-menu-item" onclick="event.stopPropagation(); SmartLockManagerPanel.snoozeZone('${zid}', 4)">
                    <ha-icon icon="mdi:bell-sleep-outline" style="width:16px;height:16px;"></ha-icon><span>4h</span>
                  </button>
                  <button class="zone-menu-item" onclick="event.stopPropagation(); SmartLockManagerPanel.snoozeZoneUntilMorning('${zid}')">
                    <ha-icon icon="mdi:weather-sunset-up" style="width:16px;height:16px;"></ha-icon><span>Until morning</span>
                  </button>
                </div>
              ` : ''}
            </div>
            `}
            <button class="refresh-btn zone-refresh-btn"
                    onclick="SmartLockManagerPanel.refreshZone('${zid}')"
                    title="Refresh this zone"
                    style="background:none;border:none;cursor:pointer;padding:4px;border-radius:4px;display:flex;align-items:center;color:var(--secondary-text-color);">
              <ha-icon icon="mdi:sync" style="width:22px;height:22px;"></ha-icon>
            </button>
            <div class="zone-gear-wrap">
              <button class="settings-btn"
                      onclick="SmartLockManagerPanel.toggleZoneMenu('${zid}')"
                      title="Zone settings"
                      style="color:#708090;">
                <ha-icon icon="mdi:cog" style="width:22px;height:22px;"></ha-icon>
              </button>
              ${menuOpen ? `
                <div class="zone-menu zone-header-menu">
                  <button class="zone-menu-item" onclick="SmartLockManagerPanel.renameZone('${zid}')">
                    <ha-icon icon="mdi:rename-box" style="width:16px;height:16px;"></ha-icon><span>Rename zone</span>
                  </button>
                  <button class="zone-menu-item" onclick="SmartLockManagerPanel.openZoneSettings('${zid}')">
                    <ha-icon icon="mdi:tune" style="width:16px;height:16px;"></ha-icon><span>Settings</span>
                  </button>
                  <button class="zone-menu-item" onclick="SmartLockManagerPanel.applyZoneCodes('${zid}')">
                    <ha-icon icon="mdi:sync" style="width:16px;height:16px;"></ha-icon><span>Re-apply codes</span>
                  </button>
                  <button class="zone-menu-item" onclick="SmartLockManagerPanel.clearZoneCodes('${zid}')">
                    <ha-icon icon="mdi:broom" style="width:16px;height:16px;"></ha-icon><span>Clear codes</span>
                  </button>
                  <div class="zone-menu-sep"></div>
                  <button class="zone-menu-item danger" onclick="SmartLockManagerPanel.deleteZone('${zid}')">
                    <ha-icon icon="mdi:delete" style="width:16px;height:16px;"></ha-icon><span>Delete entire zone</span>
                  </button>
                </div>
              ` : ''}
            </div>
          </div>
        </div>

        <div class="lock-stats">
          <div class="stat-item">
            <div class="stat-value">${zone.active_codes_count ?? 0}</div>
            <div class="stat-label">Active</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">${zone.configured_codes_count ?? 0}</div>
            <div class="stat-label">Configured</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">${zone.slots ?? (zone.code_slots ? zone.code_slots.length : 0)}</div>
            <div class="stat-label">Slots</div>
          </div>
        </div>

        ${this.renderZoneSyncWarning(zone)}
        ${this.renderLocksSection(zone)}
        ${this.renderZoneSlots(zone)}
        ${this.renderZoneAccessLog(zone)}
        ${this.renderZoneDevAlerts(zone)}
      </div>
    `;
  }

  render() {
    if (!this.shadowRoot) return;

    const locks = this._locks || [];
    const zones = this._zones || [];

    this.shadowRoot.innerHTML = `
      <style>${panelStyles(this)}</style>

      <div class="header">
        <div class="header-left">
          <ha-icon icon="mdi:lock-smart"></ha-icon>
          <h1>Smart Lock Manager</h1>
        </div>
        <div class="header-controls" style="display: flex; gap: 8px;">
          <button class="refresh-btn new-zone-btn"
                  onclick="SmartLockManagerPanel.createZone()"
                  title="Create a new empty zone"
                  style="background: none; border: none; cursor: pointer; padding: 4px; border-radius: 4px; display: flex; align-items: center; color: var(--primary-color); opacity: 0.85; transition: opacity 0.2s;">
            <ha-icon icon="mdi:plus-box" style="margin-right: 3px; width: 24px; height: 24px;"></ha-icon>
            <span style="height: 20px; line-height: 20px; display: flex; align-items: center; margin-left: 4px;">New Zone</span>
          </button>
          <div class="pause-all-wrap" style="position: relative; display: flex;">
            <button class="refresh-btn pause-all-btn"
                    onclick="SmartLockManagerPanel.togglePauseAllMenu()"
                    title="Pause alerts for every zone"
                    style="background: none; border: none; cursor: pointer; padding: 4px; border-radius: 4px; display: flex; align-items: center; color: var(--primary-text-color); opacity: 0.7; transition: opacity 0.2s;">
              <ha-icon icon="mdi:bell-sleep-outline" style="margin-right: 3px; width: 24px; height: 24px;"></ha-icon>
              <span style="height: 20px; line-height: 20px; display: flex; align-items: center; margin-left: 4px;">Pause all</span>
            </button>
            ${this._openPauseAllMenu ? `
              <div class="zone-menu pause-all-menu">
                <button class="zone-menu-item" onclick="SmartLockManagerPanel.pauseAll(1)">
                  <ha-icon icon="mdi:bell-sleep-outline" style="width:16px;height:16px;"></ha-icon><span>1h</span>
                </button>
                <button class="zone-menu-item" onclick="SmartLockManagerPanel.pauseAll(2)">
                  <ha-icon icon="mdi:bell-sleep-outline" style="width:16px;height:16px;"></ha-icon><span>2h</span>
                </button>
                <button class="zone-menu-item" onclick="SmartLockManagerPanel.pauseAll(4)">
                  <ha-icon icon="mdi:bell-sleep-outline" style="width:16px;height:16px;"></ha-icon><span>4h</span>
                </button>
                <button class="zone-menu-item" onclick="SmartLockManagerPanel.pauseAll(8)">
                  <ha-icon icon="mdi:bell-sleep-outline" style="width:16px;height:16px;"></ha-icon><span>8h</span>
                </button>
              </div>
            ` : ''}
          </div>
          <button class="refresh-btn"
                  onclick="SmartLockManagerPanel.forceRefresh()"
                  title="Refresh all data from Home Assistant"
                  style="background: none; border: none; cursor: pointer; padding: 4px; border-radius: 4px; display: flex; align-items: center; color: var(--primary-text-color); opacity: 0.7; transition: opacity 0.2s;">
            <ha-icon icon="mdi:sync" style="margin-right: 3px; width: 25px; height: 25px;"></ha-icon>
            <span style="height: 20px; line-height: 20px; display: flex; align-items: center; margin-left: 6px;">Refresh All</span>
          </button>
        </div>
      </div>

      ${this._renderGlobalSnoozeBanner()}

      ${(!this._zoneDataLoaded && zones.length === 0) ? `
        <div class="no-locks">
          <ha-icon icon="mdi:lock-outline"></ha-icon>
          <p>Loading zones…</p>
        </div>
      ` : zones.length === 0 ? `
        <div class="no-locks">
          <ha-icon icon="mdi:lock-outline"></ha-icon>
          <p>No zones yet</p>
          <p>Use "New Zone" above to create your first zone</p>
        </div>
      ` : `
        <div class="locks-grid">
          ${zones.map(zone => this.renderZoneCard(zone)).join('')}
        </div>
      `}

      <!-- Slot Edit Modal -->
      <div class="modal">
        <div class="modal-content">
          <div class="modal-header">
            <h3 class="modal-title">Edit Slot ${this._editingSlot}</h3>
            <button class="close-btn" onclick="SmartLockManagerPanel.closeModal()">×</button>
          </div>

          <form id="slot-form">
            <!-- ENABLED TOGGLE MOVED TO TOP -->
            <div class="form-group">
              <label class="toggle-label">
                <input type="checkbox" name="is_active" id="is_active">
                <span class="toggle-text">Enable this slot</span>
              </label>
              <small>Unchecked slots will be disabled (not deleted)</small>
            </div>

            <div class="form-group">
              <label for="user_name">User Name</label>
              <input type="text" id="user_name" name="user_name" placeholder="Enter user name" onkeypress="if(event.key==='Enter') SmartLockManagerPanel.saveSlot()">
            </div>

            <div class="form-group">
              <label for="pin_code">PIN Code</label>
              <input type="text" id="pin_code" name="pin_code" placeholder="Enter PIN code (4-8 digits)" required
                     oninput="SmartLockManagerPanel.validatePinCode(this)"
                     onkeypress="if(event.key==='Enter') SmartLockManagerPanel.saveSlot()">
              <span id="pin-validation-message" class="pin-validation-message" style="opacity: 0;"></span>
            </div>

            <!-- Date Range Access Control -->
            <div class="date-range-section">
              <div class="date-range-header">Access Date Range</div>
              <small style="color: var(--secondary-text-color); margin-bottom: 16px; display: block;">Leave fields empty for unlimited access</small>

              <div class="form-group">
                <label for="access_from">Start Date & Time</label>
                <input type="datetime-local" id="access_from" name="access_from" onchange="SmartLockManagerPanel.validateDateRange()">
                <small>When access begins (leave empty for immediate access)</small>
              </div>

              <div class="form-group">
                <label for="access_to">End Date & Time</label>
                <input type="datetime-local" id="access_to" name="access_to" onchange="SmartLockManagerPanel.validateDateRange()">
                <small>When access expires (leave empty for permanent access)</small>
              </div>
            </div>

            <div class="form-group">
              <label for="max_uses">Max Uses (-1 for unlimited)</label>
              <input type="number" id="max_uses" name="max_uses" value="-1" min="-1" onkeypress="if(event.key==='Enter') SmartLockManagerPanel.saveSlot()">
            </div>

            <div class="form-group">
              <label for="allowed_hours">Allowed Hours</label>
              <select id="allowed_hours" name="allowed_hours" multiple size="6" class="time-select">
                <option value="0">12:00 AM - 1:00 AM</option>
                <option value="1">1:00 AM - 2:00 AM</option>
                <option value="2">2:00 AM - 3:00 AM</option>
                <option value="3">3:00 AM - 4:00 AM</option>
                <option value="4">4:00 AM - 5:00 AM</option>
                <option value="5">5:00 AM - 6:00 AM</option>
                <option value="6">6:00 AM - 7:00 AM</option>
                <option value="7">7:00 AM - 8:00 AM</option>
                <option value="8">8:00 AM - 9:00 AM</option>
                <option value="9">9:00 AM - 10:00 AM</option>
                <option value="10">10:00 AM - 11:00 AM</option>
                <option value="11">11:00 AM - 12:00 PM</option>
                <option value="12">12:00 PM - 1:00 PM</option>
                <option value="13">1:00 PM - 2:00 PM</option>
                <option value="14">2:00 PM - 3:00 PM</option>
                <option value="15">3:00 PM - 4:00 PM</option>
                <option value="16">4:00 PM - 5:00 PM</option>
                <option value="17">5:00 PM - 6:00 PM</option>
                <option value="18">6:00 PM - 7:00 PM</option>
                <option value="19">7:00 PM - 8:00 PM</option>
                <option value="20">8:00 PM - 9:00 PM</option>
                <option value="21">9:00 PM - 10:00 PM</option>
                <option value="22">10:00 PM - 11:00 PM</option>
                <option value="23">11:00 PM - 12:00 AM</option>
              </select>
              <small>Hold Ctrl/Cmd to select multiple hours. Leave blank for 24/7 access.</small>
            </div>

            <div class="form-group">
              <label for="allowed_days">Allowed Days</label>
              <select id="allowed_days" name="allowed_days" multiple size="7" class="days-select">
                <option value="0">Monday</option>
                <option value="1">Tuesday</option>
                <option value="2">Wednesday</option>
                <option value="3">Thursday</option>
                <option value="4">Friday</option>
                <option value="5">Saturday</option>
                <option value="6">Sunday</option>
              </select>
              <small>Hold Ctrl/Cmd to select multiple days. Leave blank for all days.</small>
            </div>

            <div class="form-group">
              <label>
                <input type="checkbox" name="notify_on_use"> Notify when used
              </label>
            </div>
          </form>

          <div class="form-actions">
            <button class="btn secondary" onclick="SmartLockManagerPanel.closeModal()">Cancel</button>
            <button class="btn" onclick="SmartLockManagerPanel.saveSlot()">Save</button>
          </div>
        </div>
      </div>

      <!-- Lock Settings Modal -->
      <div class="modal" style="display: ${this._settingsModalOpen ? 'flex' : 'none'};">
        <div class="modal-content">
          <!-- Modal Header -->
          <div class="modal-header">
            <h3 class="modal-title">Lock Settings</h3>
            <button class="close-btn" onclick="SmartLockManagerPanel.closeSettings()">×</button>
          </div>

          <!-- Form Container - Full Width -->
          <div class="form-container">
            <form id="settings-form">
            <!-- FRIENDLY NAME -->
            <div class="form-group">
              <label for="friendly_name">Friendly Name</label>
              <input type="text" id="friendly_name" name="friendly_name" value="${this._locks?.find(l => l.attributes.lock_entity_id === this._currentLockEntityId)?.attributes?.friendly_name || this._locks?.find(l => l.attributes.lock_entity_id === this._currentLockEntityId)?.attributes?.lock_name || ''}" placeholder="Lock display name" onkeypress="if(event.key==='Enter') SmartLockManagerPanel.saveSettings()">
              <small>Custom name for this lock (appears on cards and in lists)</small>
            </div>

            <!-- ZONE SETTINGS -->
            <div class="main-lock-settings">
              <!-- NUMBER OF SLOTS -->
              <div class="form-group">
                <label for="slot_count">Number of Slots: <span id="slot_count_display">${this._locks?.find(l => l.attributes.lock_entity_id === this._currentLockEntityId)?.attributes?.total_slots || 10}</span></label>
                <input type="range" id="slot_count" name="slot_count" value="${this._locks?.find(l => l.attributes.lock_entity_id === this._currentLockEntityId)?.attributes?.total_slots || 10}" min="1" max="30" class="slider" oninput="document.getElementById('slot_count_display').textContent = this.value">
                <div class="slider-labels">
                  <span>1</span>
                  <span>30</span>
                </div>
                <small>Reducing slots will clear higher-numbered slots</small>
              </div>

              <!-- BUSINESS HOURS -->
              <div class="form-group">
                <label for="business_hours">Business Hours</label>
                <select id="business_hours" name="business_hours" multiple size="6">
                  <option value="0">12:00 AM - 1:00 AM</option>
                  <option value="1">1:00 AM - 2:00 AM</option>
                  <option value="2">2:00 AM - 3:00 AM</option>
                  <option value="3">3:00 AM - 4:00 AM</option>
                  <option value="4">4:00 AM - 5:00 AM</option>
                  <option value="5">5:00 AM - 6:00 AM</option>
                  <option value="6">6:00 AM - 7:00 AM</option>
                  <option value="7">7:00 AM - 8:00 AM</option>
                  <option value="8">8:00 AM - 9:00 AM</option>
                  <option value="9" selected>9:00 AM - 10:00 AM</option>
                  <option value="10" selected>10:00 AM - 11:00 AM</option>
                  <option value="11" selected>11:00 AM - 12:00 PM</option>
                  <option value="12" selected>12:00 PM - 1:00 PM</option>
                  <option value="13" selected>1:00 PM - 2:00 PM</option>
                  <option value="14" selected>2:00 PM - 3:00 PM</option>
                  <option value="15" selected>3:00 PM - 4:00 PM</option>
                  <option value="16" selected>4:00 PM - 5:00 PM</option>
                  <option value="17" selected>5:00 PM - 6:00 PM</option>
                  <option value="18">6:00 PM - 7:00 PM</option>
                  <option value="19">7:00 PM - 8:00 PM</option>
                  <option value="20">8:00 PM - 9:00 PM</option>
                  <option value="21">9:00 PM - 10:00 PM</option>
                  <option value="22">10:00 PM - 11:00 PM</option>
                  <option value="23">11:00 PM - 12:00 AM</option>
                </select>
                <small>Business hours for auto-lock and alerts (9AM-6PM default)</small>
              </div>

              <!-- BUSINESS DAYS -->
              <div class="form-group">
                <label for="business_days">Business Days</label>
                <select id="business_days" name="business_days" multiple size="7">
                    <option value="0" selected>Monday</option>
                    <option value="1" selected>Tuesday</option>
                    <option value="2" selected>Wednesday</option>
                    <option value="3" selected>Thursday</option>
                  <option value="4" selected>Friday</option>
                  <option value="5">Saturday</option>
                  <option value="6">Sunday</option>
                </select>
                <small>Days when business hours apply (weekdays default)</small>
              </div>

              <div class="form-group">
                <label class="toggle-label">
                  <input type="checkbox" name="auto_lock_enabled" id="auto_lock_enabled">
                  <span class="toggle-text">Auto-lock outside business hours</span>
                </label>
                <small>Automatically lock the door when opened outside business hours</small>
              </div>
            </div> <!-- END main-lock-settings -->
            </form>

            <!-- Form Actions -->
            <div class="form-actions">
              <button class="btn secondary" onclick="SmartLockManagerPanel.closeSettings()">Cancel</button>
              <button class="btn" onclick="SmartLockManagerPanel.saveSettings()">Save Settings</button>
            </div>
          </div> <!-- END form-container -->
        </div>
      </div>

      <!-- Edit Lock Modal (per-lock friendly name) -->
      <div class="modal" style="display: ${this._editLockModalOpen ? 'flex' : 'none'};">
        <div class="modal-content">
          <div class="modal-header">
            <h3 class="modal-title">Edit Lock</h3>
            <button class="close-btn" onclick="SmartLockManagerPanel.closeEditLock()">×</button>
          </div>
          <div class="form-container">
            <form id="edit-lock-form">
              <div class="form-group">
                <label for="edit_lock_friendly_name">Friendly Name</label>
                <input type="text" id="edit_lock_friendly_name" name="friendly_name"
                       value="${this._esc(this._memberByEntity(this._editLockEntityId)?.friendly_name || '')}"
                       placeholder="Lock display name"
                       onkeypress="if(event.key==='Enter'){event.preventDefault();SmartLockManagerPanel.saveEditLock();}">
                <small>Custom name for this lock (appears on cards and in the access log)</small>
              </div>
            </form>
            <div class="form-actions">
              <button class="btn secondary" onclick="SmartLockManagerPanel.closeEditLock()">Cancel</button>
              <button class="btn" onclick="SmartLockManagerPanel.saveEditLock()">Save</button>
            </div>
          </div>
        </div>
      </div>

      <!-- Zone Settings Modal (Phase 4a) -->
      ${this._renderZoneSettingsModal()}

    `;
  }



  // Static methods for event handling
  static selectLock(entityId) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }
    const lock = panel._locks.find(l => l.entity_id === entityId);
    if (lock) {
      panel._selectedLock = lock;
      panel.requestUpdate();
    }
  }

  static openSlot(lockEntityId, slotNumber) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }

    panel._currentLockEntityId = lockEntityId;
    panel.openSlotModal(slotNumber);
  }

  static closeModal() {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }
    panel.closeModal();
  }

  // Collapse/expand a zone's collapsible section ('locks' | 'slots' | 'log').
  // Flips the persisted per-zone/per-section state and re-renders so the choice
  // survives data refreshes and doesn't disturb other cards/sections.
  static toggleSection(zoneId, section) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }
    const key = `${zoneId}:${section}`;
    // Resolve current effective state (stored or computed default) then invert.
    const zone = (panel._zones || []).find(z => String(z.zone_id) === String(zoneId));
    let current;
    if (panel._sectionState[key] !== undefined) {
      current = panel._sectionState[key];
    } else if (section === 'locks') {
      const members = Array.isArray(zone?.members) ? zone.members : [];
      current = members.length === 0;
    } else if (section === 'slots') {
      const slots = Array.isArray(zone?.code_slots) ? zone.code_slots : [];
      current = slots.filter(s => s.has_code).length === 0;
    } else if (section === 'alerts') {
      current = false; // dev alerts default collapsed
    } else {
      current = true; // access log default expanded
    }
    panel._sectionState[key] = !current;
    panel.requestUpdate();
  }

  static saveSlot() {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }
    panel.saveSlotSettings();
  }

  static validatePinCode(input) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }
    panel.validatePinCodeInput(input);
  }

  static toggleSlot(lockEntityId, slotNumber) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }
    panel._currentLockEntityId = lockEntityId;
    panel.toggleSlot(slotNumber);
  }

  static clearSlot(lockEntityId, slotNumber) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }
    panel._currentLockEntityId = lockEntityId;
    panel.clearSlot(slotNumber);
  }

  static resetUsage(lockEntityId, slotNumber) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }
    panel._currentLockEntityId = lockEntityId;
    panel.resetSlotUsage(slotNumber);
  }

  static async refreshLock(lockEntityId) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }

    // Show card spinner during refresh and sync
    panel.showCardSpinner(lockEntityId, 'Syncing...');

    try {
      // Force sync with Z-Wave and refresh data
      await panel.callService('read_zwave_codes', {
        entity_id: lockEntityId
      });

      // Also force a data refresh
      await panel.loadLockData(true);
    } catch (error) {
    } finally {
      // Hide card spinner when operation completes
      panel.hideCardSpinner(lockEntityId);
    }
  }

  static getUsageStats(lockEntityId) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }
    panel.callService('get_usage_stats', {
      entity_id: lockEntityId
    });
  }


  static openSettings(lockEntityId) {

    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }

    panel.openSettingsModal(lockEntityId);
  }

  static closeSettings() {

    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }

    panel.closeSettingsModal();
  }

  static saveSettings() {

    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }

    panel.saveSettings();
  }

  static async forceRefresh() {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }

    // Show spinners on all lock cards
    const lockEntityIds = panel._locks?.map(lock => lock.attributes.lock_entity_id) || [];
    lockEntityIds.forEach(entityId => panel.showCardSpinner(entityId, 'Refreshing...'));

    try {
      // Force sync with Z-Wave for all locks
      for (const entityId of lockEntityIds) {
        await panel.callService('read_zwave_codes', {
          entity_id: entityId
        });
      }

      // Force data refresh (sensor states + zone model)
      await panel.loadLockData(true);
      await panel.loadZoneData();
    } finally {
      // Hide spinners on all lock cards
      lockEntityIds.forEach(entityId => panel.hideCardSpinner(entityId));
    }
  }

  showSaveSpinner() {

    // Try multiple selectors to find the save button
    const selectors = [
      '#settings-form button[onclick*="saveSettings"]',
      'button[onclick*="saveSettings"]',
      '.btn:not(.secondary)',
      '.form-actions .btn:last-child'
    ];

    let saveButton = null;
    for (const selector of selectors) {
      saveButton = this.shadowRoot.querySelector(selector);
      if (saveButton) {
        break;
      }
    }

    if (saveButton) {
      saveButton.disabled = true;
      saveButton.style.opacity = '0.6';
      saveButton.style.cursor = 'wait';
      const originalText = saveButton.textContent;
      saveButton.textContent = 'Saving...';
      saveButton.dataset.originalText = originalText;
    }
  }

  hideSaveSpinner() {

    // Try multiple selectors to find the save button
    const selectors = [
      '#settings-form button[onclick*="saveSettings"]',
      'button[onclick*="saveSettings"]',
      '.btn:not(.secondary)',
      '.form-actions .btn:last-child'
    ];

    let saveButton = null;
    for (const selector of selectors) {
      saveButton = this.shadowRoot.querySelector(selector);
      if (saveButton) {
        break;
      }
    }

    if (saveButton) {
      saveButton.disabled = false;
      saveButton.style.opacity = '1';
      saveButton.style.cursor = 'pointer';
      const originalText = saveButton.dataset.originalText || 'Save Settings';
      saveButton.textContent = originalText;
    } else {
    }
  }

  showCardSpinner(lockEntityId, message = 'Saving...') {
    if (!lockEntityId) {
      return;
    }

    const spinnerId = `saving-spinner-${lockEntityId.replace(/\./g, '_')}`;
    const spinner = this.shadowRoot.getElementById(spinnerId);

    if (spinner) {
      // Update the message
      const messageSpan = spinner.querySelector('span');
      if (messageSpan) {
        messageSpan.textContent = message;
      }
      spinner.style.display = 'inline-flex';
    } else {
    }
  }

  hideCardSpinner(lockEntityId) {
    if (!lockEntityId) {
      return;
    }

    const spinnerId = `saving-spinner-${lockEntityId.replace(/\./g, '_')}`;
    const spinner = this.shadowRoot.getElementById(spinnerId);

    if (spinner) {
      spinner.style.display = 'none';
    } else {
    }
  }

  showSlotSpinner(slotNumber) {
    // Find the slot row element
    const slotRows = this.shadowRoot.querySelectorAll('.slot-row');
    const slotRow = Array.from(slotRows).find(row => {
      const slotText = row.querySelector('.slot-name');
      return slotText && slotText.textContent.includes(`Slot ${slotNumber}:`);
    });

    if (slotRow) {
      // Create overlay if it doesn't exist
      let overlay = slotRow.querySelector('.slot-loading-overlay');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'slot-loading-overlay';
        overlay.style.cssText = `
          position: absolute;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0, 0, 0, 0.3);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 1000;
          border-radius: 4px;
        `;

        // Create animated spinner
        const spinner = document.createElement('div');
        spinner.style.cssText = `
          width: 24px;
          height: 24px;
          border: 2px solid rgba(255, 255, 255, 0.3);
          border-top: 2px solid white;
          border-radius: 50%;
          animation: spin 1s linear infinite;
        `;

        overlay.appendChild(spinner);

        // Make sure slot row has relative positioning
        slotRow.style.position = 'relative';
        slotRow.appendChild(overlay);
      }

      overlay.style.display = 'flex';
    }
  }

  hideSlotSpinner(slotNumber) {
    // Find the slot row element
    const slotRows = this.shadowRoot.querySelectorAll('.slot-row');
    const slotRow = Array.from(slotRows).find(row => {
      const slotText = row.querySelector('.slot-name');
      return slotText && slotText.textContent.includes(`Slot ${slotNumber}:`);
    });

    if (slotRow) {
      const overlay = slotRow.querySelector('.slot-loading-overlay');
      if (overlay) {
        overlay.style.display = 'none';
      }
    }
  }

  static clearAllSlots(lockEntityId) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }

    // Confirm before clearing all slots
    if (confirm('Are you sure you want to clear ALL slots? This will remove all user codes from this lock.')) {
      // Find the current lock to get slot count
      const currentLock = panel._locks?.find(l => l.attributes.lock_entity_id === lockEntityId);
      const totalSlots = currentLock?.attributes?.total_slots || 10;
      const startFrom = currentLock?.attributes?.start_from || 1;

      // Show staggered slot spinners with personality
      for (let i = 0; i < totalSlots; i++) {
        const slotNumber = startFrom + i;
        const delay = i * 150; // 150ms delay between each slot

        setTimeout(() => {
          panel.showSlotSpinner(slotNumber);
        }, delay);
      }

      // Call the clear service
      panel.callService('clear_all_slots', {
        entity_id: lockEntityId
      }).then(() => {
        // Hide all slot spinners after a brief moment
        setTimeout(() => {
          for (let i = 0; i < totalSlots; i++) {
            const slotNumber = startFrom + i;
            panel.hideSlotSpinner(slotNumber);
          }

          // Refresh the data after clearing
          setTimeout(() => {
            panel.loadLockData();
          }, 250);
        }, 800); // Keep spinners visible for a moment to show completion
      }).catch(() => {
        // Hide spinners on error too
        for (let i = 0; i < totalSlots; i++) {
          const slotNumber = startFrom + i;
          panel.hideSlotSpinner(slotNumber);
        }
      });
    }
  }

  static toggleLock(lockEntityId, currentState) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }

    // Determine the action and message based on current state
    const action = currentState === 'locked' ? 'unlock' : 'lock';
    const message = currentState === 'locked' ? 'Unlocking...' : 'Locking...';

    // Show spinner with appropriate message
    panel.showCardSpinner(lockEntityId, message);

    // Call the Home Assistant lock service
    panel.callService(action, {
      entity_id: lockEntityId
    }, 'lock').then(() => {
      // Refresh the data after lock/unlock
      setTimeout(() => {
        panel.loadLockData();
      }, 500);
    }).finally(() => {
      // Hide spinner when operation completes
      panel.hideCardSpinner(lockEntityId);
    });
  }

  // ----- Zone helpers + static handlers (Phase 3b) -----------------------

  // Look up an in-memory zone object by its id.
  _zoneById(zoneId) {
    return (this._zones || []).find(z => z.zone_id === zoneId);
  }

  // ----- Zone Settings modal (Phase 4a) ----------------------------------
  // Day labels for the Mon..Sun (0..6) checkbox rows used by Business Hours
  // and Scheduled Auto-Lock. Centralized so both blocks render identically.
  static get _ZS_DAYS() { return ZS_DAYS; }

  // Render a Mon..Sun checkbox row bound to a given field prefix.
  // - Inputs: idPrefix (str, e.g. "zs_bh_day"), selected (number[] of 0..6).
  // - Outputs: HTML string of 7 inline day checkboxes.
  _zsDayChecks(idPrefix, selected) {
    const sel = Array.isArray(selected) ? selected : [];
    return SmartLockManagerPanel._ZS_DAYS.map((label, i) => `
      <label class="zs-day">
        <input type="checkbox" id="${idPrefix}_${i}" ${sel.includes(i) ? 'checked' : ''}>
        <span>${label}</span>
      </label>`).join('');
  }

  // Build the Zone Settings modal markup from the active zone's `settings`.
  //
  // Reads the current zone's settings (tolerant of missing blocks/keys — every
  // field falls back to the backend's mirror default) and lays out the five
  // config sections. When the modal is closed, renders an empty (display:none)
  // shell so the markup is always present for the show/hide toggle.
  // - Outputs: HTML string for the modal.
  _renderZoneSettingsModal() {
    const open = this._zoneSettingsModalOpen;
    const zone = open ? this._zoneById(this._zoneSettingsZoneId) : null;
    if (!open || !zone) {
      return `<div class="modal" style="display:none;"></div>`;
    }
    const s = zone.settings || {};
    const bh = s.business_hours || {};
    const sal = s.scheduled_auto_lock || {};
    const idle = s.idle_auto_lock || {};
    const al = s.alerts || {};
    const oh = al.outside_hours || {};
    const su = al.sustained_unlock || {};
    const jam = al.jam || {};
    const lb = al.low_battery || {};
    const off = al.offline || {};
    const notify = s.notify || {};
    const email = notify.email || {};
    const mobile = notify.mobile || {};
    const v = (x, d) => (x === undefined || x === null ? d : x);
    const ck = (x) => (x ? 'checked' : '');
    const tiers = Array.isArray(su.tiers) ? su.tiers.join(', ') : '';
    const recips = Array.isArray(email.recipients_override) ? email.recipients_override.join(', ') : '';
    const targets = Array.isArray(mobile.targets) ? mobile.targets.join(', ') : '';

    return `
      <div class="modal" style="display:flex;">
        <div class="modal-content zone-settings-modal">
          <div class="modal-header">
            <h3 class="modal-title">Zone Settings — ${this._esc(zone.name || 'Zone')}</h3>
            <button class="close-btn" onclick="SmartLockManagerPanel.closeZoneSettings()">×</button>
          </div>
          <div class="form-container zs-form">

            <!-- Business Hours -->
            <div class="zs-section">
              <h4 class="zs-section-title">Business Hours</h4>
              <label class="zs-toggle"><input type="checkbox" id="zs_bh_enabled" ${ck(bh.enabled)}> Enabled</label>
              <div class="zs-row">
                <div class="form-group">
                  <label for="zs_bh_open">Open time</label>
                  <input type="time" id="zs_bh_open" value="${this._esc(v(bh.open_time, '08:30'))}">
                </div>
                <div class="form-group">
                  <label for="zs_bh_close">Close time</label>
                  <input type="time" id="zs_bh_close" value="${this._esc(v(bh.close_time, '17:30'))}">
                </div>
              </div>
              <div class="form-group">
                <label>Days</label>
                <div class="zs-days">${this._zsDayChecks('zs_bh_day', bh.days)}</div>
              </div>
              <label class="zs-toggle"><input type="checkbox" id="zs_bh_use_workday" ${ck(bh.use_workday_sensor)}> Use workday sensor</label>
              <div class="form-group">
                <label for="zs_bh_workday_entity">Workday entity</label>
                <input type="text" id="zs_bh_workday_entity" value="${this._esc(v(bh.workday_entity, 'binary_sensor.workday_sensor'))}" placeholder="binary_sensor.workday_sensor">
              </div>
            </div>

            <!-- Scheduled Auto-Lock (COB) -->
            <div class="zs-section">
              <h4 class="zs-section-title">Auto-Lock — Scheduled (COB)</h4>
              <label class="zs-toggle"><input type="checkbox" id="zs_sal_enabled" ${ck(sal.enabled)}> Enabled</label>
              <div class="zs-row">
                <div class="form-group">
                  <label for="zs_sal_time">Time</label>
                  <input type="time" id="zs_sal_time" value="${this._esc(v(sal.time, '17:30'))}">
                </div>
                <div class="form-group">
                  <label for="zs_sal_max_attempts">Max attempts</label>
                  <input type="number" id="zs_sal_max_attempts" min="1" value="${this._esc(v(sal.max_attempts, 3))}">
                </div>
                <div class="form-group">
                  <label for="zs_sal_settle">Settle seconds</label>
                  <input type="number" id="zs_sal_settle" min="0" value="${this._esc(v(sal.settle_seconds, 5))}">
                </div>
              </div>
              <div class="form-group">
                <label>Days</label>
                <div class="zs-days">${this._zsDayChecks('zs_sal_day', sal.days)}</div>
              </div>
              <label class="zs-toggle"><input type="checkbox" id="zs_sal_verify" ${ck(v(sal.verify_boltstatus, true))}> Verify bolt status after lock</label>
            </div>

            <!-- Idle Auto-Lock -->
            <div class="zs-section">
              <h4 class="zs-section-title">Auto-Lock — Idle</h4>
              <label class="zs-toggle"><input type="checkbox" id="zs_idle_enabled" ${ck(idle.enabled)}> Enabled</label>
              <div class="form-group">
                <label for="zs_idle_minutes">Idle minutes</label>
                <input type="number" id="zs_idle_minutes" min="0" value="${this._esc(v(idle.minutes, 5))}">
              </div>
              <label class="zs-toggle"><input type="checkbox" id="zs_idle_sun" ${ck(idle.sun_aware)}> Sun-aware (use day/night minutes)</label>
              <div class="zs-row">
                <div class="form-group">
                  <label for="zs_idle_night">Night minutes</label>
                  <input type="number" id="zs_idle_night" min="0" value="${this._esc(v(idle.night_minutes, 15))}">
                </div>
                <div class="form-group">
                  <label for="zs_idle_day">Day minutes</label>
                  <input type="number" id="zs_idle_day" min="0" value="${this._esc(v(idle.day_minutes, 5))}">
                </div>
              </div>
            </div>

            <!-- Alerts -->
            <div class="zs-section">
              <h4 class="zs-section-title">Alerts</h4>
              <div class="zs-alert-block">
                <label class="zs-toggle"><input type="checkbox" id="zs_al_oh_enabled" ${ck(oh.enabled)}> Outside hours</label>
                <div class="form-group">
                  <label for="zs_al_oh_severity">Severity</label>
                  <input type="text" id="zs_al_oh_severity" value="${this._esc(v(oh.severity, 'CRIT'))}" placeholder="CRIT">
                </div>
              </div>
              <div class="zs-alert-block">
                <label class="zs-toggle"><input type="checkbox" id="zs_al_su_enabled" ${ck(su.enabled)}> Sustained unlock</label>
                <div class="form-group">
                  <label for="zs_al_su_tiers">Escalation tiers (seconds, comma-separated)</label>
                  <input type="text" id="zs_al_su_tiers" value="${this._esc(tiers)}" placeholder="15, 30, 45">
                </div>
              </div>
              <div class="zs-alert-block">
                <label class="zs-toggle"><input type="checkbox" id="zs_al_jam_enabled" ${ck(jam.enabled)}> Jam / lock failure</label>
                <div class="form-group">
                  <label for="zs_al_jam_severity">Severity</label>
                  <input type="text" id="zs_al_jam_severity" value="${this._esc(v(jam.severity, 'CRIT'))}" placeholder="CRIT">
                </div>
              </div>
              <div class="zs-alert-block">
                <label class="zs-toggle"><input type="checkbox" id="zs_al_lb_enabled" ${ck(lb.enabled)}> Low battery</label>
                <div class="form-group">
                  <label for="zs_al_lb_threshold">Threshold (%)</label>
                  <input type="number" id="zs_al_lb_threshold" min="0" max="100" value="${this._esc(v(lb.threshold, 20))}">
                </div>
              </div>
              <div class="zs-alert-block">
                <label class="zs-toggle"><input type="checkbox" id="zs_al_off_enabled" ${ck(off.enabled)}> Offline / unavailable member</label>
              </div>
            </div>

            <!-- Notifications -->
            <div class="zs-section">
              <h4 class="zs-section-title">Notifications</h4>
              <div class="zs-alert-block">
                <label class="zs-toggle"><input type="checkbox" id="zs_email_enabled" ${ck(email.enabled)}> Email</label>
                <div class="form-group">
                  <label for="zs_email_recipients">Recipients override (comma-separated, blank = default routing)</label>
                  <input type="text" id="zs_email_recipients" value="${this._esc(recips)}" placeholder="alerts@example.com, ops@example.com">
                </div>
              </div>
              <div class="zs-alert-block">
                <label class="zs-toggle"><input type="checkbox" id="zs_mobile_enabled" ${ck(mobile.enabled)}> Mobile / persistent notification</label>
                <div class="form-group">
                  <label for="zs_mobile_targets">Targets (comma-separated notify.* services, blank = default)</label>
                  <input type="text" id="zs_mobile_targets" value="${this._esc(targets)}" placeholder="notify.mobile_app_phone, notify.persistent_notification">
                </div>
              </div>
            </div>

            <div class="form-actions">
              <button class="btn secondary" onclick="SmartLockManagerPanel.closeZoneSettings()">Cancel</button>
              <button class="btn" onclick="SmartLockManagerPanel.saveZoneSettings()">Save Settings</button>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  // Resolve a representative member entity_id for a zone. Zone slot services
  // operate on a member lock entity (the edit then propagates to the whole
  // zone). Returns null for an empty zone.
  _zoneTargetEntity(zoneId) {
    const zone = this._zoneById(zoneId);
    const members = zone?.members || [];
    return members.length ? members[0].entity_id : null;
  }

  // Find a zone member object by its lock entity_id across all zones.
  // - Inputs: entityId (str).
  // - Outputs: the member object, or undefined if not found.
  _memberByEntity(entityId) {
    for (const zone of (this._zones || [])) {
      const members = Array.isArray(zone.members) ? zone.members : [];
      const hit = members.find(m => m.entity_id === entityId);
      if (hit) return hit;
    }
    return undefined;
  }

  // --- Header gear menu / per-lock menu / picker toggles (ephemeral UI) ---
  static toggleZoneMenu(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._openZoneMenu = (p._openZoneMenu === zoneId) ? null : zoneId;
    p._openLockMenu = null; p._openPicker = null;
    p.requestUpdate();
  }
  static toggleLockMenu(entityId, ev) {
    const p = window.smartLockManagerPanel; if (!p) return;
    const willOpen = p._openLockMenu !== entityId;
    // Anchor a fixed-position menu to the gear so it escapes the locks-list
    // overflow clip. Capture the gear button's viewport rect now.
    if (willOpen && ev && ev.currentTarget) {
      const r = ev.currentTarget.getBoundingClientRect();
      // Place the menu's top-right just under the gear.
      p._lockMenuPos = { top: Math.round(r.bottom + 4), left: Math.round(r.right - 190) };
    }
    p._openLockMenu = willOpen ? entityId : null;
    p._openZoneMenu = null; p._openPicker = null;
    p.requestUpdate();
  }
  static toggleLockPicker(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._openPicker = (p._openPicker === zoneId) ? null : zoneId;
    p._openZoneMenu = null; p._openLockMenu = null;
    p.requestUpdate();
  }

  // --- Alert-snooze controls (per-zone + global) -------------------------

  // Toggle a zone's snooze-duration popover; mutually exclusive with other menus.
  // - Inputs: zoneId (str).
  // - Outputs: none (re-renders).
  static toggleSnoozeMenu(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._openSnoozeMenu = (p._openSnoozeMenu === zoneId) ? null : zoneId;
    p._openZoneMenu = null; p._openLockMenu = null; p._openPicker = null;
    p._openPauseAllMenu = false;
    p.requestUpdate();
  }

  // Snooze a single zone's alerts for a fixed number of hours.
  // - Inputs: zoneId (str), hours (float 0.25-24).
  // - Outputs: Promise<void>.
  static async snoozeZone(zoneId, hours) {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._openSnoozeMenu = null;
    await p.callZoneService('pause_alerts', { zone_id: zoneId, hours });
  }

  // Snooze a zone until its next open time ("Until morning").
  // - Inputs: zoneId (str).
  // - Outputs: Promise<void>.
  static async snoozeZoneUntilMorning(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    const zone = p._zones.find(z => String(z.zone_id) === String(zoneId));
    if (!zone) return;
    const hours = p._untilMorningHours(zone);
    p._openSnoozeMenu = null;
    await p.callZoneService('pause_alerts', { zone_id: zoneId, hours });
  }

  // Resume a zone's alerts. If the zone is covered only by the GLOBAL pause
  // (no live per-zone snooze), clear the global pause; otherwise clear just
  // this zone.
  // - Inputs: zoneId (str).
  // - Outputs: Promise<void>.
  static async unsnoozeZone(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    const zone = p._zones.find(z => String(z.zone_id) === String(zoneId));
    if (!zone) return;
    const snz = p._zoneSnoozeUntil(zone);
    if (snz && snz.global && !(zone.snoozed_until && new Date(zone.snoozed_until) > new Date())) {
      await p.callZoneService('resume_alerts', {});
    } else {
      await p.callZoneService('resume_alerts', { zone_id: zoneId });
    }
  }

  // Toggle the global "Pause all" duration popover.
  // - Inputs: none.
  // - Outputs: none (re-renders).
  static togglePauseAllMenu() {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._openPauseAllMenu = !p._openPauseAllMenu;
    p._openSnoozeMenu = null; p._openZoneMenu = null;
    p.requestUpdate();
  }

  // Pause alerts for ALL zones (global) for a fixed number of hours.
  // - Inputs: hours (float 0.25-24).
  // - Outputs: Promise<void>.
  static async pauseAll(hours) {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._openPauseAllMenu = false;
    await p.callZoneService('pause_alerts', { hours });
  }

  // Clear the global alert pause.
  // - Inputs: none.
  // - Outputs: Promise<void>.
  static async resumeAll() {
    const p = window.smartLockManagerPanel; if (!p) return;
    await p.callZoneService('resume_alerts', {});
  }

  // --- Zone lifecycle / membership services ---
  static async createZone() {
    const p = window.smartLockManagerPanel; if (!p) return;
    const name = prompt('Name for the new zone:');
    if (name == null) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    await p.callZoneService('create_zone', { name: trimmed });
  }
  static async renameZone(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    const zone = p._zoneById(zoneId);
    p._openZoneMenu = null; p.requestUpdate();
    const name = prompt('New name for this zone:', zone?.name || '');
    if (name == null) return;
    const trimmed = name.trim();
    if (!trimmed || trimmed === zone?.name) return;
    await p.callZoneService('update_zone', { zone_id: zoneId, name: trimmed });
  }
  static async deleteZone(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    const zone = p._zoneById(zoneId);
    if (!confirm(`Delete zone "${zone?.name || zoneId}"? This wipes its codes from all member locks and returns them to the unhomed pool.`)) return;
    await p.callZoneService('delete_zone', { zone_id: zoneId });
  }
  static async applyZoneCodes(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    await p.callZoneService('apply_zone_codes', { zone_id: zoneId });
  }
  static async clearZoneCodes(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    const zone = p._zoneById(zoneId);
    if (!confirm(`Clear all codes from zone "${zone?.name || zoneId}"? This removes every user code from all member locks.`)) return;
    await p.callZoneService('clear_zone_codes', { zone_id: zoneId });
  }
  static async addLockToZone(zoneId, entityId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    await p.callZoneService('add_lock_to_zone', { zone_id: zoneId, lock_entity_id: entityId });
  }
  static async removeLockFromZone(zoneId, entityId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    if (!confirm('Remove this lock from the zone? Its zone codes will be wiped from the hardware and it returns to the unhomed pool.')) return;
    await p.callZoneService('remove_lock_from_zone', { zone_id: zoneId, lock_entity_id: entityId });
  }
  static async refreshZone(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._openZoneMenu = null; p._openLockMenu = null; p._openPicker = null;
    await p.loadZoneData();
    await p.loadLockData(true);
    p.requestUpdate();
  }

  // Open the per-lock Edit modal (friendly-name editor) for a member lock.
  //
  // Replaces the former inline prompt with a native modal consistent with the
  // panel's other modals (Slot Edit / Lock Settings). The Save handler writes
  // SLM's stored settings.friendly_name via update_lock_settings; that name is
  // now authoritative everywhere (api/zones.py _lock_friendly_name prefers the
  // SLM name), so lock rows AND access-log door badges follow it.
  // - Inputs: entityId (str) — the lock entity_id.
  // - Outputs: void.
  static editLock(entityId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._openLockMenu = null;
    p._editLockEntityId = entityId;
    p._editLockModalOpen = true;
    p.requestUpdate();
    // Focus the field once rendered.
    requestAnimationFrame(() => {
      const input = p.shadowRoot?.querySelector('#edit_lock_friendly_name');
      if (input) { input.focus(); input.select(); }
    });
  }

  // Close the per-lock Edit modal without saving.
  // - Outputs: void.
  static closeEditLock() {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._editLockModalOpen = false;
    p._editLockEntityId = null;
    p.requestUpdate();
  }

  // Save the per-lock Edit modal: persist the new SLM friendly name, then close
  // and reload so every lock row and access-log badge reflects the new name.
  // - Outputs: Promise<void>.
  static async saveEditLock() {
    const p = window.smartLockManagerPanel; if (!p) return;
    const entityId = p._editLockEntityId;
    if (!entityId) { SmartLockManagerPanel.closeEditLock(); return; }
    const input = p.shadowRoot?.querySelector('#edit_lock_friendly_name');
    const trimmed = (input?.value || '').trim();
    const current = p._memberByEntity(entityId)?.friendly_name || '';
    // Close first so the modal doesn't block the post-save refresh.
    SmartLockManagerPanel.closeEditLock();
    if (!trimmed || trimmed === current) return;
    // Write SLM's stored friendly name (SLM name wins in the zones API), then
    // callZoneService reloads zone + lock data so cards/badges re-render.
    await p.callZoneService('update_lock_settings', { entity_id: entityId, friendly_name: trimmed });
  }

  // --- Zone Settings modal (Phase 4a) ---
  // Open the Zone Settings modal for a zone. Closes the gear menu first.
  // - Inputs: zoneId (str).
  // - Outputs: void.
  static openZoneSettings(zoneId) {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._openZoneMenu = null;
    p._zoneSettingsZoneId = zoneId;
    p._zoneSettingsModalOpen = true;
    p.requestUpdate();
  }

  // Close the Zone Settings modal without saving.
  // - Outputs: void.
  static closeZoneSettings() {
    const p = window.smartLockManagerPanel; if (!p) return;
    p._zoneSettingsModalOpen = false;
    p._zoneSettingsZoneId = null;
    p.requestUpdate();
  }

  // Scrape the Zone Settings form, build the full settings object, persist it
  // via update_zone_settings, then close + reload zone data. Sending the full
  // object is safe (backend deep-merges per block); empty/partial fields fall
  // back to sane parsed values and never throw.
  // - Outputs: Promise<void>.
  static async saveZoneSettings() {
    const p = window.smartLockManagerPanel; if (!p) return;
    const zoneId = p._zoneSettingsZoneId;
    const root = p.shadowRoot;
    if (!zoneId || !root) { SmartLockManagerPanel.closeZoneSettings(); return; }

    // --- DOM scrape helpers (null-safe) ---
    const bool = (id) => !!root.getElementById(id)?.checked;
    const str = (id) => (root.getElementById(id)?.value ?? '').trim();
    const intOr = (id, dflt) => {
      const raw = str(id);
      if (raw === '') return dflt;
      const n = parseInt(raw, 10);
      return Number.isFinite(n) ? n : dflt;
    };
    const days = (prefix) => {
      const out = [];
      for (let i = 0; i < 7; i++) { if (bool(`${prefix}_${i}`)) out.push(i); }
      return out;
    };
    const intList = (id) => str(id)
      .split(',')
      .map(t => parseInt(t.trim(), 10))
      .filter(n => Number.isFinite(n));
    const strList = (id) => str(id)
      .split(',')
      .map(t => t.trim())
      .filter(t => t !== '');

    const settings = {
      business_hours: {
        enabled: bool('zs_bh_enabled'),
        open_time: str('zs_bh_open') || '08:30',
        close_time: str('zs_bh_close') || '17:30',
        days: days('zs_bh_day'),
        use_workday_sensor: bool('zs_bh_use_workday'),
        workday_entity: str('zs_bh_workday_entity') || 'binary_sensor.workday_sensor',
      },
      scheduled_auto_lock: {
        enabled: bool('zs_sal_enabled'),
        time: str('zs_sal_time') || '17:30',
        days: days('zs_sal_day'),
        max_attempts: intOr('zs_sal_max_attempts', 3),
        settle_seconds: intOr('zs_sal_settle', 5),
        verify_boltstatus: bool('zs_sal_verify'),
      },
      idle_auto_lock: {
        enabled: bool('zs_idle_enabled'),
        minutes: intOr('zs_idle_minutes', 5),
        sun_aware: bool('zs_idle_sun'),
        night_minutes: intOr('zs_idle_night', 15),
        day_minutes: intOr('zs_idle_day', 5),
      },
      alerts: {
        outside_hours: {
          enabled: bool('zs_al_oh_enabled'),
          severity: str('zs_al_oh_severity') || 'CRIT',
        },
        sustained_unlock: {
          enabled: bool('zs_al_su_enabled'),
          tiers: intList('zs_al_su_tiers'),
        },
        jam: {
          enabled: bool('zs_al_jam_enabled'),
          severity: str('zs_al_jam_severity') || 'CRIT',
        },
        low_battery: {
          enabled: bool('zs_al_lb_enabled'),
          threshold: intOr('zs_al_lb_threshold', 20),
        },
        offline: {
          enabled: bool('zs_al_off_enabled'),
        },
      },
      notify: {
        email: {
          enabled: bool('zs_email_enabled'),
          recipients_override: strList('zs_email_recipients'),
        },
        mobile: {
          enabled: bool('zs_mobile_enabled'),
          targets: strList('zs_mobile_targets'),
        },
      },
    };

    // Close first so the modal doesn't block the post-save refresh, then
    // persist + reload. callZoneService reloads zone data on completion.
    SmartLockManagerPanel.closeZoneSettings();
    await p.callZoneService('update_zone_settings', { zone_id: zoneId, settings });
  }

  // --- Zone-level code slot operations (target zone's first member; the edit
  //     propagates to the whole zone server-side) ---
  static openZoneSlot(zoneId, slotNumber) {
    const p = window.smartLockManagerPanel; if (!p) return;
    const entity = p._zoneTargetEntity(zoneId);
    if (!entity) { alert('Add a lock to this zone before configuring codes.'); return; }
    p._currentZoneId = zoneId;
    p._currentLockEntityId = entity;
    p.openSlotModal(slotNumber);
  }
  static toggleZoneSlot(zoneId, slotNumber) {
    const p = window.smartLockManagerPanel; if (!p) return;
    const entity = p._zoneTargetEntity(zoneId);
    if (!entity) return;
    p._currentLockEntityId = entity;
    p.toggleSlot(slotNumber);
  }
  static clearZoneSlot(zoneId, slotNumber) {
    const p = window.smartLockManagerPanel; if (!p) return;
    const entity = p._zoneTargetEntity(zoneId);
    if (!entity) return;
    p._currentLockEntityId = entity;
    p.clearSlot(slotNumber);
  }
  static resetZoneSlotUsage(zoneId, slotNumber) {
    const p = window.smartLockManagerPanel; if (!p) return;
    const entity = p._zoneTargetEntity(zoneId);
    if (!entity) return;
    p._currentLockEntityId = entity;
    p.resetSlotUsage(slotNumber);
  }

}

// Set global reference
window.SmartLockManagerPanel = SmartLockManagerPanel;

// Register the custom element only if not already defined
if (!customElements.get('smart-lock-manager-panel')) {
  customElements.define('smart-lock-manager-panel', SmartLockManagerPanel);
}

} // End of redefinition guard

// Export for Home Assistant
window.customCards = window.customCards || [];
window.customCards.push(CUSTOM_CARD_META);
