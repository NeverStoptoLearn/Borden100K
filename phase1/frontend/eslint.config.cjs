const js = require('@eslint/js');
const vue = require('eslint-plugin-vue');
const globals = require('globals');

module.exports = [
  { ignores: ['node_modules/**', 'dist/**', 'test-results/**', 'playwright-report/**'] },
  js.configs.recommended,
  ...vue.configs['flat/essential'],
  {
    files: ['src/**/*.{js,vue}', 'tests/**/*.js'],
    languageOptions: { globals: { ...globals.browser, ...globals.es2022 } },
    rules: { 'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }] },
  },
  { files: ['tests/**/*.js'], languageOptions: { globals: globals.node } },
  { files: ['vite.config.js', 'eslint.config.cjs'], languageOptions: { globals: globals.node } },
];
