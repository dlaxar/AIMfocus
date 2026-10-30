import { execSync } from 'node:child_process'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Read from git when the dev server starts / the bundle is built; empty when git is unavailable
function git(args) {
  try {
    return execSync(`git ${args}`, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch {
    return ''
  }
}

// git@github.com:owner/repo.git or https://github.com/owner/repo.git -> https://github.com/owner/repo
const repoUrl = git('remote get-url origin')
  .replace(/^git@([^:]+):/, 'https://$1/')
  .replace(/\.git$/, '')

const commit = {
  hash: git('rev-parse HEAD'),
  shortHash: git('rev-parse --short HEAD'),
  subject: git('log -1 --format=%s'),
  date: git('log -1 --format=%cI'),
  url: repoUrl.startsWith('https://github.com/') ? repoUrl : '',
}

export default defineConfig({
  plugins: [vue()],
  define: {
    __COMMIT__: JSON.stringify(commit),
  },
  server: {
    port: 5173,
    // Forward API calls to the core (FastAPI on :8001) to avoid CORS in dev
    proxy: {
      '/api': 'http://localhost:8001',
      '/healthz': 'http://localhost:8001',
    },
  },
})
