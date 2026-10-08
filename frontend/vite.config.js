import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { quasar, transformAssetUrls } from '@quasar/vite-plugin'

// Backend dev server proxy so the SPA can call /api/v1 and /api/v2 without CORS tweaks.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendUrl = env.VITE_BACKEND_URL || 'http://localhost:8000'
  const apiProxy = {
    '/api': { target: backendUrl, changeOrigin: true },
  }

  return {
    plugins: [
      vue({ template: { transformAssetUrls } }),
      // Quasar Vite 插件：按需组件 tree-shaking，不改造为 Quasar CLI
      quasar(),
    ],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: apiProxy,
    },
    preview: {
      host: '0.0.0.0',
      proxy: apiProxy,
    },
  }
})
