/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import { configDefaults } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { openmrsDevProxyPlugin } from './vite.devOpenmrsProxy.js'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Reads the repository's existing root .env (OMRS_ADMIN_PASSWORD) instead of
  // duplicating a credential into a second file under ui/. Only the dev-proxy
  // plugin below (a Node-side Vite plugin, never client code) reads this
  // value — Vite only exposes `VITE_`-prefixed vars to the browser bundle, and
  // OMRS_ADMIN_PASSWORD is deliberately not prefixed that way.
  const rootEnvDir = path.resolve(import.meta.dirname, '..')
  const env = loadEnv(mode, rootEnvDir, '')

  return {
    envDir: rootEnvDir,
    plugins: [react(), openmrsDevProxyPlugin(env)],
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/setupTests.ts'],
      // DH-CLINIC-004B: e2e/ holds real-browser Playwright specs (a
      // different test runner, run only via `npm run test:e2e` against a
      // live stack) — vitest's default include glob would otherwise also
      // try to collect e2e/specs/*.spec.ts and fail importing '@playwright/test'.
      exclude: [...configDefaults.exclude, 'e2e/**'],
    },
  }
})
