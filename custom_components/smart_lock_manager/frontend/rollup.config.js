import { nodeResolve } from '@rollup/plugin-node-resolve';
import commonjs from '@rollup/plugin-commonjs';
import { terser } from '@rollup/plugin-terser';
import replace from '@rollup/plugin-replace';
import copy from 'rollup-plugin-copy';

const production = process.env.NODE_ENV === 'production';

export default {
  input: 'src/main.js',
  output: {
    file: 'dist/smart-lock-manager-panel.js',
    format: 'iife',
    name: 'SmartLockManagerPanel',
    sourcemap: !production,
  },
  plugins: [
    replace({
      'process.env.NODE_ENV': JSON.stringify(process.env.NODE_ENV || 'development'),
      preventAssignment: true,
    }),
    nodeResolve({
      browser: true,
    }),
    commonjs(),
    production && terser({
      compress: {
        drop_console: true,
        drop_debugger: true,
      },
      mangle: {
        reserved: ['SmartLockManagerPanel'],
      },
    }),
    copy({
      targets: [
        {
          src: 'dist/smart-lock-manager-panel.js',
          dest: '../../../',
          rename: (name, extension) => `${name}.js`
        }
      ],
      hook: 'writeBundle'
    })
  ].filter(Boolean),
  watch: {
    clearScreen: false,
  },
};