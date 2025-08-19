// Smart Lock Manager Frontend Entry Point
// Modular architecture with clean separation of concerns

import { ServiceClient } from './modules/ServiceClient.js';
import { DataManager } from './modules/DataManager.js';
import { FormValidator } from './modules/FormValidator.js';
import { SLOT_COUNT } from './utils/Constants.js';

// Prevent redefinition if already loaded
if (!window.SmartLockManagerPanel) {

class SmartLockManagerPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = undefined;
    this._narrow = false;
    this._selectedLock = null;
    this._locks = [];
    this._modalOpen = false;
    this._settingsModalOpen = false;
    this._editingSlot = null;
    this._currentLockEntityId = null;
    this._cardSpinners = new Map();
    this._slotSpinners = new Map();
    
    // Initialize modules
    this.serviceClient = new ServiceClient(this._hass);
    this.dataManager = new DataManager(this._hass, this.serviceClient);
    this.formValidator = new FormValidator();
  }

  set hass(hass) {
    const oldHass = this._hass;
    this._hass = hass;
    
    // Store reference for global access
    window.smartLockManagerPanel = this;
    
    // Update modules with new hass
    this.serviceClient.setHass(hass);
    this.dataManager.setHass(hass);
    
    // Setup event listeners when hass becomes available
    if (hass && (!oldHass || oldHass.connection !== hass.connection)) {
      this.dataManager.setupEventListeners();
    }
    
    // Force reload lock data if states changed
    if (oldHass && hass && oldHass.states !== hass.states) {
      this.loadLockData();
    }
    
    // Don't auto-refresh if modal is open to prevent losing user input
    if (!this._modalOpen && !this._settingsModalOpen) {
      this.loadLockData();
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
  }

  connectedCallback() {
    // Store global reference for static methods
    window.smartLockManagerPanel = this;
    this.loadLockData();
    this.render();
  }

  disconnectedCallback() {
    // Clean up global reference
    if (window.smartLockManagerPanel === this) {
      delete window.smartLockManagerPanel;
    }
  }

  async loadLockData(bypassCache = false) {
    this._locks = await this.dataManager.loadLockData(bypassCache);
    this._selectedLock = this.dataManager.getSelectedLock();
    
    // Don't auto-refresh if modal is open to prevent losing user input
    if (!this._modalOpen && !this._settingsModalOpen) {
      this.requestUpdate();
    }
  }

  requestUpdate() {
    this.render();
  }

  // Method stubs for the massive original functionality
  // These will be gradually implemented in separate modules
  
  openSlotModal(slotNumber) {
    this._editingSlot = slotNumber;
    this._modalOpen = true;
    this.requestUpdate();
    
    // Populate form fields after the modal is rendered
    setTimeout(() => {
      this.populateSlotForm(slotNumber);
      this.formValidator.setupFormValidation(document.getElementById('slot-form'));
    }, 100);
  }

  closeModal() {
    this._modalOpen = false;
    this._editingSlot = null;
    this.formValidator.clearValidationErrors();
    
    // Refresh data when modal closes to show any saved changes
    this.loadLockData(true);
    this.requestUpdate();
  }

  openSettingsModal(lockEntityId) {
    this._currentLockEntityId = lockEntityId;
    this.dataManager.setCurrentLockEntityId(lockEntityId);
    this._settingsModalOpen = true;
    this.requestUpdate();
    
    // Initialize lock type fields after modal renders
    setTimeout(() => {
      this.initializeLockTypeFields();
    }, 100);
  }

  closeSettingsModal() {
    this._settingsModalOpen = false;
    this._currentLockEntityId = null;
    this.dataManager.clearCurrentLockEntityId();
    this.requestUpdate();
  }

  // Placeholder methods that will be implemented in modules
  populateSlotForm(slotNumber) {
    // TODO: Move to SlotModal component
  }

  initializeLockTypeFields() {
    // TODO: Move to SettingsModal component
  }

  async saveSlotSettings() {
    // TODO: Move to SlotModal component
  }

  async saveSettings() {
    // TODO: Move to SettingsModal component
  }

  // Minimal render method - the full template will be moved to template modules
  render() {
    if (!this._locks.length) {
      this.innerHTML = `
        <ha-card>
          <div class="card-header">
            <h1>Smart Lock Manager</h1>
          </div>
          <div class="card-content">
            <p>No Smart Lock Manager entities found. Please configure your locks first.</p>
          </div>
        </ha-card>
      `;
      return;
    }

    // Basic template - full template will be in template modules
    this.innerHTML = `
      <style>
        /* Basic styles - full styles will be in template modules */
        .card-header { padding: 16px; }
        .card-content { padding: 16px; }
      </style>
      
      <ha-card>
        <div class="card-header">
          <h1>Smart Lock Manager</h1>
          <p>Selected Lock: ${this._selectedLock?.attributes?.friendly_name || 'None'}</p>
        </div>
        <div class="card-content">
          <p>Modular frontend architecture is being implemented...</p>
          <p>Found ${this._locks.length} lock(s)</p>
        </div>
      </ha-card>
    `;
  }

  // Static methods for global access
  static openSlotModal(slotNumber) {
    if (window.smartLockManagerPanel) {
      window.smartLockManagerPanel.openSlotModal(slotNumber);
    }
  }

  static closeModal() {
    if (window.smartLockManagerPanel) {
      window.smartLockManagerPanel.closeModal();
    }
  }

  static openSettingsModal(lockEntityId) {
    if (window.smartLockManagerPanel) {
      window.smartLockManagerPanel.openSettingsModal(lockEntityId);
    }
  }

  static closeSettingsModal() {
    if (window.smartLockManagerPanel) {
      window.smartLockManagerPanel.closeSettingsModal();
    }
  }
}

// Register the custom element
customElements.define('smart-lock-manager-panel', SmartLockManagerPanel);

// Export for global access
window.SmartLockManagerPanel = SmartLockManagerPanel;

}

console.log('Smart Lock Manager Panel v2025.1.0 - Modular Architecture Loaded');