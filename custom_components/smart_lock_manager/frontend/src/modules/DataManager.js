// Data management and state handling module

import { SLOT_COUNT, EVENT_TYPES } from '../utils/Constants.js';

export class DataManager {
  constructor(hass, serviceClient) {
    this.hass = hass;
    this.serviceClient = serviceClient;
    this.locks = [];
    this.selectedLock = null;
    this.zWaveCodes = new Map();
    this.eventListenerSetup = false;
  }

  /**
   * Update Home Assistant reference
   * @param {Object} hass - Home Assistant object
   */
  setHass(hass) {
    this.hass = hass;
    this.serviceClient.setHass(hass);
    this.setupEventListeners();
  }

  /**
   * Load lock data from Home Assistant
   * @param {boolean} bypassCache - Whether to bypass cache and fetch fresh data
   * @returns {Promise<Array>} - Array of lock data
   */
  async loadLockData(bypassCache = false) {
    if (!this.hass) return [];

    try {
      // Find Smart Lock Manager entities
      const entities = Object.keys(this.hass.states).filter(entityId => {
        if (!entityId.startsWith('sensor.')) return false;
        const state = this.hass.states[entityId];
        return state?.attributes?.integration === 'smart_lock_manager' ||
               state?.attributes?.unique_id?.includes('smart_lock_manager');
      });

      const lockData = [];

      for (const entityId of entities) {
        let currentState;
        let attributes;

        if (bypassCache) {
          // Fetch fresh state from API
          const freshState = await this.serviceClient.getEntityState(entityId);
          if (freshState) {
            currentState = freshState.state;
            attributes = freshState.attributes;
            // Update cached state
            if (this.hass.states[entityId]) {
              this.hass.states[entityId] = freshState;
            }
          } else {
            continue;
          }
        } else {
          // Use cached state
          const entity = this.hass.states[entityId];
          currentState = entity.state;
          attributes = entity.attributes;
        }

        lockData.push({
          entity_id: entityId,
          state: currentState,
          attributes: attributes
        });
      }

      this.locks = lockData;

      // Select first lock if none selected
      if (!this.selectedLock && this.locks.length > 0) {
        this.selectedLock = this.locks[0];
      }

      return this.locks;
    } catch (error) {
      return [];
    }
  }

  /**
   * Setup event listeners for Z-Wave events
   */
  setupEventListeners() {
    if (this.eventListenerSetup || !this.hass?.connection) return;

    this.hass.connection.subscribeEvents((event) => {
      if (event.event_type === EVENT_TYPES.CODES_READ) {
        this.handleZWaveCodesRead(event.data);
      }
    }, 'state_changed');

    this.eventListenerSetup = true;
  }

  /**
   * Handle Z-Wave code reading events
   * @param {Object} eventData - Event data from Z-Wave
   */
  handleZWaveCodesRead(eventData) {
    if (!eventData?.entity_id || !eventData?.codes) return;

    // Cache Z-Wave codes for sync validation
    this.zWaveCodes.set(eventData.entity_id, eventData.codes);
  }

  /**
   * Get slot display information
   * @param {Object} lock - Lock data object
   * @param {number} slot - Slot number
   * @returns {Object} - Display information for slot
   */
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

  /**
   * Get current selected lock
   * @returns {Object|null} - Selected lock data
   */
  getSelectedLock() {
    return this.selectedLock;
  }

  /**
   * Set selected lock
   * @param {Object} lock - Lock data object
   */
  setSelectedLock(lock) {
    this.selectedLock = lock;
  }

  /**
   * Get all locks
   * @returns {Array} - Array of all lock data
   */
  getAllLocks() {
    return this.locks;
  }

  /**
   * Get parent lock options for dropdown
   * @returns {Array} - Array of parent lock options
   */
  getParentLockOptions() {
    return this.locks
      .filter(lock => {
        const attributes = lock.attributes;
        // Don't include the current lock being edited
        if (attributes.lock_entity_id === this.currentLockEntityId) {
          return false;
        }
        // Only include main locks (not child locks)
        return attributes.is_main_lock !== false && !attributes.parent_lock_id;
      })
      .map(lock => ({
        entity_id: lock.attributes.lock_entity_id,
        name: lock.attributes.friendly_name || lock.attributes.lock_name || lock.entity_id
      }));
  }

  /**
   * Get child locks for a parent
   * @param {string} parentLockEntityId - Parent lock entity ID
   * @returns {Array} - Array of child locks
   */
  getChildLocks(parentLockEntityId) {
    return this.locks.filter(lock => 
      lock.attributes.parent_lock_id === parentLockEntityId
    );
  }

  /**
   * Refresh Z-Wave codes for a lock
   * @param {string} lockEntityId - Lock entity ID
   */
  async refreshZWaveCodes(lockEntityId) {
    if (this.hass && lockEntityId) {
      await this.serviceClient.readCodes(lockEntityId);
    }
  }

  /**
   * Get cached Z-Wave codes for a lock
   * @param {string} lockEntityId - Lock entity ID
   * @returns {Object|null} - Cached Z-Wave codes
   */
  getZWaveCodes(lockEntityId) {
    return this.zWaveCodes.get(lockEntityId) || null;
  }

  /**
   * Set current lock entity ID being edited
   * @param {string} entityId - Lock entity ID
   */
  setCurrentLockEntityId(entityId) {
    this.currentLockEntityId = entityId;
  }

  /**
   * Clear current lock entity ID
   */
  clearCurrentLockEntityId() {
    this.currentLockEntityId = null;
  }
}