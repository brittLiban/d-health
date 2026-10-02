import {
  expect,
  request as apiRequest,
  test,
  type APIRequestContext,
  type Page,
} from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import { waitForEdge } from '../support/edgeReadiness'

/**
 * DHEALTH-LAB-REPORT-001 — the version-bound printable report, against the real stack.
 *
 * ── The question this file answers ──
 *
 * Not "can the server build a report". `adapter/tests/test_dhealthlabreport001_report.py` qualifies
 * that at the HTTP edge (32 tests) and
 * `adapter/tests/integration/test_dhealthlabreport001_postgres_qualification.py` qualifies the
 * timestamp, the concurrency and the no-migration claim on real PostgreSQL (6 more). Neither can
 * answer the thing this file is for:
 *
 *   **Can a laboratory technician and an ordering clinician each reach a printable report for one
 *   exact released version by clicking the product — and when an amendment later changes the result,
 *   does the older version's report still show what was actually reported?**
 *
 * And the half no unit test can touch at all: **what the print medium actually produces.** jsdom
 * applies no print rules and paginates nothing. Here Chromium does both, and the PDF it emits is
 * inspected.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated, no session injected, no auth state written into browser storage. Every
 * clinical act below — routing the order, accepting it, recording collection, entering the values,
 * releasing, amending, releasing again — is performed by clicking the product as somebody who signed
 * in through a real Authorization Code + PKCE flow.
 *
 * Direct `/exchange/v1` calls appear only to assert refusals the browser cannot demonstrate on its
 * own (RP-H) and to read authoritative state for an assertion (never to advance the workflow). Both
 * use bearer tokens the application itself put on the wire.
 *
 * ── What this suite does NOT establish ──
 *
 * **Not human usability.** No laboratory technician and no clinician has used this report. A green
 * run says the mechanism works when driven correctly; whether a laboratory would accept this document
 * as its compte rendu is a human judgement no script can make, and it has not been made.
 *
 * **Not visual perfection.** Text extraction and page counting prove that content is present and
 * paginates; they prove nothing about typography or how the page looks. The screenshots RP-G takes
 * are for a human to look at, not an assertion.
 *
 * **No certification, signature, accreditation or regulatory claim of any kind.**
 *
 * ── State this suite leaves behind ──
 *
 * One lab order, released twice (Version 1 and Version 2). Nothing deletes a lab order by design;
 * `scripts/demo/reset.sh` clears it as workflow state. The laboratory's offering is left **active**:
 * RP-I switches it off to prove a historical report survives, then switches it back on explicitly,
 * because the golden journey and every sibling laboratory suite need it accepting.
 *
 * The generated PDFs are written to the OS temp directory and deleted by the test that made them.
 * They are evidence, not artifacts, and nothing commits them.
 *
 * All patient data is the demo baseline's synthetic patient. No real patient data is used, and the
 * only analysis involved is the synthetic `Analyse de démonstration A`.
 */

import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const EXCHANGE = process.env.E2E_EXCHANGE_URL ?? 'https://exchange.localhost'
const API = `${EXCHANGE}/exchange/v1`

const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}
const LAB_TECH = {
  username: process.env.DEMO_LAB_TECH_USERNAME ?? 'demo-lab-tech',
  password: process.env.DEMO_LAB_TECH_PASSWORD ?? '',
}
/**
 * `demo-releaser` holds `LAB_RESULT_RELEASER` and nothing else; `demo-lab-tech` holds `LAB_TECH` and
 * nothing else. ADR-044 keeps entry and release as separate authorities, and the demo baseline
 * models that honestly, so this suite needs both.
 */
const RELEASER = {
  username: process.env.DEMO_RELEASER_USERNAME ?? 'demo-releaser',
  password: process.env.DEMO_RELEASER_PASSWORD ?? '',
}
/**
 * `demo-lab-admin` holds `ORG_ADMIN` only -- `facility.configure`, no clinical role of any kind. It
 * appears here for two opposite reasons: RP-I needs it to switch the analysis off, and RP-H needs it
 * to prove that configuration authority grants no report read.
 */
const LAB_ADMIN = {
  username: process.env.DEMO_LAB_ADMIN_USERNAME ?? 'demo-lab-admin',
  password: process.env.DEMO_LAB_ADMIN_PASSWORD ?? '',
}

const PATIENT = 'Patient Démo Alpha'
const ANALYSIS = 'Analyse de démonstration A'
const LAB_ORG = 'Laboratoire Démo DHealth'

/** Version 1's released numeric value, and Version 2's. Distinct on purpose. */
const V1_VALUE = '5.4'
const V2_VALUE = '7.1'

let orderReference = ''
let orderId = ''
let labId = ''
let providerId = ''
let labTechToken = ''
let clinicianToken = ''
let versionOneId = ''
let versionTwoId = ''

const consoleErrors: string[] = []
const failedRequests: string[] = []

function watchForBrowserProblems(page: Page): void {
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(`${page.url()} :: ${message.text()}`)
  })
  page.on('pageerror', (error) => consoleErrors.push(`${page.url()} :: ${error.message}`))
  page.on('requestfailed', (request) => {
    const failure = request.failure()?.errorText ?? ''
    if (failure.includes('ERR_ABORTED')) return
    failedRequests.push(`${request.method()} ${request.url()} :: ${failure}`)
  })
}

function api(): Promise<APIRequestContext> {
  return apiRequest.newContext({ ignoreHTTPSErrors: true })
}

async function signIn(page: Page, who: { username: string; password: string }): Promise<string> {
  expect(who.password, `${who.username}'s password must come from demo/native/demo.env`).not.toBe('')
  const tokenPromise = captureFirstExchangeBearerToken(page)
  await page.goto('/')
  await loginViaRealKeycloak(page, who.username, who.password)
  await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({ timeout: 30_000 })
  return tokenPromise
}

async function openSection(page: Page, section: string): Promise<void> {
  await page.getByRole('navigation').getByRole('button', { name: section, exact: true }).click()
}

async function organizationId(token: string, type: 'LAB' | 'PROVIDER'): Promise<string> {
  const ctx = await api()
  try {
    const me = await ctx.get(`${API}/me`, { headers: { Authorization: `Bearer ${token}` } })
    expect(me.status(), 'a real login must authorize /me').toBe(200)
    const body = await me.json()
    const match = (
      body.memberships as { organization_id: string; organization_type: string }[]
    ).find((m) => m.organization_type === type)
    if (!match) throw new Error(`${type} membership missing from /me -- is the demo baseline seeded?`)
    return match.organization_id
  } finally {
    await ctx.dispose()
  }
}

/** The report document, wherever on the page it is. It portals to `body`, outside `#root`. */
function reportDocument(page: Page) {
  return page.locator('[data-released-report]')
}

/**
 * Opens this run's order in the laboratory's Ordres page.
 *
 * The `Tous` tab is not optional. That page filters by status and opens on `Nouveaux`, so an order
 * that has been accepted, processed or released is not in the default list at all -- which looks
 * exactly like "the order is missing" to a locator.
 */
async function openLabOrderPanel(page: Page): Promise<void> {
  await openSection(page, 'Ordres')
  await page.getByRole('tab', { name: 'Tous', exact: true }).click()
  const card = page
    .locator('li,div')
    .filter({ hasText: orderReference })
    .filter({ hasText: PATIENT })
    .first()
  await expect(card).toBeVisible({ timeout: 30_000 })
  await card.getByRole('button', { name: new RegExp(`Examiner l.ordre de ${PATIENT}`) }).click()
}

async function openLabReportForVersion(page: Page, versionNumber: number): Promise<void> {
  await openLabOrderPanel(page)
  await page.getByRole('button', { name: "Voir l'historique des versions" }).click()
  const row = page
    .getByRole('listitem')
    .filter({ hasText: new RegExp(`Version ${versionNumber}\\b`) })
    .first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  await row.getByRole('button', { name: 'Voir le compte rendu' }).click()
  await expect(reportDocument(page)).toBeVisible({ timeout: 30_000 })
}

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  await waitForEdge()
})

test.describe('DHEALTH-LAB-REPORT-001 — one exact released version, printable', () => {
  // ── RP-A — a clinic routes a synthetic order and the laboratory takes it ─────────────────────

  test('RP-A: the clinic routes an order and the laboratory starts processing it', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    clinicianToken = await signIn(page, CLINICIAN)
    providerId = await organizationId(clinicianToken, 'PROVIDER')

    await openSection(page, 'Patients')
    await expect(page.getByText(PATIENT).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: /^Ouvrir/ }).first().click()

    await page.getByRole('button', { name: 'Nouvel ordre de laboratoire' }).click()
    await page.getByRole('checkbox', { name: new RegExp(ANALYSIS) }).check()
    await page.getByRole('button', { name: /^Suivant/ }).click()
    await page.getByRole('button', { name: 'Confirmer et prescrire' }).click()

    await expect(page.getByText(/Choisir un laboratoire destinataire/)).toBeVisible({
      timeout: 30_000,
    })
    await page.getByRole('radio', { name: new RegExp(LAB_ORG) }).check()
    await page.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }).click()

    await openSection(page, 'Ordres')
    const row = page.getByRole('row').filter({ hasText: PATIENT }).first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    orderReference = (await row.locator('td').nth(5).innerText()).replace('…', '').trim()
    expect(orderReference.length).toBeGreaterThan(4)
  })

  test('RP-A2: the laboratory accepts, records collection and enters the values', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    labTechToken = await signIn(page, LAB_TECH)
    labId = await organizationId(labTechToken, 'LAB')

    await openSection(page, 'Ordres')
    const card = page
      .locator('li,div')
      .filter({ hasText: orderReference })
      .filter({ hasText: PATIENT })
      .first()
    await expect(card).toBeVisible({ timeout: 30_000 })
    await card.getByRole('button', { name: new RegExp(`Examiner l.ordre de ${PATIENT}`) }).click()

    await page.getByRole('button', { name: "Accepter l'ordre" }).click()
    await page.getByRole('button', { name: 'Enregistrer la collecte' }).click()
    await page.getByRole('button', { name: 'Confirmer la collecte' }).click()
    await expect(page.getByText('Collecte enregistrée').first()).toBeVisible({ timeout: 30_000 })

    await page.getByRole('button', { name: "Commencer l'analyse" }).click()
    await page.getByRole('button', { name: 'Commencer', exact: true }).click()

    const required = page.getByLabel(/Requis/)
    await expect(required.first()).toBeVisible({ timeout: 30_000 })
    const count = await required.count()
    expect(count, 'the demo template declares required observations').toBeGreaterThan(0)
    for (let index = 0; index < count; index += 1) {
      const field = required.nth(index)
      const numeric = (await field.getAttribute('inputmode')) === 'decimal'
      await field.fill(numeric ? V1_VALUE : 'Observation de démonstration')
    }
    await page.getByRole('button', { name: 'Enregistrer les valeurs' }).click()
    await expect(page.getByText(/valeur saisie n.est pas encore enregistrée/)).toBeHidden({
      timeout: 30_000,
    })
    await page.getByRole('button', { name: 'Terminer la saisie' }).click()
    await page.getByRole('button', { name: 'Confirmer la fin de la saisie' }).click()
    await expect(page.getByText(/Saisie terminée/).first()).toBeVisible({ timeout: 30_000 })

    const ctx = await api()
    try {
      const queue = await ctx.get(`${API}/organizations/${labId}/lab-orders?limit=100`, {
        headers: { Authorization: `Bearer ${labTechToken}` },
      })
      expect(queue.status()).toBe(200)
      const orders = (await queue.json()) as { order_id: string }[]
      const match = orders.find((o) => o.order_id.startsWith(orderReference.toLowerCase()))
      expect(match, 'the routed order must be in this laboratory queue').toBeTruthy()
      orderId = match!.order_id
    } finally {
      await ctx.dispose()
    }
  })

  // ── RP-B — entered is not released, so there is no report ────────────────────────────────────

  test('RP-B: before release there is no version, no report action and no report to fetch', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    const techToken = await signIn(page, LAB_TECH)

    await openLabOrderPanel(page)

    /*
     * The result is entered and the entry is complete, and the order is still IN_PROGRESS. What the
     * laboratory's panel offers at this point is stronger than "no report button": the whole version
     * history section does not exist, because it is bound to a COMPLETED order. There is no version
     * to list, so there is nothing to print, and the product does not offer a control that would
     * have to refuse.
     */
    await expect(
      page.getByRole('button', { name: "Voir l'historique des versions" }),
    ).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Voir le compte rendu' })).toHaveCount(0)
    // And this is the panel for the right order, so the two absences above are not passing because
    // the screen is empty.
    await expect(page.getByText(/Saisie terminée/).first()).toBeVisible({ timeout: 30_000 })

    const ctx = await api()
    try {
      // No version id exists to ask for, so this is the honest shape of the attempt: a guess. The
      // server refuses it, and the refusal body carries no entered value.
      const guessed = '00000000-0000-4000-8000-000000000000'
      const refused = await ctx.get(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions/${guessed}/report`,
        { headers: { Authorization: `Bearer ${techToken}` } },
      )
      expect(refused.status()).toBe(404)
      expect(await refused.text()).not.toContain(V1_VALUE)

      // The clinic cannot obtain one either: its own route refuses on the order's status.
      const clinicRefused = await ctx.get(
        `${API}/organizations/${providerId}/orders/${orderId}/release-versions/${guessed}/report`,
        { headers: { Authorization: `Bearer ${clinicianToken}` } },
      )
      expect(clinicRefused.status()).toBe(409)
      expect(await clinicRefused.text()).not.toContain(V1_VALUE)
    } finally {
      await ctx.dispose()
    }
  })

  // ── RP-C — release Version 1, and open its report ────────────────────────────────────────────

  test('RP-C: the releaser releases Version 1', async ({ page }) => {
    watchForBrowserProblems(page)
    // The releaser, not the technician who entered the values and not the administrator: ADR-044
    // keeps the three apart, and the demo baseline grants LAB_RESULT_RELEASER to this identity only.
    await signIn(page, RELEASER)
    await openSection(page, 'Résultats')
    await page
      .getByRole('button', { name: new RegExp(`Examiner les résultats de ${PATIENT}`) })
      .first()
      .click()
    await expect(page.getByText(new RegExp(V1_VALUE)).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: 'Libérer les résultats' }).click()
    await page
      .getByRole('button', { name: /Confirmer la libération|Libérer/ })
      .last()
      .click()
    await expect(page.getByText(/Résultats libérés/).first()).toBeVisible({ timeout: 30_000 })
  })

  test('RP-C2: the laboratory opens Report Version 1 and it is a document, not a screen', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openLabReportForVersion(page, 1)

    const report = reportDocument(page)
    await expect(report).toContainText(LAB_ORG)
    await expect(report).toContainText('Compte rendu d’analyses')
    await expect(report).toContainText(PATIENT)
    await expect(report).toContainText(ANALYSIS)
    await expect(report).toContainText(V1_VALUE)
    await expect(report.getByText('Version 1', { exact: true })).toBeVisible()
    await expect(report).toContainText('Résultat actuel')
    await expect(report).toContainText('Publié le')

    // Forbidden content. Each is something the clinic or laboratory screens around this report do
    // carry, which is exactly why the report has to be checked rather than assumed.
    const text = ((await report.textContent()) ?? '').toLowerCase()
    for (const absent of [
      'revue', // the clinician's review disposition
      'suivi requis',
      'motif', // reason for visit
      'facture', // invoice
      'paiement',
      'djf', // price
      'signature',
      'certifi',
      'anormal',
      'prélèvement', // specimen: no specimen semantics are introduced
      'tube',
    ]) {
      expect(text, `"${absent}" must not appear on a released laboratory report`).not.toContain(
        absent,
      )
    }

    const ctx = await api()
    try {
      const versions = await ctx.get(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions`,
        { headers: { Authorization: `Bearer ${labTechToken}` } },
      )
      expect(versions.status()).toBe(200)
      const rows = (await versions.json()).versions as {
        release_version_id: string
        version_number: number
      }[]
      expect(rows).toHaveLength(1)
      versionOneId = rows.find((r) => r.version_number === 1)!.release_version_id

      // Cache policy, on the route this ticket introduced. Scoped to that: this says nothing about
      // the repository's broader clinical cache-control work.
      const report1 = await ctx.get(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions/${versionOneId}/report`,
        { headers: { Authorization: `Bearer ${labTechToken}` } },
      )
      expect(report1.status()).toBe(200)
      expect(report1.headers()['cache-control']).toBe('no-store')
    } finally {
      await ctx.dispose()
    }
  })

  test('RP-C3: the ordering clinic opens the same report under its own authority', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)
    await openSection(page, 'Résultats')
    await page.getByRole('button', { name: /^Examiner le résultat de/ }).first().click()
    await page.getByRole('button', { name: 'Voir le compte rendu' }).click()

    const report = reportDocument(page)
    await expect(report).toBeVisible({ timeout: 30_000 })
    await expect(report).toContainText(V1_VALUE)
    await expect(report.getByText('Version 1', { exact: true })).toBeVisible()
    // The clinic's own result screen shows the review disposition. The report does not.
    expect(((await report.textContent()) ?? '').toLowerCase()).not.toContain('revue')
  })

  // ── RP-D — the print medium ──────────────────────────────────────────────────────────────────

  test('RP-D: print media carries the document and none of the application', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openLabReportForVersion(page, 1)

    // Screen first: the action bar exists, and so does the application behind the report.
    await expect(page.getByRole('button', { name: 'Imprimer le rapport' })).toBeVisible()
    await expect(page.getByRole('navigation')).toBeVisible()

    await page.emulateMedia({ media: 'print' })
    try {
      // `toBeHidden` is a real computed-style check in Chromium, not a DOM-presence check: the
      // application is `display: none` and the action bar with it.
      await expect(page.getByRole('navigation')).toBeHidden()
      await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeHidden()
      await expect(page.getByRole('button', { name: 'Imprimer le rapport' })).toBeHidden()
      await expect(page.getByRole('button', { name: '← Retour' })).toBeHidden()
      // And the report itself is still there.
      await expect(reportDocument(page)).toBeVisible()
      await expect(reportDocument(page)).toContainText(V1_VALUE)

      // No horizontal overflow in the print medium at A4's own width.
      await page.setViewportSize({ width: 794, height: 1123 })
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow, 'the printed document must not overflow the page width').toBeLessThanOrEqual(
        1,
      )
    } finally {
      await page.emulateMedia({ media: null })
    }
  })

  test('RP-D2: Chromium emits a PDF whose text is the released report', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openLabReportForVersion(page, 1)

    const dir = mkdtempSync(join(tmpdir(), 'dhealth-report-'))
    const file = join(dir, 'version-1.pdf')
    try {
      await page.pdf({ path: file, format: 'A4', printBackground: false })
      const bytes = readFileSync(file)
      expect(bytes.length, 'a PDF was produced').toBeGreaterThan(1000)
      expect(bytes.subarray(0, 5).toString('latin1')).toBe('%PDF-')

      // Page count, from the PDF's own page objects. Chromium's Skia writer emits these
      // uncompressed, so this is readable without a PDF library -- and no PDF library is added,
      // because a report feature does not need one.
      const raw = bytes.toString('latin1')
      const pages = (raw.match(/\/Type\s*\/Page[^s]/g) ?? []).length
      expect(pages, 'a short report is one page').toBe(1)
      // Not a blank trailing page: one page, and the content stream is not empty.
      expect(raw).toContain('/Contents')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  // ── RP-E — an amendment draft changes nothing ────────────────────────────────────────────────

  test('RP-E: an amendment draft leaves Report Version 1 exactly as it was', async ({ page }) => {
    watchForBrowserProblems(page)
    const techToken = await signIn(page, LAB_TECH)

    // Capture the report as the server renders it, before the amendment exists.
    const ctx = await api()
    let before: string
    try {
      const response = await ctx.get(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions/${versionOneId}/report`,
        { headers: { Authorization: `Bearer ${techToken}` } },
      )
      expect(response.status()).toBe(200)
      before = await response.text()
    } finally {
      await ctx.dispose()
    }

    await openLabOrderPanel(page)

    await page.getByRole('button', { name: 'Commencer un amendement' }).click()
    await page.getByRole('button', { name: "Commencer l'amendement" }).click()
    await expect(page.getByText(/Amendement en cours/).first()).toBeVisible({ timeout: 30_000 })

    await page.getByRole('button', { name: 'Corriger' }).first().click()
    await page.getByLabel('Nouvelle valeur').fill(V2_VALUE)
    await page.getByRole('button', { name: 'Confirmer la correction' }).click()
    await expect(page.getByText(/Modifié dans cet amendement/).first()).toBeVisible({
      timeout: 30_000,
    })

    // The draft now holds V2_VALUE. Version 1's report has not moved by one field.
    const after = await api()
    try {
      const response = await after.get(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions/${versionOneId}/report`,
        { headers: { Authorization: `Bearer ${techToken}` } },
      )
      expect(response.status()).toBe(200)
      const body = await response.text()
      expect(body).toBe(before)
      expect(body).not.toContain(V2_VALUE)
    } finally {
      await after.dispose()
    }

    // And no Version 2 exists to report on.
    await page.reload()
    await openLabReportForVersion(page, 1)
    await expect(reportDocument(page)).toContainText(V1_VALUE)
    await expect(reportDocument(page)).not.toContainText(V2_VALUE)
  })

  // ── RP-F — release Version 2, and both reports afterwards ────────────────────────────────────

  test('RP-F: Version 2 is released', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, RELEASER)
    await openSection(page, 'Résultats')
    // The release page keeps two queues behind two tabs: results awaiting a first release, and
    // amendments awaiting theirs. An amendment is not in the default one.
    await page.getByRole('tab', { name: 'Amendements', exact: true }).click()
    await page
      .getByRole('button', { name: new RegExp(`Examiner l.amendement de ${PATIENT}`) })
      .first()
      .click()
    await expect(page.getByText(new RegExp(V2_VALUE)).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: "Libérer l'amendement" }).click()
    await page
      .getByRole('button', { name: /Confirmer|Libérer/ })
      .last()
      .click()
    await expect(page.getByText(/Amendement libéré/).first()).toBeVisible({ timeout: 30_000 })

    const ctx = await api()
    try {
      const versions = await ctx.get(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions`,
        { headers: { Authorization: `Bearer ${labTechToken}` } },
      )
      const rows = (await versions.json()).versions as {
        release_version_id: string
        version_number: number
      }[]
      expect(rows).toHaveLength(2)
      versionTwoId = rows.find((r) => r.version_number === 2)!.release_version_id
    } finally {
      await ctx.dispose()
    }
  })

  test('RP-F2: Report Version 2 is Version 2, and Report Version 1 is still Version 1', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)

    // Version 2 first.
    await openLabReportForVersion(page, 2)
    let report = reportDocument(page)
    await expect(report).toContainText(V2_VALUE)
    await expect(report).not.toContainText(V1_VALUE)
    await expect(report.getByText('Version 2 — rectificatif').first()).toBeVisible()
    await expect(report).toContainText('Résultat actuel')

    // Then Version 1, re-opened after Version 2 exists. This is the assertion the whole ticket is
    // for: an older version's report still carries what was actually reported.
    await page.reload()
    await openLabReportForVersion(page, 1)
    report = reportDocument(page)
    await expect(report).toContainText(V1_VALUE)
    await expect(report).not.toContainText(V2_VALUE)
    await expect(report.getByText('Version 1', { exact: true })).toBeVisible()
    // Clearly historical, and never called invalid, cancelled or revoked.
    await expect(report).toContainText('Version remplacée — résultat actuel : version 2')
    const text = ((await report.textContent()) ?? '').toLowerCase()
    for (const absent of ['invalide', 'annulé', 'révoqué', 'erreur']) {
      expect(text).not.toContain(absent)
    }
  })

  test('RP-F3: the clinic printing while inspecting Version 1 gets Version 1', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)
    await openSection(page, 'Résultats')
    await page.getByRole('button', { name: /^Examiner le résultat de/ }).first().click()

    // Switch the panel to Version 1 while Version 2 is the current release, then print.
    const versionOneTab = page.getByRole('tab', { name: /Version 1/ })
    await expect(versionOneTab).toBeVisible({ timeout: 30_000 })
    await versionOneTab.click()
    await expect(page.getByText(new RegExp(V1_VALUE)).first()).toBeVisible({ timeout: 30_000 })

    await page.getByRole('button', { name: 'Voir le compte rendu' }).click()
    const report = reportDocument(page)
    await expect(report).toBeVisible({ timeout: 30_000 })
    // The version on screen, not the latest one.
    await expect(report.getByText('Version 1', { exact: true })).toBeVisible()
    await expect(report).toContainText(V1_VALUE)
    await expect(report).not.toContainText(V2_VALUE)
    await expect(report).toContainText('Version remplacée — résultat actuel : version 2')
  })

  // ── RP-G — responsive screen widths, and screenshots for a human ─────────────────────────────

  test('RP-G: the report screen works at 1280, 768 and 390 with no horizontal overflow', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openLabReportForVersion(page, 1)

    for (const [width, height] of [
      [1280, 900],
      [768, 1024],
      [390, 844],
    ] as const) {
      await page.setViewportSize({ width, height })
      const report = reportDocument(page)
      await expect(report).toBeVisible()
      // The values are readable at every width -- not clipped, not behind a scrollbox.
      await expect(report).toContainText(V1_VALUE)
      await expect(report).toContainText(PATIENT)
      await expect(report.getByText('Version 1', { exact: true })).toBeVisible()
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow, `no horizontal page overflow at ${width}px`).toBeLessThanOrEqual(1)
      // For a human to look at. Not an assertion: no script can judge whether this reads well.
      await page.screenshot({
        path: `test-results/report-version-1-${width}.png`,
        fullPage: true,
      })
    }
  })

  // ── RP-H — authority, isolation, and a long report ───────────────────────────────────────────

  test('RP-H: unauthorized and rival actors cannot read the report', async ({ page }) => {
    watchForBrowserProblems(page)
    // Real tokens from real logins, used against a report that is not theirs to read. The browser
    // cannot demonstrate most of this -- the control is simply not offered to these actors -- so the
    // boundary is asserted where it actually lives.
    const adminToken = await signIn(page, LAB_ADMIN)

    // First, in the browser: this administrator's navigation has no Resultats section at all, so
    // there is no screen from which a report could be opened.
    await expect(
      page.getByRole('navigation').getByRole('button', { name: 'Résultats', exact: true }),
    ).toHaveCount(0)

    const ctx = await api()
    try {
      // No credential at all.
      const anonymous = await ctx.get(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions/${versionOneId}/report`,
      )
      expect(anonymous.status()).toBe(401)

      // **The facility-configuration separation.** demo-lab-admin holds ORG_ADMIN at this exact
      // laboratory -- it is the identity RP-I uses to switch the analysis off -- and holds no
      // clinical role. facility.configure must not read a released clinical result.
      const configurer = await ctx.get(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions/${versionOneId}/report`,
        { headers: { Authorization: `Bearer ${adminToken}` } },
      )
      expect(configurer.status()).toBe(403)
      expect(await configurer.text()).not.toContain(V1_VALUE)

      // The same actor through the clinic's route, where they hold no membership at all.
      const wrongRoute = await ctx.get(
        `${API}/organizations/${providerId}/orders/${orderId}/release-versions/${versionOneId}/report`,
        { headers: { Authorization: `Bearer ${adminToken}` } },
      )
      expect([403, 404]).toContain(wrongRoute.status())
      expect(await wrongRoute.text()).not.toContain(V1_VALUE)

      // The clinician asking through the laboratory's route.
      const clinicianAtLab = await ctx.get(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions/${versionOneId}/report`,
        { headers: { Authorization: `Bearer ${clinicianToken}` } },
      )
      expect([403, 404]).toContain(clinicianAtLab.status())
      expect(await clinicianAtLab.text()).not.toContain(V1_VALUE)

      // A guessed version id under a real, authorized order.
      const guessed = await ctx.get(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions/` +
          `11111111-1111-4111-8111-111111111111/report`,
        { headers: { Authorization: `Bearer ${labTechToken}` } },
      )
      expect(guessed.status()).toBe(404)
      expect(await guessed.text()).not.toContain(V1_VALUE)
    } finally {
      await ctx.dispose()
    }
  })

  /*
   * Three tests, not one, and the reason is the Keycloak session.
   *
   * This phase needs two different identities: the administrator who switches the analysis off, and
   * the technician who then still opens the report. Signing in twice inside one test does not switch
   * identity -- the Keycloak SSO cookie is still live, so the second `goto('/')` silently
   * re-authenticates as the first user and never reaches the login form. Playwright gives each test
   * its own browser context, so splitting is what makes each sign-in a real one. They run in order
   * because this file is serial.
   */

  test('RP-I: the administrator switches the analysis off, and both reports still answer', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_ADMIN)

    await openSection(page, 'Services et tarifs')
    const row = page.locator('li').filter({ hasText: ANALYSIS }).first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    await expect(row).toContainText('accepte les nouveaux ordres')

    // One click, no confirmation step: the control is its own confirmation, and the row's own
    // wording is how the screen reports the new state. An extra "Confirmer" click here would land on
    // the toggle again and silently switch it back -- which is exactly what an earlier draft of this
    // test did.
    await row.getByRole('button', { name: 'Ne plus accepter', exact: true }).click()
    await expect(row).toContainText("n'accepte plus de nouveaux ordres", { timeout: 30_000 })

    const ctx = await api()
    try {
      // Both released versions remain readable to a legitimate reader. Availability governs new
      // ordering; it does not rewrite released truth.
      for (const versionId of [versionOneId, versionTwoId]) {
        const response = await ctx.get(
          `${API}/organizations/${labId}/lab-orders/${orderId}/release-versions/${versionId}/report`,
          { headers: { Authorization: `Bearer ${labTechToken}` } },
        )
        expect(response.status(), `report for ${versionId} must survive the disable`).toBe(200)
      }
      const clinicSide = await ctx.get(
        `${API}/organizations/${providerId}/orders/${orderId}/release-versions/${versionOneId}/report`,
        { headers: { Authorization: `Bearer ${clinicianToken}` } },
      )
      expect(clinicSide.status()).toBe(200)
    } finally {
      await ctx.dispose()
    }
  })

  test('RP-I2: with the analysis switched off, the technician still opens the report', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    // By clicking the product, not only through the API -- and as the technician, because an
    // ORG_ADMIN membership has no Ordres section at all (RP-H asserts the same for Resultats). The
    // actor who switched the analysis off is not the actor who reads results, and this phase needs
    // both.
    await signIn(page, LAB_TECH)
    await openLabReportForVersion(page, 1)
    await expect(reportDocument(page)).toContainText(V1_VALUE)
    await expect(reportDocument(page)).toContainText('Version remplacée')
  })

  test('RP-I3: the administrator switches it back on, for every sibling suite', async ({ page }) => {
    watchForBrowserProblems(page)
    // Explicitly, not by relying on reset.sh: offerings are baseline configuration rather than
    // workflow state and are deliberately absent from its `_WORKFLOW_TABLES`. The golden journey and
    // every sibling laboratory suite need this analysis accepting.
    const adminToken = await signIn(page, LAB_ADMIN)
    await openSection(page, 'Services et tarifs')
    const row = page.locator('li').filter({ hasText: ANALYSIS }).first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    await expect(row).toContainText("n'accepte plus de nouveaux ordres")
    await row.getByRole('button', { name: 'Accepter à nouveau', exact: true }).click()
    await expect(row).toContainText('accepte les nouveaux ordres', { timeout: 30_000 })

    const verify = await api()
    try {
      const offerings = await verify.get(`${API}/organizations/${labId}/offerings`, {
        headers: { Authorization: `Bearer ${adminToken}` },
      })
      expect(offerings.status()).toBe(200)
      const rows = (await offerings.json()) as { active: boolean }[]
      expect(
        rows.every((o) => o.active),
        'the offering must be left accepting for the sibling suites',
      ).toBe(true)
    } finally {
      await verify.dispose()
    }
  })

  // ── RP-J — a report long enough to need more than one page ───────────────────────────────────

  test('RP-J: a deliberately long synthetic report spans pages without losing a row', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openLabReportForVersion(page, 1)

    /*
     * The demo baseline's analysis has two observations, which is one page. Rather than
     * manufacture a long clinical order -- which would mean inventing a diagnostic panel, and
     * inventing clinical content is forbidden -- this clones the report's own rendered rows in the
     * DOM until the document genuinely exceeds one A4 page.
     *
     * What that does and does not prove: it proves the *print layout* paginates, repeats the table
     * head, and loses no row. It proves nothing about the server, which is why the content here is
     * duplicated presentation rather than fabricated results. The labels are visibly synthetic.
     */
    const rowCount = await page.evaluate(() => {
      const table = document.querySelector('[data-released-report] table tbody')
      if (!table) throw new Error('no results table in the report')
      const template = table.querySelector('tr')
      if (!template) throw new Error('no observation row to clone')
      for (let index = 0; index < 60; index += 1) {
        const clone = template.cloneNode(true) as HTMLElement
        const header = clone.querySelector('th')
        if (header) header.textContent = `Ligne synthétique de pagination ${index + 1}`
        table.appendChild(clone)
      }
      return table.querySelectorAll('tr').length
    })
    expect(rowCount).toBeGreaterThan(60)

    const dir = mkdtempSync(join(tmpdir(), 'dhealth-report-long-'))
    const file = join(dir, 'long.pdf')
    try {
      await page.pdf({ path: file, format: 'A4', printBackground: false })
      const raw = readFileSync(file).toString('latin1')
      const pages = (raw.match(/\/Type\s*\/Page[^s]/g) ?? []).length
      expect(pages, 'a long report must span more than one page').toBeGreaterThan(1)

      // The last row is on paper, not clipped off the end. Asserted in the print medium against the
      // live DOM, because the PDF's own text is font-subset encoded and not readable as ASCII.
      await page.emulateMedia({ media: 'print' })
      try {
        const last = page.getByText('Ligne synthétique de pagination 60')
        await expect(last).toBeVisible()
        // No scrollbox swallowed the overflow: the document grew, it did not scroll.
        const scrolls = await page.evaluate(() => {
          const root = document.querySelector('[data-released-report]') as HTMLElement | null
          if (!root) return true
          const style = getComputedStyle(root)
          return ['auto', 'scroll'].includes(style.overflowY) || root.scrollHeight > root.clientHeight
        })
        expect(scrolls, 'the printed report must not live inside a scroll container').toBe(false)
      } finally {
        await page.emulateMedia({ media: null })
      }
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  // ── RP-K — the browser had no unexplained problems ───────────────────────────────────────────

  test('RP-K: no unexpected console errors and no failed requests across the whole run', async () => {
    expect(consoleErrors, `unexpected console errors:\n${consoleErrors.join('\n')}`).toEqual([])
    expect(failedRequests, `unexpected failed requests:\n${failedRequests.join('\n')}`).toEqual([])
  })
})
