// Home Assistant API communication module

import { SERVICES } from '../utils/Constants.js';

export class ServiceClient {
  constructor(hass) {
    this.hass = hass;
  }

  /**
   * Update Home Assistant reference
   * @param {Object} hass - Home Assistant object
   */
  setHass(hass) {
    this.hass = hass;
  }

  /**
   * Call a Home Assistant service
   * @param {string} service - Service name
   * @param {Object} serviceData - Service data
   * @param {string} domain - Service domain
   * @returns {Promise} - Service call result
   */
  async callService(service, serviceData = {}, domain = SERVICES.DOMAIN) {
    if (!this.hass?.callService) {
      throw new Error('Home Assistant not available');
    }

    try {
      const result = await this.hass.callService(domain, service, serviceData);
      
      // Force refresh of entities after service call
      await this.refreshEntities(serviceData.entity_id);
      
      return result;
    } catch (error) {
      throw new Error(`Service call failed: ${error.message}`);
    }
  }

  /**
   * Set advanced code with full scheduling options
   * @param {string} entityId - Lock entity ID
   * @param {Object} codeData - Code configuration data
   * @returns {Promise} - Service call result
   */
  async setCodeAdvanced(entityId, codeData) {
    const serviceData = {
      entity_id: entityId,
      ...codeData
    };

    return this.callService(SERVICES.SET_CODE_ADVANCED, serviceData);
  }

  /**
   * Clear a code slot
   * @param {string} entityId - Lock entity ID
   * @param {number} codeSlot - Slot number to clear
   * @returns {Promise} - Service call result
   */
  async clearCode(entityId, codeSlot) {
    return this.callService(SERVICES.CLEAR_CODE, {
      entity_id: entityId,
      code_slot: codeSlot
    });
  }

  /**
   * Enable a code slot
   * @param {string} entityId - Lock entity ID
   * @param {number} codeSlot - Slot number to enable
   * @returns {Promise} - Service call result
   */
  async enableSlot(entityId, codeSlot) {
    return this.callService(SERVICES.ENABLE_SLOT, {
      entity_id: entityId,
      code_slot: codeSlot
    });
  }

  /**
   * Disable a code slot
   * @param {string} entityId - Lock entity ID
   * @param {number} codeSlot - Slot number to disable
   * @returns {Promise} - Service call result
   */
  async disableSlot(entityId, codeSlot) {
    return this.callService(SERVICES.DISABLE_SLOT, {
      entity_id: entityId,
      code_slot: codeSlot
    });
  }

  /**
   * Update lock settings
   * @param {string} entityId - Lock entity ID
   * @param {Object} settings - Settings to update
   * @returns {Promise} - Service call result
   */
  async updateLockSettings(entityId, settings) {
    return this.callService(SERVICES.UPDATE_LOCK_SETTINGS, {
      entity_id: entityId,
      ...settings
    });
  }

  /**
   * Sync child locks with parent
   * @param {string} entityId - Parent lock entity ID
   * @returns {Promise} - Service call result
   */
  async syncChildLocks(entityId) {
    return this.callService(SERVICES.SYNC_CHILD_LOCKS, {
      entity_id: entityId
    });
  }

  /**
   * Read codes from Z-Wave lock
   * @param {string} entityId - Lock entity ID
   * @returns {Promise} - Service call result
   */
  async readCodes(entityId) {
    return this.callService(SERVICES.READ_CODES, {
      entity_id: entityId
    });
  }

  /**
   * Refresh Home Assistant entities
   * @param {string} entityId - Entity ID to refresh
   * @returns {Promise} - Refresh result
   */
  async refreshEntities(entityId) {
    if (!this.hass?.callService || !entityId) return;

    try {
      // Find related sensor entities
      const sensorEntities = Object.keys(this.hass.states).filter(id => 
        id.includes('smart_lock_manager') && id.includes(entityId.split('.')[1])
      );

      // Force refresh each sensor
      for (const sensorId of sensorEntities) {
        await this.hass.callService('homeassistant', 'update_entity', {
          entity_id: sensorId
        });
      }

      // Wait for backend processing
      await new Promise(resolve => setTimeout(resolve, 1000));
    } catch (error) {
      // Refresh errors are non-critical
    }
  }

  /**
   * Get fresh entity state from API
   * @param {string} entityId - Entity ID
   * @returns {Promise<Object>} - Entity state
   */
  async getEntityState(entityId) {
    if (!this.hass?.connection || !entityId) return null;

    try {
      const response = await this.hass.connection.sendMessage({
        type: 'get_states',
        entity_id: entityId
      });
      return response[0] || null;
    } catch (error) {
      return this.hass.states[entityId] || null;
    }
  }
}