import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// V-Studio dev server.
//
// To avoid CORS in local dev, when VITE_DEV_PROXY_TARGET is set we proxy
// /api/* through Vite to the backend. The browser sees same-origin
// (localhost:5175) so no preflight rejection from Railway's CORS allowlist.
//
// In production builds the proxy is irrelevant — Vite inlines VITE_API_BASE
// and the deployed UI hits the backend directly (CORS allowlist on Railway
// must include the deployed UI origin).
export default defineConfig(({ mode }) => {
  // Pass '' as cwd → loadEnv falls back to its own resolution. Avoids
  // referencing Node's `process` global, which would require @types/node
  // and break `tsc -b` during prod build.
  const env = loadEnv(mode, '', '');
  const proxyTarget =
    env.VITE_DEV_PROXY_TARGET || env.VITE_API_BASE ||
    'https://cmds-book-production.up.railway.app';

  return {
    plugins: [react()],
    // Force a SINGLE katex instance across the app and rehype-katex.
    // Without this, `import 'katex/contrib/mhchem'` registers \ce on the
    // app's hoisted katex while rehype-katex renders with its own nested
    // copy — so chemistry (\ce{...}) never resolves at render time and
    // falls back to raw "\ceKCl". Deduping collapses them to one instance
    // so the mhchem side-effect import reaches the renderer.
    resolve: {
      dedupe: ['katex'],
    },
    server: {
      port: 5175,
      strictPort: true,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
          secure: true,
        },
      },
    },
    preview: {
      port: 5175,
    },
  };
});
