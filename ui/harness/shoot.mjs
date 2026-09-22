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
  'platform-zero-membership',
  'platform-with-membership',
  'platform-connect-readiness',
  'platform-native-blocked',
  'platform-not-integrable',
  'platform-route-activation',
  'platform-first-admin',
  'platform-native-no-template',
  'platform-native-ready',
  'platform-new-facility',
  'platform-team',
  'platform-team-add',
  'platform-team-empty',
  'shell-provider',
  'shell-lab-dual',
  'shell-two-establishments',
  'relay',
  'relay-amended',
  'patient',
]

// Scenes whose subject is a screen reached by an action, and the control that
// reaches it. Without this the detail captures would show the facility list.
const SCENE_ACTIONS = {
  'platform-connect-readiness': [/Laboratoire Central/],
  'platform-native-blocked': [/Laboratoire Horizon/],
  'platform-not-integrable': [/Laboratoire Central/],
  'platform-new-facility': [/Enregistrer un établissement/],
  // Two gestures, because the confirmation is the subject: open the provider,
  // then press Activer. The scene is the restatement an operator reads before
  // switching production routing on, not the button that leads to it.
  'platform-route-activation': [/Clinique Al Rahma/, /^Activer$/],
  'platform-native-no-template': [/Laboratoire Bahr/],
  'platform-native-ready': [/Laboratoire Nour/],
  'platform-team': [/Laboratoire Central/],
  'platform-team-empty': [/Laboratoire Central/],
  // The add panel, open on somebody who is already on the team: the role she
  // holds is ticked and disabled, because nothing on this screen removes access.
  'platform-team-add': [
    { click: /Laboratoire Central/ },
    { click: /^Ajouter une personne$/ },
    { fill: { label: /Rechercher une personne/, value: 'Fatou' } },
    { click: /^Rechercher$/ },
    { click: /Fatou Hassan/ },
  ],
  // Four gestures to reach the step this correction was about: open the wizard,
  // name the facility, choose Native, then find and select the person.
  'platform-first-admin': [
    { click: /Enregistrer un établissement/ },
    { fill: { label: /Nom de l'établissement/, value: 'Laboratoire Central' } },
    { click: /Laboratoire d'analyses/, role: 'radio' },
    { click: /^Continuer$/ },
    { click: /Utiliser DHealth/, role: 'radio' },
    { click: /^Continuer$/ },
    { fill: { label: /Rechercher une personne/, value: 'Ahmed' } },
    { click: /^Rechercher$/ },
    { click: /Ahmed Osman/ },
  ],
}

// Phone width is the patient surface's case. Platform work is desktop and tablet,
// so 768 is the width that matters for the network scenes -- a facility list and a
// wizard have to stay usable on a tablet in an office, not on a handset.
const WIDTHS = [
  { name: 'desktop', width: 1280, height: 860 },
  { name: 'tablet', width: 768, height: 1024 },
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

    // Some scenes are about a screen you reach by doing something. Capturing the
    // list and calling the file "connect-readiness" would be a screenshot that
    // documents the wrong thing, so the capture performs the real interaction.
    // A step is a regex (click that button) or {click} / {fill}. The wizard needs
    // fill: its later steps are unreachable until the earlier ones are answered,
    // and a screenshot of a step nobody could reach documents nothing.
    for (const step of SCENE_ACTIONS[scene] ?? []) {
      if (step instanceof RegExp || step.click) {
        await page
          .getByRole(step.role ?? 'button', { name: step instanceof RegExp ? step : step.click })
          .first()
          .click()
      } else if (step.fill) {
        await page.getByLabel(step.fill.label).first().fill(step.fill.value)
      }
      await page.waitForTimeout(300)
    }

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

    // DH-MVP-ONBOARD-001: a primary action pushed outside the viewport is unusable
    // without anything looking wrong, and a wizard is exactly where that happens --
    // the step advances and the button that advances it is off the right edge.
    const offscreen = await page.evaluate(() => {
      const width = document.documentElement.clientWidth
      return [...document.querySelectorAll('button')]
        .filter((el) => {
          const r = el.getBoundingClientRect()
          return r.width > 0 && (r.right > width + 1 || r.left < -1)
        })
        .map((el) => el.textContent?.trim() ?? '(sans texte)')
    })
    if (offscreen.length)
      problems.push(`control off-screen ${viewport.name}/${scene}: ${offscreen.join(', ')}`)

    // Every interactive control needs an accessible name. A select whose only
    // label is a neighbouring table header reads as nothing to a screen reader.
    const unlabelled = await page.evaluate(() =>
      [...document.querySelectorAll('select, input')]
        .filter((el) => {
          if (el.type === 'radio' || el.type === 'checkbox' || el.type === 'hidden') return false
          const labelled =
            el.getAttribute('aria-label') ||
            el.getAttribute('aria-labelledby') ||
            (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) ||
            el.closest('label')
          return !labelled
        })
        .map((el) => `${el.tagName.toLowerCase()}#${el.id || '(no id)'}`),
    )
    if (unlabelled.length)
      problems.push(`unlabelled control ${viewport.name}/${scene}: ${unlabelled.join(', ')}`)

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
