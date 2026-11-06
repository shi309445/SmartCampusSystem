import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 开发服务器配置
 *
 * 前端默认直连 http://127.0.0.1:8000（见 .env.development 的 VITE_API_BASE_URL），
 * 后端 CORS 已经允许 5173 / localhost:5173（app/config.py 的 CORS_ORIGINS）。
 *
 * 下面仍然配了 /api 与 /static 的代理：把 .env.development 里的地址改成空字符串，
 * 前端就改走同源代理——后端换端口、或者不想让浏览器发跨域预检时用得上。
 *
 * 没有启用 vite-plugin-vue-devtools：它的悬浮按钮会和右下角的智能助手入口叠在一起。
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      },
      '/static': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  }
})
