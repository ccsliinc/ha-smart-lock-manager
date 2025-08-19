// DOM manipulation utility functions

/**
 * Safely get element by ID
 * @param {string} id - Element ID
 * @returns {Element|null} - Found element or null
 */
export function getElementById(id) {
  return document.getElementById(id);
}

/**
 * Safely query selector
 * @param {string} selector - CSS selector
 * @param {Element} parent - Parent element (default: document)
 * @returns {Element|null} - Found element or null
 */
export function querySelector(selector, parent = document) {
  return parent.querySelector(selector);
}

/**
 * Safely query all selectors
 * @param {string} selector - CSS selector
 * @param {Element} parent - Parent element (default: document)
 * @returns {NodeList} - Found elements
 */
export function querySelectorAll(selector, parent = document) {
  return parent.querySelectorAll(selector);
}

/**
 * Create element with attributes and content
 * @param {string} tagName - HTML tag name
 * @param {Object} attributes - Element attributes
 * @param {string} content - Inner HTML content
 * @returns {Element} - Created element
 */
export function createElement(tagName, attributes = {}, content = '') {
  const element = document.createElement(tagName);
  
  Object.entries(attributes).forEach(([key, value]) => {
    if (key === 'className') {
      element.className = value;
    } else if (key === 'dataset') {
      Object.entries(value).forEach(([dataKey, dataValue]) => {
        element.dataset[dataKey] = dataValue;
      });
    } else {
      element.setAttribute(key, value);
    }
  });
  
  if (content) {
    element.innerHTML = content;
  }
  
  return element;
}

/**
 * Show element with animation
 * @param {Element} element - Element to show
 * @param {string} display - Display style (default: 'block')
 */
export function showElement(element, display = 'block') {
  if (!element) return;
  element.style.display = display;
  element.style.opacity = '1';
}

/**
 * Hide element with animation
 * @param {Element} element - Element to hide
 */
export function hideElement(element) {
  if (!element) return;
  element.style.opacity = '0';
  setTimeout(() => {
    element.style.display = 'none';
  }, 300);
}

/**
 * Add CSS class safely
 * @param {Element} element - Target element
 * @param {string} className - Class name to add
 */
export function addClass(element, className) {
  if (element && className) {
    element.classList.add(className);
  }
}

/**
 * Remove CSS class safely
 * @param {Element} element - Target element
 * @param {string} className - Class name to remove
 */
export function removeClass(element, className) {
  if (element && className) {
    element.classList.remove(className);
  }
}

/**
 * Toggle CSS class safely
 * @param {Element} element - Target element
 * @param {string} className - Class name to toggle
 * @returns {boolean} - Whether class is now present
 */
export function toggleClass(element, className) {
  if (element && className) {
    return element.classList.toggle(className);
  }
  return false;
}

/**
 * Clear all child elements
 * @param {Element} element - Parent element
 */
export function clearChildren(element) {
  if (element) {
    element.innerHTML = '';
  }
}

/**
 * Set element text content safely
 * @param {Element} element - Target element
 * @param {string} text - Text content
 */
export function setTextContent(element, text) {
  if (element) {
    element.textContent = text;
  }
}

/**
 * Get form data as object
 * @param {HTMLFormElement} form - Form element
 * @returns {Object} - Form data as key-value pairs
 */
export function getFormData(form) {
  if (!form) return {};
  
  const formData = new FormData(form);
  const data = {};
  
  for (const [key, value] of formData.entries()) {
    data[key] = value;
  }
  
  return data;
}