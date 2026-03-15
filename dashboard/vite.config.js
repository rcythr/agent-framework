import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/agents': 'http://localhost:3000',
      '/sessions': 'http://localhost:3000',
      '/projects': 'http://localhost:3000',
      '/internal': 'http://localhost:3000',
    },
  },
})
