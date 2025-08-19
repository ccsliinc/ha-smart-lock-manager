// Smart Lock Manager Advanced Panel v2.0.2
// Enhanced panel with slot management grid, advanced code management, and usage analytics

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
    }

    // Don't auto-refresh if modal is open to prevent losing user input
    if (!this._modalOpen && !this._settingsModalOpen) {
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
      if (!this._modalOpen && !this._settingsModalOpen) {
        this.requestUpdate();
      }
    } catch (error) {
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

    // Initialize lock type fields after modal renders
    setTimeout(() => {
      this.initializeLockTypeFields();

      // Initialize friendly name input
      const friendlyNameInput = this.shadowRoot.querySelector('#friendly_name');
      if (friendlyNameInput) {
        // Input found and will be initialized by initializeLockTypeFields
      }
    }, 100);
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

  getParentLockOptions() {
    if (!this._locks) return '';

    // Filter out the current lock and only show main/parent locks
    const parentLocks = this._locks.filter(lock => {
      const attributes = lock.attributes || {};
      // Don't include the current lock being edited
      if (attributes.lock_entity_id === this._currentLockEntityId) {
        return false;
      }
      // Only include main locks (not child locks)
      return attributes.is_main_lock !== false;
    });

    return parentLocks.map(lock => {
      const attributes = lock.attributes || {};
      const lockName = attributes.friendly_name || attributes.lock_name || attributes.lock_entity_id || lock.entity_id;
      const entityId = attributes.lock_entity_id || lock.entity_id;
      return `<option value="${entityId}">${lockName}</option>`;
    }).join('');
  }

  getChildLocks(parentLockEntityId) {
    if (!this._locks) return [];

    return this._locks.filter(lock => {
      const attributes = lock.attributes || {};
      return attributes.parent_lock_id === parentLockEntityId;
    });
  }

  renderChildLocksSection(parentLockEntityId) {
    const childLocks = this.getChildLocks(parentLockEntityId);

    if (childLocks.length === 0) {
      return '';
    }

    return `
      <div class="child-locks-section">
        <div class="child-locks-header">
          <h4>Child Locks</h4>
        </div>
        <div class="child-locks-list">
          ${childLocks.map(childLock => {
            const attributes = childLock.attributes || {};
            const childEntityId = attributes.lock_entity_id || childLock.entity_id;
            // Use friendly name first, then custom_friendly_name, then lock_name
            const childName = attributes.custom_friendly_name || attributes.lock_name || 'Child Lock';
            const isConnected = attributes.is_connected !== false;
            const syncStatus = this.getChildSyncStatus(childLock, parentLockEntityId);

            return `
              <div class="child-lock-item">
                <div class="child-lock-info">
                  <div class="status-light ${syncStatus.color}" title="${syncStatus.message}"></div>
                  <span class="child-lock-name">${childName}</span>
                </div>
                <div class="child-lock-actions">
                  <button class="child-remove-btn" onclick="window.smartLockManagerPanel.callService('remove_child_lock', {entity_id: '${childEntityId}'})" title="Remove Child Lock">
                    <ha-icon icon="mdi:delete-outline"></ha-icon>
                  </button>
                  <button class="child-settings-btn" onclick="SmartLockManagerPanel.openSettings('${childEntityId}')" title="Child Lock Settings">
                    <ha-icon icon="mdi:cog"></ha-icon>
                  </button>
                </div>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  }

  getChildSyncStatus(childLock, parentLockEntityId) {
    const attributes = childLock.attributes || {};
    const isConnected = attributes.is_connected !== false;

    if (!isConnected) {
      return { color: 'red', message: 'Child lock offline' };
    }

    // Get parent lock for comparison
    const parentLock = this._locks?.find(l =>
      (l.attributes?.lock_entity_id || l.entity_id) === parentLockEntityId
    );

    if (!parentLock) {
      return { color: 'red', message: 'Parent lock not found' };
    }

    // Get slot details from both locks
    const childSlots = attributes.slot_details || {};
    const parentSlots = parentLock.attributes?.slot_details || {};

    const totalSlots = parentLock.attributes?.total_slots || 10;
    const startFrom = parentLock.attributes?.start_from || 1;

    let syncedCount = 0;
    let errorCount = 0;
    let syncingCount = 0;
    let missingFromChild = 0;

    // Check each slot for sync status
    for (let i = 0; i < totalSlots; i++) {
      const slotNumber = startFrom + i;
      const slotKey = `slot_${slotNumber}`;

      const parentSlot = parentSlots[slotKey];
      const childSlot = childSlots[slotKey];

      // If parent slot is empty, child should be empty too
      if (!parentSlot || !parentSlot.is_active) {
        if (!childSlot || !childSlot.is_active) {
          syncedCount++; // Both empty = synced
        } else {
          errorCount++; // Child has data but parent doesn't = error
        }
        continue;
      }

      // Parent slot is active, check child
      if (!childSlot || !childSlot.is_active) {
        // Parent has active slot but child doesn't - this indicates syncing needed
        missingFromChild++;
        continue;
      }

      // Compare slot data
      const parentPin = parentSlot.usercode;
      const childPin = childSlot.usercode;
      const parentUser = parentSlot.user_name;
      const childUser = childSlot.user_name;
      const parentActive = parentSlot.is_active;
      const childActive = childSlot.is_active;

      if (parentPin === childPin &&
          parentUser === childUser &&
          parentActive === childActive) {
        syncedCount++; // Perfect match = synced
      } else if (childSlot.is_syncing ||
                 parentSlot.sync_status === 'syncing' ||
                 childSlot.slot_status === 'Synchronizing' ||
                 childSlot.status?.name === 'SYNCHRONIZING' ||
                 parentSlot.slot_status === 'Synchronizing' ||
                 parentSlot.status?.name === 'SYNCHRONIZING') {
        syncingCount++; // Currently syncing
      } else {
        errorCount++; // Data mismatch = error
      }
    }

    // Determine overall status - treat missing slots as syncing if recently updated
    const childLastUpdate = new Date(attributes.last_updated || attributes.last_update || 0);
    const parentLastUpdate = new Date(parentLock.attributes?.last_updated || parentLock.attributes?.last_update || 0);
    const now = new Date();
    const childTimeSinceUpdate = now - childLastUpdate;
    const parentTimeSinceUpdate = now - parentLastUpdate;
    const recentlyUpdated = childTimeSinceUpdate < 120000 || parentTimeSinceUpdate < 120000; // Within last 2 minutes

    if (missingFromChild > 0 && recentlyUpdated) {
      // Parent was recently updated and child is missing slots - likely syncing
      return {
        color: 'yellow',
        message: `${missingFromChild} slot${missingFromChild > 1 ? 's' : ''} syncing to child...`
      };
    } else if (syncingCount > 0) {
      return {
        color: 'yellow',
        message: `${syncingCount} slot${syncingCount > 1 ? 's' : ''} syncing...`
      };
    } else if (missingFromChild > 0 && errorCount === 0) {
      // Only missing slots, no errors - treat as syncing (more optimistic)
      return {
        color: 'yellow',
        message: `${missingFromChild} slot${missingFromChild > 1 ? 's' : ''} syncing to child...`
      };
    } else if (errorCount > 0 || missingFromChild > 0) {
      const totalIssues = errorCount + missingFromChild;
      return {
        color: 'red',
        message: `${totalIssues} slot${totalIssues > 1 ? 's' : ''} out of sync`
      };
    } else {
      return {
        color: 'green',
        message: `All ${syncedCount} slots synchronized`
      };
    }
  }

  initializeLockTypeFields() {
    const currentLock = this._locks?.find(l => l.attributes.lock_entity_id === this._currentLockEntityId);
    if (!currentLock) return;

    const attributes = currentLock.attributes || {};
    const isMainLockSelect = this.shadowRoot.querySelector('#is_main_lock');
    const parentLockSelect = this.shadowRoot.querySelector('#parent_lock_id');

    if (isMainLockSelect) {
      // Set current lock type
      isMainLockSelect.value = (attributes.is_main_lock !== false) ? 'true' : 'false';

      // Set parent lock if this is a child lock
      if (parentLockSelect && attributes.parent_lock_id) {
        parentLockSelect.value = attributes.parent_lock_id;
      }

      // Initialize field visibility
      SmartLockManagerPanel.toggleLockTypeFields();
    }
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

    // Check parent/child lock settings
    const isMainLock = formData.get('is_main_lock') === 'true';
    const parentLockId = formData.get('parent_lock_id') || null;

    const currentIsMainLock = currentLock?.attributes?.is_main_lock !== false;
    const currentParentLockId = currentLock?.attributes?.parent_lock_id || null;

    if (isMainLock !== currentIsMainLock) {
      updateData.is_main_lock = isMainLock;
      hasUpdates = true;
    }

    if (parentLockId !== currentParentLockId) {
      updateData.parent_lock_id = parentLockId;
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

    await this.callService('set_code_advanced', serviceData);

    // Automatically sync the code to the physical Z-Wave lock
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


  render() {
    if (!this.shadowRoot) return;

    const locks = this._locks || [];

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

        /* Child Locks Section */
        .child-locks-section {
          margin: 16px 0;
          padding: 12px;
          background: var(--primary-background-color);
          border-radius: 8px;
          border: 1px solid var(--divider-color);
        }

        .child-locks-header h4 {
          margin: 0 0 12px 0;
          font-size: 14px;
          font-weight: 500;
          color: var(--primary-text-color);
        }

        .child-locks-list {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .child-lock-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 8px 12px;
          background: var(--card-background-color);
          border-radius: 6px;
          border: 1px solid var(--divider-color);
        }

        .child-lock-info {
          display: flex;
          align-items: center;
          gap: 12px;
          flex: 1;
        }

        .child-lock-name {
          font-size: 13px;
          font-weight: 500;
          color: var(--primary-text-color);
        }

        .status-light {
          width: 10px;
          height: 10px;
          border-radius: 50%;
          margin-right: 8px;
          flex-shrink: 0;
        }

        .status-light.green {
          background: var(--success-color, #4caf50);
        }

        .status-light.red {
          background: var(--error-color, #f44336);
        }

        .status-light.yellow {
          background: var(--warning-color, #ff9800);
        }

        .child-settings-btn {
          background: none;
          border: none;
          cursor: pointer;
          padding: 4px;
          border-radius: 4px;
          color: var(--secondary-text-color);
          transition: all 0.2s;
        }

        .child-settings-btn:hover {
          background: var(--primary-background-color);
          color: var(--primary-color);
        }

        .child-settings-btn ha-icon {
          font-size: 14px;
        }

        .child-lock-actions {
          display: flex;
          gap: 4px;
          align-items: center;
        }

        .child-remove-btn {
          background: none;
          border: none;
          cursor: pointer;
          padding: 4px;
          border-radius: 4px;
          color: var(--error-color, #f44336);
          transition: all 0.2s;
        }

        .child-remove-btn:hover {
          background: var(--error-color, #f44336);
          color: white;
        }

        .child-remove-btn ha-icon {
          font-size: 14px;
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
          <button class="refresh-btn"
                  onclick="SmartLockManagerPanel.forceRefresh()"
                  title="Refresh all lock data from Home Assistant"
                  style="background: none; border: none; cursor: pointer; padding: 4px; border-radius: 4px; display: flex; align-items: center; color: var(--primary-text-color); opacity: 0.7; transition: opacity 0.2s;">
            <ha-icon icon="mdi:sync" style="margin-right: 3px; width: 25px; height: 25px;"></ha-icon>
            <span style="height: 20px; line-height: 20px; display: flex; align-items: center; margin-left: 6px;">Refresh All</span>
          </button>
        </div>
      </div>


      ${locks.length === 0 ? `
        <div class="no-locks">
          <ha-icon icon="mdi:lock-outline"></ha-icon>
          <p>No Smart Lock Manager integrations found</p>
          <p>Add a Smart Lock Manager integration to get started</p>
        </div>
      ` : `
        <div class="locks-grid">
          ${locks.filter(lock => {
            const attributes = lock.attributes || {};
            // Only show parent locks (not child locks) in main view
            return attributes.is_main_lock !== false;
          }).sort((a, b) => {
            // Sort alphabetically by friendly name (or lock name as fallback)
            const nameA = (a.attributes?.friendly_name || a.attributes?.lock_name || '').toLowerCase();
            const nameB = (b.attributes?.friendly_name || b.attributes?.lock_name || '').toLowerCase();
            return nameA.localeCompare(nameB);
          }).map(lock => {
            const attributes = lock.attributes || {};
            const lockEntityId = attributes.lock_entity_id || lock.entity_id;
            const totalSlots = attributes.total_slots || 10;
            const startFrom = attributes.start_from || 1;


            // Skip this lock if no valid entity ID
            if (!lockEntityId) {
              return '';
            }

            // Get the actual lock entity state for the lock/unlock button
            const actualLockEntity = this._hass.states[lockEntityId];
            const actualLockState = actualLockEntity?.state || 'unknown';

            return `
              <div class="lock-card">
                <div class="lock-header">
                  <div class="lock-title-section">
                    <div class="lock-status-column">
                      <div class="lock-connection-status" title="${lockEntityId in this._hass.states ? 'Connected' : 'Disconnected'}">
                        <ha-icon icon="mdi:${lockEntityId in this._hass.states ? 'link' : 'link-off'}" style="width: 16px; height: 16px; color: ${lockEntityId in this._hass.states ? '#4caf50' : '#f44336'};"></ha-icon>
                      </div>
                    </div>
                    <div class="lock-header-column">
                      <div class="lock-title-row">
                        <h3 class="lock-title">${attributes.friendly_name || attributes.lock_name || 'Smart Lock'}</h3>
                        <div class="saving-spinner" id="saving-spinner-${lockEntityId.replace(/\./g, '_')}" style="display: none;">
                          <div class="spinner"></div>
                          <span>Saving...</span>
                        </div>
                      </div>
                      <div class="lock-entity-name" style="font-size: 11px; color: var(--secondary-text-color); font-weight: 300; margin-top: 2px; opacity: 0.7;">Entity: ${lockEntityId}</div>
                    </div>
                  </div>
                  <div class="lock-header-right">
                    <div class="header-buttons" style="display: flex; gap: 6px; align-items: center;">
                      <button class="lock-toggle-btn"
                              onclick="SmartLockManagerPanel.toggleLock('${lockEntityId}', '${actualLockState}')"
                              title="${actualLockState === 'locked' ? 'Click to unlock' : 'Click to lock'}"
                              style="background: none; border: none; cursor: pointer; padding: 4px; border-radius: 4px; display: flex; align-items: center; color: ${actualLockState === 'locked' ? '#4a7c2a' : '#cc3333'};">
                        <ha-icon icon="mdi:${actualLockState === 'locked' ? 'lock' : 'lock-open'}" style="width: 25px; height: 25px;"></ha-icon>
                      </button>
                      <button class="clear-all-btn"
                              onclick="SmartLockManagerPanel.clearAllSlots('${lockEntityId}')"
                              title="Clear all slots"
                              style="background: none; border: none; cursor: pointer; padding: 4px; border-radius: 4px; display: flex; align-items: center; color: #daa520;">
                        <ha-icon icon="mdi:broom" style="width: 25px; height: 25px;"></ha-icon>
                      </button>
                      <button class="settings-btn"
                              onclick="SmartLockManagerPanel.openSettings('${lockEntityId}')"
                              title="Lock settings"
                              style="background: none; border: none; cursor: pointer; padding: 4px; border-radius: 4px; display: flex; align-items: center; color: #708090;">
                        <ha-icon icon="mdi:cog" style="width: 25px; height: 25px;"></ha-icon>
                      </button>
                    </div>
                  </div>
                </div>

                <div class="lock-stats">
                  <div class="stat-item">
                    <div class="stat-value">${attributes.valid_codes_count || 0}</div>
                    <div class="stat-label">Active</div>
                  </div>
                  <div class="stat-item">
                    <div class="stat-value">${attributes.configured_codes_count || 0}</div>
                    <div class="stat-label">Configured</div>
                  </div>
                  <div class="stat-item">
                    <div class="stat-value">${totalSlots}</div>
                    <div class="stat-label">Slots</div>
                  </div>
                </div>

                ${this.renderChildLocksSection(lockEntityId)}

                <div class="slots-container">
                  ${Array.from({length: totalSlots}, (_, i) => {
                    const slotNumber = startFrom + i;
                    const info = this.getSlotDisplayInfo(lock, slotNumber);
                    const details = attributes.slot_details?.[`slot_${slotNumber}`];

                    return `
                      <div class="slot-row" onclick="SmartLockManagerPanel.openSlot('${lockEntityId}', ${slotNumber})">
                        <div class="slot-indicator" style="background-color: ${info.color};"></div>
                        <div class="slot-info">
                          <div class="slot-name">${info.title}</div>
                          <div class="slot-details">${info.status}</div>
                        </div>
                        ${details && details.pin_code ? `
                          <div class="slot-actions">
                            <button class="slot-action-btn toggle-btn ${details.is_active ? 'active' : 'inactive'}"
                                    onclick="event.stopPropagation(); SmartLockManagerPanel.toggleSlot('${lockEntityId}', ${slotNumber})"
                                    title="${details.is_active ? 'Disable this slot' : 'Enable this slot'}">
                              ${details.is_active ? '⏸' : '▶'}
                            </button>
                            <button class="slot-action-btn clear-btn" onclick="event.stopPropagation(); SmartLockManagerPanel.clearSlot('${lockEntityId}', ${slotNumber})" title="Clear slot and remove from lock">🗙</button>
                            ${details.use_count > 0 ? `<button class="slot-action-btn reset-btn" onclick="event.stopPropagation(); SmartLockManagerPanel.resetUsage('${lockEntityId}', ${slotNumber})" title="Reset Usage">🔄</button>` : ''}
                          </div>
                        ` : ''}
                      </div>
                    `;
                  }).join('')}
                </div>

                <div class="lock-actions">
                  <button class="btn" onclick="SmartLockManagerPanel.refreshLock('${lockEntityId}')" title="Refresh this lock's status and sync with Z-Wave">
                    Refresh
                  </button>
                </div>
              </div>
            `;
          }).join('')}
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

            <!-- LOCK TYPE -->
            <div class="form-group lock-type-section">
              <label for="is_main_lock">Lock Type</label>
              <select id="is_main_lock" name="is_main_lock" onchange="SmartLockManagerPanel.toggleLockTypeFields()">
                <option value="true">Parent Lock</option>
                <option value="false">Child Lock</option>
              </select>
              <small>Parent locks manage codes; child locks inherit from parents</small>
            </div>

            <!-- PARENT LOCK SELECTION -->
            <div class="form-group parent-lock-section" style="display: none;">
              <label for="parent_lock_id">Parent Lock</label>
              <select id="parent_lock_id" name="parent_lock_id">
                <option value="">Select Parent Lock</option>
                ${this.getParentLockOptions()}
              </select>
              <small>This child lock will inherit codes from the selected parent</small>
            </div>

            <!-- MAIN LOCK SETTINGS (HIDDEN FOR CHILD LOCKS) -->
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

    `;
  }



  // Debug Interface Methods
  toggleDebugMode() {
    this._debugMode = !this._debugMode;
    this.requestUpdate();
  }

  static toggleDebugMode() {
    const panel = window.smartLockManagerPanel;
    if (panel) {
      panel.toggleDebugMode();
    }
  }

  renderDebugInterface() {
    if (!this._locks || this._locks.length === 0) {
      return `<div class="debug-panel">
        <div class="debug-header">
          <ha-icon icon="mdi:bug"></ha-icon>
          <h3>Debug Mode - No locks found</h3>
        </div>
      </div>`;
    }

    return `
      <div class="debug-panel">
        <div class="debug-header">
          <ha-icon icon="mdi:bug"></ha-icon>
          <h3>Smart Lock Manager Debug Interface</h3>
        </div>
        ${this._locks.map(lock => this.renderLockDebugInfo(lock)).join('')}
      </div>
    `;
  }

  renderLockDebugInfo(lock) {
    const attributes = lock.attributes || {};
    const lockEntityId = attributes.lock_entity_id || lock.entity_id;
    const lockName = attributes.friendly_name || attributes.lock_name || lockEntityId;
    const slotDetails = attributes.slot_details || {};
    const totalSlots = attributes.total_slots || 10;
    const isMainLock = attributes.is_main_lock !== false;
    const parentLockId = attributes.parent_lock_id;
    const childLockIds = attributes.child_lock_ids || [];

    // Get parent-child relationship info
    let parentLock = null;
    let childLocks = [];
    if (parentLockId) {
      parentLock = this._locks.find(l => (l.attributes?.lock_entity_id || l.entity_id) === parentLockId);
    }
    if (childLockIds.length > 0) {
      childLocks = this._locks.filter(l => childLockIds.includes(l.attributes?.lock_entity_id || l.entity_id));
    }

    // Analyze sync status if this is a child lock
    let syncAnalysis = '';
    if (parentLock) {
      const syncStatus = this.getChildSyncStatus(lock, parentLockId);
      const parentSlots = parentLock.attributes?.slot_details || {};

      syncAnalysis = `
        <div class="debug-section">
          <h4>🔄 Child Lock Sync Analysis</h4>
          <div class="debug-status ${syncStatus.color}">
            Status: ${syncStatus.message}
          </div>
          <div class="debug-info">Parent: ${parentLock.attributes?.friendly_name || parentLock.attributes?.lock_name || parentLockId}
Parent Slots: ${Object.keys(parentSlots).filter(key => parentSlots[key]?.is_active).length} active
Child Slots: ${Object.keys(slotDetails).filter(key => slotDetails[key]?.is_active).length} active

Slot Comparison:
${Array.from({length: totalSlots}, (_, i) => {
  const slotNum = i + 1;
  const slotKey = `slot_${slotNum}`;
  const parentSlot = parentSlots[slotKey];
  const childSlot = slotDetails[slotKey];

  const parentActive = parentSlot?.is_active ? '✓' : '✗';
  const childActive = childSlot?.is_active ? '✓' : '✗';
  const parentUser = parentSlot?.user_name || 'Empty';
  const childUser = childSlot?.user_name || 'Empty';
  const match = (!parentSlot?.is_active && !childSlot?.is_active) ||
               (parentSlot?.usercode === childSlot?.usercode &&
                parentSlot?.user_name === childSlot?.user_name &&
                parentSlot?.is_active === childSlot?.is_active) ? '✓' : '✗';

  return `Slot ${slotNum}: P[${parentActive} ${parentUser}] C[${childActive} ${childUser}] Match[${match}]`;
}).join('\n')}</div>
          <div class="debug-controls">
            <button class="debug-btn-small" onclick="SmartLockManagerPanel.debugSyncSlot('${lockEntityId}', '${parentLockId}', 2)">Force Sync Slot 2</button>
            <button class="debug-btn-small" onclick="SmartLockManagerPanel.debugSyncAllSlots('${lockEntityId}', '${parentLockId}')">Sync All Slots</button>
            <button class="debug-btn-small danger" onclick="SmartLockManagerPanel.debugClearChildSlot('${lockEntityId}', 2)">Clear Child Slot 2</button>
          </div>
        </div>
      `;
    }

    return `
      <div class="debug-section">
        <h4>🔐 ${lockName} (${isMainLock ? 'Parent' : 'Child'} Lock)</h4>
        <div class="debug-info">Entity ID: ${lockEntityId}
Type: ${isMainLock ? 'Parent Lock' : 'Child Lock'}
Total Slots: ${totalSlots}
Active Slots: ${Object.keys(slotDetails).filter(key => slotDetails[key]?.is_active).length}
Connected: ${attributes.is_connected !== false ? 'Yes' : 'No'}
Last Update: ${attributes.last_update ? new Date(attributes.last_update).toLocaleString() : 'Unknown'}
${parentLock ? `Parent: ${parentLock.attributes?.friendly_name || parentLock.attributes?.lock_name}` : ''}
${childLocks.length > 0 ? `Children: ${childLocks.map(c => c.attributes?.friendly_name || c.attributes?.lock_name).join(', ')}` : ''}

Active Slots Detail:
${Object.keys(slotDetails)
  .filter(key => slotDetails[key]?.is_active)
  .sort((a, b) => {
    const numA = parseInt(a.replace('slot_', ''));
    const numB = parseInt(b.replace('slot_', ''));
    return numA - numB;
  })
  .map(key => {
    const slot = slotDetails[key];
    const slotNum = key.replace('slot_', '');
    return `Slot ${slotNum}: ${slot.user_name} (PIN: ${slot.usercode ? '***' : 'None'})`;
  })
  .join('\n') || 'No active slots'}</div>
        <div class="debug-controls">
          <button class="debug-btn-small" onclick="SmartLockManagerPanel.debugAddSlot('${lockEntityId}', 'Test User')">Add Test Slot</button>
          <button class="debug-btn-small" onclick="SmartLockManagerPanel.debugToggleSlot('${lockEntityId}', 1)">Toggle Slot 1</button>
          <button class="debug-btn-small danger" onclick="SmartLockManagerPanel.debugClearSlot('${lockEntityId}', 2)">Clear Slot 2</button>
          <button class="debug-btn-small" onclick="SmartLockManagerPanel.forceRefresh()">Refresh Data</button>
        </div>
      </div>
      ${syncAnalysis}
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

  static syncChildLocks(lockEntityId) {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }
    panel.callService('sync_child_locks', {
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

  static toggleLockTypeFields() {
    const panel = window.smartLockManagerPanel;
    if (!panel) {
      return;
    }

    const isMainLockSelect = panel.shadowRoot.querySelector('#is_main_lock');
    const parentLockSelect = panel.shadowRoot.querySelector('#parent_lock_id');
    const parentLockSection = panel.shadowRoot.querySelector('.parent-lock-section');
    const mainLockSettings = panel.shadowRoot.querySelector('.main-lock-settings');

    if (!isMainLockSelect || !parentLockSection || !mainLockSettings) {
      return;
    }

    // Check if this lock is currently a child lock
    const currentLock = panel._locks?.find(l => l.attributes.lock_entity_id === panel._currentLockEntityId);
    const isCurrentlyChildLock = currentLock?.attributes?.parent_lock_id;

    if (isCurrentlyChildLock) {
      // Lock is already a child - disable both dropdowns and show informational message
      isMainLockSelect.disabled = true;
      if (parentLockSelect) {
        parentLockSelect.disabled = true;
      }
      parentLockSection.style.display = 'block';
      mainLockSettings.style.display = 'none';

      // Add or update info message
      let infoMessage = parentLockSection.querySelector('.child-lock-info-message');
      if (!infoMessage) {
        infoMessage = document.createElement('small');
        infoMessage.className = 'child-lock-info-message';
        infoMessage.style.cssText = 'color: var(--warning-color); font-style: italic; display: block; margin-top: 8px;';
        parentLockSection.appendChild(infoMessage);
      }
      infoMessage.textContent = 'This lock is currently a child lock. Use the "Unlink" button on the main panel to change its status.';
    } else {
      // Lock is not currently a child - enable dropdowns and use normal logic
      isMainLockSelect.disabled = false;
      if (parentLockSelect) {
        parentLockSelect.disabled = false;
      }

      // Remove info message if it exists
      const infoMessage = parentLockSection.querySelector('.child-lock-info-message');
      if (infoMessage) {
        infoMessage.remove();
      }

      const isMainLock = isMainLockSelect.value === 'true';

      if (isMainLock) {
        // Show main lock settings, hide parent selection
        parentLockSection.style.display = 'none';
        mainLockSettings.style.display = 'block';
      } else {
        // Show parent selection, hide main lock settings
        parentLockSection.style.display = 'block';
        mainLockSettings.style.display = 'none';
      }
    }
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

      // Force data refresh
      await panel.loadLockData(true);
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

  // Debug Action Methods
  static debugAddSlot(lockEntityId, userName) {
    const panel = window.smartLockManagerPanel;
    if (!panel) return;

    // Find first available slot
    const lock = panel._locks.find(l => (l.attributes?.lock_entity_id || l.entity_id) === lockEntityId);
    if (!lock) return;

    const slotDetails = lock.attributes?.slot_details || {};
    let availableSlot = null;
    for (let i = 1; i <= 10; i++) {
      if (!slotDetails[`slot_${i}`]?.is_active) {
        availableSlot = i;
        break;
      }
    }

    if (!availableSlot) {
      alert('No available slots');
      return;
    }

    const randomPin = Math.floor(1000 + Math.random() * 9000).toString();
    panel.callService('set_code_advanced', {
      entity_id: lockEntityId,
      code_slot: availableSlot,
      usercode: randomPin,
      code_slot_name: `${userName} ${availableSlot}`
    });
  }

  static debugToggleSlot(lockEntityId, slotNumber) {
    const panel = window.smartLockManagerPanel;
    if (!panel) return;

    const lock = panel._locks.find(l => (l.attributes?.lock_entity_id || l.entity_id) === lockEntityId);
    if (!lock) return;

    const slotDetails = lock.attributes?.slot_details || {};
    const slot = slotDetails[`slot_${slotNumber}`];
    const isActive = slot?.is_active;

    if (isActive) {
      panel.callService('disable_slot', {
        entity_id: lockEntityId,
        code_slot: slotNumber
      });
    } else if (slot) {
      panel.callService('enable_slot', {
        entity_id: lockEntityId,
        code_slot: slotNumber
      });
    } else {
      alert(`Slot ${slotNumber} doesn't exist. Use Add Test Slot first.`);
    }
  }

  static debugClearSlot(lockEntityId, slotNumber) {
    const panel = window.smartLockManagerPanel;
    if (!panel) return;

    panel.callService('clear_code', {
      entity_id: lockEntityId,
      code_slot: slotNumber
    });
  }

  static debugSyncSlot(childEntityId, parentEntityId, slotNumber) {
    const panel = window.smartLockManagerPanel;
    if (!panel) return;

    const parentLock = panel._locks.find(l => (l.attributes?.lock_entity_id || l.entity_id) === parentEntityId);
    if (!parentLock) {
      alert('Parent lock not found');
      return;
    }

    const parentSlots = parentLock.attributes?.slot_details || {};
    const parentSlot = parentSlots[`slot_${slotNumber}`];

    if (!parentSlot?.is_active) {
      alert(`Parent slot ${slotNumber} is not active`);
      return;
    }

    // Copy the slot from parent to child
    panel.callService('set_code_advanced', {
      entity_id: childEntityId,
      code_slot: slotNumber,
      usercode: parentSlot.usercode,
      code_slot_name: parentSlot.user_name
    });
  }

  static debugSyncAllSlots(childEntityId, parentEntityId) {
    const panel = window.smartLockManagerPanel;
    if (!panel) return;

    panel.callService('sync_child_locks', {
      entity_id: parentEntityId
    });
  }

  static debugClearChildSlot(childEntityId, slotNumber) {
    this.debugClearSlot(childEntityId, slotNumber);
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

