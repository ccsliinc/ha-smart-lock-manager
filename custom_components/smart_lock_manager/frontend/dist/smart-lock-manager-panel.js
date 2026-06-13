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
    this._observeOnly = false;  // True under SLM_DEV_MOCK (gates Dev Alerts UI)
    this._devAlerts = [];       // OBSERVE-ONLY recorded dev alerts
    this._zoneDataLoaded = false;
    this._openZoneMenu = null;  // zone_id whose header gear menu is open
    this._openLockMenu = null;  // entity_id whose per-lock gear menu is open
    this._openPicker = null;    // zone_id whose "+" unhomed picker is open
    this._editLockModalOpen = false; // per-lock Edit (friendly name) modal
    this._editLockEntityId = null;   // entity_id being edited in that modal
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
    if (!this._modalOpen && !this._settingsModalOpen && !this._editLockModalOpen) {
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
      if (!this._modalOpen && !this._settingsModalOpen && !this._editLockModalOpen) {
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
      // OBSERVE-ONLY dev alert log. ``observe_only`` is true only under
      // SLM_DEV_MOCK; the Dev Alerts section renders only then.
      this._observeOnly = !!payload?.observe_only;
      this._devAlerts = Array.isArray(payload?.dev_alerts) ? payload.dev_alerts : [];
      this._zoneDataLoaded = true;
      if (!this._modalOpen && !this._settingsModalOpen && !this._editLockModalOpen) {
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

    // Cache the Z-Wave codes for sync validation
    this._zwaveCodeCache[entity_id] = codes;

    // Z-Wave codes cached

    // Trigger a re-render to update slot colors
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

  getSlotDisplayInfo(lock, slot) {
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

  // Build the icon + meta for a single access-log action/source.
  getAccessLogPresentation(entry) {
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

  // Format an ISO timestamp into a local, human-friendly string.
  formatAccessLogTime(iso) {
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
      if (pinCodeField) pinCodeField.value = slotDetails.pin_code || '';
      if (maxUsesField) maxUsesField.value = slotDetails.max_uses || -1;
      if (isActiveField) isActiveField.checked = slotDetails.pin_code ? true : true; // Default to enabled for new slots
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
      try {

        // Save the entity ID before closing modal (as closeSettingsModal clears it)
        const entityIdForSpinner = this._currentLockEntityId;

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

    // IMMEDIATE VISUAL FEEDBACK - Update slot name immediately
    if (userName) {
      this.updateSlotDisplayImmediate(this._editingSlot, userName, isActive);
    }

    // Close modal immediately and show card spinner
    this.closeModal();
    this.showCardSpinner(entityIdForSpinner, 'Saving...');

    try {
      if (!isActive && pinCode) {
        // User unchecked the enable box but has PIN code - disable the slot (don't delete)
        await this.callService('disable_slot', {
          entity_id: entityIdForSpinner,
          code_slot: slotNumber
        });
        return;
      }

      if (!pinCode) {
        // No PIN code - clear the slot completely
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

    // Determine whether the PIN actually changed. If the user only edited the
    // username/metadata (PIN unchanged from what is already stored), we must
    // NOT re-write the code to the physical Z-Wave lock — that needless
    // set_lock_usercode can surface a transient Kwikset error. We only chain
    // sync_slot_to_zwave when the PIN genuinely changed.
    const currentLock = this._locks?.find(l => l.attributes.lock_entity_id === entityIdForSpinner);
    const existingPin = currentLock?.attributes?.slot_details?.[`slot_${slotNumber}`]?.pin_code || '';
    const pinUnchanged = existingPin && pinCode && existingPin === pinCode;

    await this.callService('set_code_advanced', serviceData);

    // Automatically sync the code to the physical Z-Wave lock ONLY when the
    // PIN changed. Metadata-only edits skip the physical re-write entirely.
    if (!pinUnchanged && serviceData.usercode && serviceData.usercode.length >= 4) {
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
  _esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Map a zone code-slot (from the zones API) to display color/status,
  // mirroring the backend color hierarchy: grey disabled -> green active.
  // - Inputs: slot (zone code_slot object).
  // - Outputs: { color, title, status }.
  _zoneSlotDisplay(slot) {
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
            <ha-icon icon="${chevron}" style="width:18px;height:18px;"></ha-icon>
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
          <ha-icon icon="${chevron}" style="width:18px;height:18px;"></ha-icon>
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
          <ha-icon icon="${chevron}" style="width:18px;height:18px;"></ha-icon>
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
  getDevAlertPresentation(alert) {
    if (alert.is_recovery) {
      return { icon: 'mdi:check-circle', color: '#4a7c2a', label: 'Recovery' };
    }
    const sev = (alert.severity || '').toUpperCase();
    if (sev === 'CRIT') {
      return { icon: 'mdi:alert-octagon', color: '#cc3333', label: 'Critical' };
    }
    return { icon: 'mdi:alert', color: '#f0a020', label: 'Warning' };
  }

  // Render a zone's OBSERVE-ONLY "Dev Alerts" section: a collapsible header
  // over uniform single-line rows (severity marker / door / type / message /
  // time), recovery rows styled distinctly. Only shown under SLM_DEV_MOCK
  // (the API only carries dev_alerts then). Clearly labeled observe-only —
  // NO notifications are sent by the engine that records these.
  // - Inputs: zone (zone obj with dev_alerts[]).
  // - Outputs: HTML string ('' when not in observe-only mode).
  renderZoneDevAlerts(zone) {
    if (!this._observeOnly) return '';
    const zid = this._esc(zone.zone_id);
    const alerts = Array.isArray(zone.dev_alerts) ? zone.dev_alerts : [];

    const rows = alerts.map(alert => {
      const p = this.getDevAlertPresentation(alert);
      const door = this._esc(alert.door_name || alert.member_entity_id || '');
      const type = this._esc((alert.alert_type || '').replace(/_/g, ' '));
      const msg = this._esc(alert.message || '');
      const ts = this._esc(this.formatAccessLogTime(alert.timestamp));
      const tooltip = [p.label, door, type, msg, ts].filter(Boolean).join(' - ');
      const recoveryCls = alert.is_recovery ? ' dev-alert-recovery' : '';
      return `
        <div class="access-log-row dev-alert-row${recoveryCls}" title="${tooltip}">
          <ha-icon class="al-icon" icon="${p.icon}" style="width:18px;height:18px;color:${p.color};flex-shrink:0;"></ha-icon>
          <span class="al-lock-badge" title="${door}">${door}</span>
          <span class="al-attr-text">${type ? `[${type}] ` : ''}${msg}</span>
          <span class="al-time">${ts}</span>
        </div>
      `;
    }).join('');

    // Collapsed by default (secondary diagnostic info).
    const expanded = this._isSectionExpanded(zone.zone_id, 'alerts', false);
    const chevron = expanded ? 'mdi:chevron-down' : 'mdi:chevron-up';

    return `
      <div class="access-log-section dev-alerts-section">
        <div class="access-log-header" onclick="SmartLockManagerPanel.toggleSection('${zid}', 'alerts')">
          <span>Dev Alerts${alerts.length ? ` (${alerts.length})` : ''}</span>
          <ha-icon icon="${chevron}" style="width:18px;height:18px;"></ha-icon>
        </div>
        <div class="dev-alerts-banner" style="${expanded ? '' : 'display:none;'}">
          Dev / observe-only — no notifications sent
        </div>
        <div class="access-log-body" style="${expanded ? '' : 'display:none;'}">
          ${alerts.length ? rows : '<div class="access-log-empty">No dev alerts recorded yet.</div>'}
        </div>
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

    return `
      <div class="lock-card zone-card">
        <div class="lock-header zone-header">
          <div class="zone-title-wrap">
            <h3 class="lock-title" title="${name}">${name}</h3>
            <div class="saving-spinner" id="saving-spinner-zone-${zid}" style="display:none;">
              <div class="spinner"></div><span>Saving...</span>
            </div>
          </div>
          <div class="lock-header-right">
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
      <style>
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

        /* OBSERVE-ONLY Dev Alerts section. */
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
          display: ${this._modalOpen ? 'flex' : 'none'};
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
      </style>

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
          <button class="refresh-btn"
                  onclick="SmartLockManagerPanel.forceRefresh()"
                  title="Refresh all data from Home Assistant"
                  style="background: none; border: none; cursor: pointer; padding: 4px; border-radius: 4px; display: flex; align-items: center; color: var(--primary-text-color); opacity: 0.7; transition: opacity 0.2s;">
            <ha-icon icon="mdi:sync" style="margin-right: 3px; width: 25px; height: 25px;"></ha-icon>
            <span style="height: 20px; line-height: 20px; display: flex; align-items: center; margin-left: 6px;">Refresh All</span>
          </button>
        </div>
      </div>


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
window.customCards.push({
  type: 'smart-lock-manager-panel',
  name: 'Smart Lock Manager Panel',
  description: 'Advanced panel for managing smart locks with scheduling and usage tracking'
});
