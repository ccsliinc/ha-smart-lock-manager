// Self-contained ESLint config (no external shareable config required) so
// `npm run lint` runs with only `eslint` installed. Legacy .eslintrc format;
// pinned to eslint 8 in package.json.
module.exports = {
  root: true,
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
  },
  env: {
    browser: true,
    es2022: true,
  },
  extends: ['eslint:recommended'],
  globals: {
    customElements: 'readonly',
    HTMLElement: 'readonly',
    window: 'readonly',
    document: 'readonly',
    setTimeout: 'readonly',
    clearTimeout: 'readonly',
    fetch: 'readonly',
    Node: 'readonly',
  },
  rules: {
    'no-console': 'warn',
    'no-debugger': 'error',
    // The extracted panel is a near-mechanical port of the original bundle;
    // do not fail the build on pre-existing unused locals/vars.
    'no-unused-vars': 'warn',
    'no-empty': ['warn', { allowEmptyCatch: true }],
  },
};
