import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
/* The demo is its own Vite root. It shares node_modules with the app but nothing else: the app's
   build never sees this folder, so the Figma sample values cannot reach the server. */
export default defineConfig({ root: __dirname, plugins: [react()], server: { port: 5300, strictPort: true } });
