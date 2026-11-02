// Minimal UI test runner with NO new dependency: Vite (already installed) bundles each tests/*.test.ts(x) for Node (SSR build) with the API client and the useApi hook replaced by the mocks in tests/mocks,
// then Node's built-in test runner executes the bundles. Pages are rendered with react-dom/server, so a test sees exactly the markup a user would see for a given API state.
//   npm run test:ui
import { build } from 'vite';
import react from '@vitejs/plugin-react';
import { readdirSync, mkdirSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, 'node_modules', '.cache', 'ui-tests');
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

const tests = readdirSync(join(root, 'tests')).filter((f) => /\.test\.tsx?$/.test(f));
if (!tests.length) { console.error('no tests found'); process.exit(1); }

for (const f of tests) {
  await build({
    root, configFile: false, logLevel: 'error', plugins: [react()],
    resolve: { alias: [
      { find: /^(\.\.\/)+api\/client$|^\.\/api\/client$/, replacement: join(root, 'tests', 'mocks', 'client.ts') },
      { find: /^(\.\.\/)+hooks\/useApi$|^\.\/hooks\/useApi$/, replacement: join(root, 'tests', 'mocks', 'useApi.ts') },
    ] },
    build: { ssr: join(root, 'tests', f), outDir: out, emptyOutDir: false, minify: false, rollupOptions: { output: { format: 'es', entryFileNames: f.replace(/\.tsx?$/, '.mjs') } } },
  });
}
const files = tests.map((f) => join(out, f.replace(/\.tsx?$/, '.mjs')));
const r = spawnSync(process.execPath, ['--test', ...files], { stdio: 'inherit', cwd: root });
process.exit(r.status ?? 1);
