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
  // DH-CONNECT-CONTRACT-001 §79. Seven, because the states differ on screen and
  // the pair that matters -- qualified beside not-registered -- is only
  // convincing if somebody can look at both.
  'connector-none',
  'connector-registered',
  'connector-failed',
  'connector-suite-passed',
  'connector-qualified',
  // DH-CONNECT-AGENT-001: the three states DHealth can now actually observe. The healthy one is
  // the capture most likely to be over-read, which is exactly why it is in the set.
  'connector-runtime-healthy',
  'connector-runtime-degraded',
  'connector-runtime-unavailable',
  'connector-stale',
  'connector-suspended',
  'connector-native-unchanged',
  'shell-provider',
  'shell-lab-dual',
  'shell-two-establishments',
  'relay',
  'relay-amended',
  'patient',
  // DH-USABILITY-001 §29: the clinic and laboratory workflows. Everything above this line is
  // platform, shell or patient work -- the two screens a clinician and a technician spend their
  // day in had never been photographed at any width, and the mechanical checks below (overflow,
  // off-screen controls, unlabelled inputs, nav height) had therefore never run against them.
  'clinic-today',
  'clinic-patients',
  'clinic-patient-new',
  'clinic-orders',
  'clinic-order-handoff',
  'clinic-results-queue',
  'clinic-results-detail',
  'clinic-results-superseded',
  'lab-today',
  'lab-orders',
  'lab-entry',
  'lab-release-queue',
  'lab-release-inspect',
  'lab-version-history',
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
  // Every connector scene opens the same laboratory; the connector section sits
  // below discovery on its detail screen.
  'connector-none': [/Laboratoire Central/],
  'connector-registered': [/Laboratoire Central/],
  'connector-failed': [/Laboratoire Central/],
  'connector-suite-passed': [/Laboratoire Central/],
  'connector-qualified': [/Laboratoire Central/],
  // The runtime states live on the facility detail, same as every other connector scene. Without
  // the click these capture the facility *list*, which documents nothing about the runtime.
  'connector-runtime-healthy': [/Laboratoire Central/],
  'connector-runtime-degraded': [/Laboratoire Central/],
  'connector-runtime-unavailable': [/Laboratoire Central/],
  'connector-stale': [/Laboratoire Central/],
  'connector-suspended': [/Laboratoire Central/],
  'connector-native-unchanged': [/Laboratoire Central/],
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

  // DH-USABILITY-001 §29. Each of these is a screen reached by doing something, and the gesture
  // is part of what is being reviewed: if the control that opens the panel is hard to find, the
  // capture is of a screen nobody reaches.
  'clinic-patient-new': [/^Nouveau patient$/],
  'clinic-order-handoff': [/^Suivi patient pour Fatouma Robleh$/],
  'clinic-results-detail': [/^Examiner/],
  // The P0 this ticket fixed: open the review, then step back to the version that was replaced.
  // The capture is the answer to "would a clinician know this is not the result that counts?"
  'clinic-results-superseded': [{ click: /^Examiner/ }, { click: /^Version 1/, role: 'tab' }],
  // Not the first "Examiner" on the page: the first order is new and unaccepted, and the screen
  // worth photographing is the one with the entry form on it.
  'lab-entry': [/^Examiner l'ordre de Ismail Warsama$/],
  'lab-release-inspect': [/^Examiner les résultats de Ismail Warsama$/],
  // The lab-side sibling of the same fact, two gestures in: the history, then the exact content
  // of the version that was superseded.
  'lab-version-history': [
    { click: /^Examiner les résultats de Ismail Warsama$/ },
    { click: /^Voir l'historique des versions$/ },
    { click: /^Voir le contenu exact$/ },
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
  // DH-USABILITY-001 §29: a page per scene. With the workflow scenes added, one long-lived page
  // taking full-page shots at deviceScaleFactor 2 exhausted the renderer partway through the
  // desktop pass ("Page crashed") -- a harness limit, not a finding about the application, and
  // one that would otherwise silently truncate the evidence.
  let page = null

  for (const scene of SCENES) {
    if (page) await page.close()
    page = await context.newPage()

    // Anything the page logs or fails to load is a finding, not noise: a font that
    // 404s or a thrown render error is exactly what this exercise is looking for.
    page.on('console', (msg) => {
      if (msg.type() === 'error') problems.push(`console ${viewport.name}/${scene}: ${msg.text()}`)
    })
    page.on('pageerror', (err) =>
      problems.push(`pageerror ${viewport.name}/${scene}: ${err.message}`),
    )
    page.on('requestfailed', (req) =>
      problems.push(
        `requestfailed ${viewport.name}/${scene}: ${req.url()} ${req.failure()?.errorText ?? ''}`,
      ),
    )

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
      // DH-USABILITY-001 §29: a control sitting outside a *scrollable* box is a different fact
      // from one sitting outside the window. The first is reachable -- a wide table of orders
      // scrolls sideways inside its own card -- and the second is not reachable at all. Reporting
      // them as one thing made the honest case and the broken case look identical, which is how a
      // check stops being read.
      const scrollable = (el) => {
        for (let a = el.parentElement; a; a = a.parentElement) {
          const overflow = getComputedStyle(a).overflowX
          if (overflow === 'auto' || overflow === 'scroll') return true
        }
        return false
      }
      const out = { offscreen: [], needsScrolling: [] }
      for (const el of document.querySelectorAll('button')) {
        const r = el.getBoundingClientRect()
        if (!(r.width > 0 && (r.right > width + 1 || r.left < -1))) continue
        const label = el.textContent?.trim() ?? '(sans texte)'
        if (scrollable(el)) out.needsScrolling.push(label)
        else out.offscreen.push(label)
      }
      return out
    })
    if (offscreen.offscreen.length)
      problems.push(
        `control off-screen ${viewport.name}/${scene}: ${offscreen.offscreen.join(', ')}`,
      )
    if (offscreen.needsScrolling.length)
      problems.push(
        `control reachable only by scrolling its container ${viewport.name}/${scene}: ${offscreen.needsScrolling.join(', ')}`,
      )

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
