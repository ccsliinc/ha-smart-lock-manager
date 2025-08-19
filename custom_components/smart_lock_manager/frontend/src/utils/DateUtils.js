// Date and time utility functions

/**
 * Convert a Date object to datetime-local input format (YYYY-MM-DDTHH:MM)
 * @param {Date} date - The date to convert
 * @returns {string} - Formatted datetime string
 */
export function dateToDatetimeLocal(date) {
  if (!date || !(date instanceof Date) || isNaN(date.getTime())) {
    return '';
  }
  
  const year = date.getFullYear();
  const month = (date.getMonth() + 1).toString().padStart(2, '0');
  const day = date.getDate().toString().padStart(2, '0');
  const hours = date.getHours().toString().padStart(2, '0');
  const minutes = date.getMinutes().toString().padStart(2, '0');
  
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

/**
 * Parse datetime string to Date object
 * @param {string} dateTimeString - ISO datetime string
 * @returns {Date|null} - Parsed date or null if invalid
 */
export function parseDateTime(dateTimeString) {
  if (!dateTimeString) return null;
  
  try {
    const date = new Date(dateTimeString);
    return isNaN(date.getTime()) ? null : date;
  } catch (error) {
    return null;
  }
}

/**
 * Check if a date string is valid
 * @param {string} dateString - Date string to validate
 * @returns {boolean} - Whether the date is valid
 */
export function isValidDate(dateString) {
  if (!dateString) return false;
  const date = new Date(dateString);
  return !isNaN(date.getTime());
}

/**
 * Format a date for display
 * @param {Date|string} date - Date to format
 * @param {Object} options - Formatting options
 * @returns {string} - Formatted date string
 */
export function formatDateForDisplay(date, options = {}) {
  if (!date) return '';
  
  const dateObj = typeof date === 'string' ? new Date(date) : date;
  if (isNaN(dateObj.getTime())) return '';
  
  const defaultOptions = {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  };
  
  return dateObj.toLocaleString('en-US', { ...defaultOptions, ...options });
}