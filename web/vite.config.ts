import { existsSync, readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// https://vite.dev/config/
export default defineConfig(({ command }) => {
  const tokenPath = path.join(
    os.homedir(),
    'Library',
    'Application Support',
    'elChango',
    'control-token',
  )
  const token =
    command === 'serve' && existsSync(tokenPath)
      ? readFileSync(tokenPath, 'utf8').trim()
      : ''

  return {
    plugins: [svelte()],
    server: {
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8765',
          changeOrigin: true,
          headers: { Authorization: `Bearer ${token}` },
        },
      },
    },
  }
})
