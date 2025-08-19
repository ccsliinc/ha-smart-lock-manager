module.exports = {
  extends: ['standard'],
  env: {
    browser: true,
    es6: true,
  },
  globals: {
    customElements: 'readonly',
    HTMLElement: 'readonly',
  },
  rules: {
    'no-console': 'warn',
    'no-debugger': 'error',
    'space-before-function-paren': ['error', {
      anonymous: 'always',
      named: 'never',
      asyncArrow: 'always'
    }],
  },
};