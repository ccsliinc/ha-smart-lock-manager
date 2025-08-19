// Form validation and user input handling module

import { PIN_VALIDATION } from '../utils/Constants.js';
import { getElementById } from '../utils/DOMUtils.js';

export class FormValidator {
  constructor() {
    this.validationErrors = new Map();
  }

  /**
   * Validate PIN code input with real-time feedback
   * @param {HTMLInputElement} input - PIN input element
   * @returns {boolean} - Whether PIN is valid
   */
  validatePinCodeInput(input) {
    if (!input) return false;

    const pinValue = input.value;
    const messageElement = getElementById('pin-validation-message');
    
    // Clear previous validation classes
    input.classList.remove('pin-error', 'pin-valid');
    
    // Check for non-numeric characters
    if (!PIN_VALIDATION.PATTERN.test(pinValue)) {
      this.showPinError(input, messageElement, 'PIN must contain only digits');
      return false;
    }
    
    // Check length requirements
    if (pinValue.length < PIN_VALIDATION.MIN_LENGTH) {
      this.showPinError(input, messageElement, 'PIN must be at least 4 digits');
      return false;
    }
    
    if (pinValue.length > PIN_VALIDATION.MAX_LENGTH) {
      this.showPinError(input, messageElement, 'PIN must be 8 digits or less');
      return false;
    }
    
    // Valid PIN
    this.showPinSuccess(input, messageElement, 'Valid PIN code');
    return true;
  }

  /**
   * Show PIN validation error
   * @param {HTMLInputElement} input - PIN input element
   * @param {HTMLElement} messageElement - Message display element
   * @param {string} message - Error message
   */
  showPinError(input, messageElement, message) {
    input.classList.add('pin-error');
    if (messageElement) {
      messageElement.textContent = message;
      messageElement.className = 'pin-validation-message error';
      messageElement.style.opacity = '1';
    }
    this.validationErrors.set('pin', message);
  }

  /**
   * Show PIN validation success
   * @param {HTMLInputElement} input - PIN input element
   * @param {HTMLElement} messageElement - Message display element
   * @param {string} message - Success message
   */
  showPinSuccess(input, messageElement, message) {
    input.classList.add('pin-valid');
    if (messageElement) {
      messageElement.textContent = message;
      messageElement.className = 'pin-validation-message success';
      messageElement.style.opacity = '1';
    }
    this.validationErrors.delete('pin');
  }

  /**
   * Validate date range inputs
   * @returns {boolean} - Whether date range is valid
   */
  validateDateRange() {
    const fromInput = getElementById('access_from');
    const toInput = getElementById('access_to');
    
    if (!fromInput || !toInput) return true; // Skip if elements not found
    
    const fromValue = fromInput.value;
    const toValue = toInput.value;
    
    // Clear previous errors
    this.hideDateRangeError();
    
    // If both are empty, that's valid
    if (!fromValue && !toValue) {
      return true;
    }
    
    // If only one is filled, that's valid too
    if (!fromValue || !toValue) {
      return true;
    }
    
    // Both are filled, validate the range
    const fromDate = new Date(fromValue);
    const toDate = new Date(toValue);
    
    if (fromDate >= toDate) {
      this.showDateRangeError('End date must be after start date');
      return false;
    }
    
    // Check if start date is in the past (optional warning)
    const now = new Date();
    if (fromDate < now) {
      // This is just a warning, not an error
      this.showDateRangeWarning('Start date is in the past');
    }
    
    return true;
  }

  /**
   * Show date range error
   * @param {string} message - Error message
   */
  showDateRangeError(message) {
    const errorElement = getElementById('date-range-error');
    if (errorElement) {
      errorElement.textContent = message;
      errorElement.className = 'date-range-error error';
      errorElement.style.display = 'block';
    }
    this.validationErrors.set('dateRange', message);
  }

  /**
   * Show date range warning
   * @param {string} message - Warning message
   */
  showDateRangeWarning(message) {
    const errorElement = getElementById('date-range-error');
    if (errorElement) {
      errorElement.textContent = message;
      errorElement.className = 'date-range-error warning';
      errorElement.style.display = 'block';
    }
  }

  /**
   * Hide date range error/warning
   */
  hideDateRangeError() {
    const errorElement = getElementById('date-range-error');
    if (errorElement) {
      errorElement.style.display = 'none';
      errorElement.textContent = '';
    }
    this.validationErrors.delete('dateRange');
  }

  /**
   * Validate multiselect field
   * @param {HTMLSelectElement} selectElement - Select element
   * @param {string} fieldName - Field name for error tracking
   * @returns {Array} - Selected values
   */
  validateMultiSelect(selectElement, fieldName) {
    if (!selectElement) return [];
    
    const selectedOptions = Array.from(selectElement.selectedOptions);
    const selectedValues = selectedOptions.map(option => parseInt(option.value));
    
    // Clear any previous errors for this field
    this.validationErrors.delete(fieldName);
    
    return selectedValues;
  }

  /**
   * Check if form has any validation errors
   * @returns {boolean} - Whether form is valid
   */
  isFormValid() {
    return this.validationErrors.size === 0;
  }

  /**
   * Get all validation errors
   * @returns {Array} - Array of error messages
   */
  getValidationErrors() {
    return Array.from(this.validationErrors.values());
  }

  /**
   * Clear all validation errors
   */
  clearValidationErrors() {
    this.validationErrors.clear();
    this.hideDateRangeError();
    
    // Clear PIN validation
    const pinInput = getElementById('pin_code');
    const pinMessage = getElementById('pin-validation-message');
    
    if (pinInput) {
      pinInput.classList.remove('pin-error', 'pin-valid');
    }
    
    if (pinMessage) {
      pinMessage.style.opacity = '0';
    }
  }

  /**
   * Setup form validation event listeners
   * @param {HTMLFormElement} form - Form element
   */
  setupFormValidation(form) {
    if (!form) return;
    
    // PIN validation on input
    const pinInput = form.querySelector('#pin_code');
    if (pinInput) {
      pinInput.addEventListener('input', () => this.validatePinCodeInput(pinInput));
    }
    
    // Date range validation on change
    const fromInput = form.querySelector('#access_from');
    const toInput = form.querySelector('#access_to');
    
    if (fromInput) {
      fromInput.addEventListener('change', () => this.validateDateRange());
    }
    
    if (toInput) {
      toInput.addEventListener('change', () => this.validateDateRange());
    }
  }
}