/**
 * DH-MVP-POLISH-001 — take the screenshots the ticket never took.
 *
 * Starts Vite on the harness entry, then captures each scene at a desktop width
 * and again at 390px, because "works at phone width" was asserted from source and
 * never seen. Writes PNGs to `harness/shots/` (gitignored).
 *
 *   node harness/shoot.mjs
 */

import { spawn } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { chromium } from '@playwright/test'

const SCENES = [
  'shell-provider',
  'shell-lab-dual',
  'shell-two-establishments',
  'relay',
  'relay-amended',
  'patient',
]

const WIDTHS = [
  { name: 'desktop', width: 1280, height: 860 },
  { name: 'phone', width: 390, height: 844 },
]

const OUT = new URL('./shots/', import.meta.url).pathname
mkdirSync(OUT, { recursive: true })

const vite = spawn('npx', ['vite', '--port', '5178', '--strictPort'], {
  cwd: new URL('..', import.meta.url).pathname,
  stdio: ['ignore', 'pipe', 'pipe'],
})

const base = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('vite did not start in 60s')), 60_000)
  vite.stdout.on('data', (chunk) => {
    const text = String(chunk)
    const match = text.match(/Local:\s+(http:\/\/\S+)/)
    if (match) {
      clearTimeout(timer)
      resolve(match[1].replace(/\/$/, ''))
    }
  })
  vite.stderr.on('data', (chunk) => process.stderr.write(chunk))
})

console.log('vite up at', base)

const browser = await chromium.launch()
const problems = []

for (const viewport of WIDTHS) {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    deviceScaleFactor: 2,
    locale: 'fr-FR',
  })
  const page = await context.newPage()

  // Anything the page logs or fails to load is a finding, not noise: a font that
  // 404s or a thrown render error is exactly what this exercise is looking for.
  page.on('console', (msg) => {
    if (msg.type() === 'error') problems.push(`console ${viewport.name}: ${msg.text()}`)
  })
  page.on('pageerror', (err) => problems.push(`pageerror ${viewport.name}: ${err.message}`))
  page.on('requestfailed', (req) =>
    problems.push(`requestfailed ${viewport.name}: ${req.url()} ${req.failure()?.errorText ?? ''}`),
  )

  for (const scene of SCENES) {
    await page.goto(`${base}/harness/index.html?scene=${scene}`, { waitUntil: 'load' })
    await page.evaluate(() => document.fonts.ready.then(() => true))
    await page.waitForTimeout(250)

    // Horizontal overflow is the one layout claim that can be checked mechanically.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    if (overflow > 0) problems.push(`overflow ${viewport.name}/${scene}: ${overflow}px`)

    // Page-level overflow misses a child that scrolls inside its own box, which is
    // how a navigation item can sit off-screen with nothing saying so. Both of
    // these shipped and neither was visible to jsdom.
    const nav = await page.evaluate(() => {
      const el = document.querySelector('nav')
      if (!el) return null
      const clipped = el.scrollWidth - el.clientWidth
      const items = [...el.querySelectorAll('button')]
      const box = el.getBoundingClientRect()
      const outside = items.filter((item) => {
        const r = item.getBoundingClientRect()
        return r.right > box.right + 1 || r.left < box.left - 1
      })
      return { clipped, outside: outside.map((i) => i.textContent?.trim()), height: box.height }
    })
    if (nav?.clipped) problems.push(`nav clipped ${viewport.name}/${scene}: ${nav.clipped}px`)
    if (nav?.outside.length)
      problems.push(`nav item off-screen ${viewport.name}/${scene}: ${nav.outside.join(', ')}`)

    // A navigation row taking a third of a phone screen is the grid distributing
    // leftover height across its rows rather than to the content.
    if (viewport.name === 'phone' && nav && nav.height > viewport.height * 0.25)
      problems.push(`nav too tall ${viewport.name}/${scene}: ${Math.round(nav.height)}px`)

    await page.screenshot({
      path: `${OUT}${scene}-${viewport.name}.png`,
      fullPage: true,
    })
    console.log(`shot ${scene}-${viewport.name}`)
  }

  await context.close()
}

// Which faces actually loaded, rather than which were declared.
const context = await browser.newContext({ viewport: { width: 1280, height: 860 } })
const page = await context.newPage()
await page.goto(`${base}/harness/index.html?scene=shell-provider`, { waitUntil: 'load' })
await page.evaluate(() => document.fonts.ready.then(() => true))
const fonts = await page.evaluate(() =>
  [...document.fonts].filter((f) => f.status === 'loaded').map((f) => `${f.family} ${f.weight}`),
)
console.log('FONTS_LOADED:', JSON.stringify([...new Set(fonts)].sort()))
await context.close()

await browser.close()
vite.kill()

console.log(problems.length ? 'PROBLEMS:\n' + problems.join('\n') : 'PROBLEMS: none')
process.exit(0)
