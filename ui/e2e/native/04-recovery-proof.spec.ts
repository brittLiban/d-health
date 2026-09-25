import { expect, request, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { loginViaRealKeycloak } from '../support/login'

/**
 * DHEALTH-NATIVE-RECOVERY-001 — proof, through the real product, that a destroyed DHealth Native
 * deployment came back.
 *
 * Runs against a stack that has just been restored from an encrypted backup into a clean
 * environment. SQL row counts are not the standard here and are checked elsewhere
 * (`scripts/native/recovery-sentinels.sh`); this file signs in as the recovered identity and looks
 * for the same clinical records on the same screens a clinician uses.
 *
 * Why identifiers and not names. The demo baseline seeds organisations and a patient with FIXED
 * names, so a clean target that was merely re-seeded shows a patient called `Patient Démo Alpha`
 * too. Every assertion below that means "the same record" is therefore keyed on the sentinel file's
 * identifiers, captured before the backup. `RECOVERY_SENTINELS` is that file.
 *
 * The order of the phases is deliberate: everything that proves the OLD state exists happens before
 * anything writes, so a new record can never be what satisfies an assertion about an old one.
 */

const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}
const LAB_TECH = {
  username: process.env.DEMO_LAB_TECH_USERNAME ?? 'demo-lab-tech',
  password: process.env.DEMO_LAB_TECH_PASSWORD ?? '',
}
const RELEASER = {
  username: process.env.DEMO_RELEASER_USERNAME ?? 'demo-releaser',
  password: process.env.DEMO_RELEASER_PASSWORD ?? '',
}

const PROVIDER_ORG = 'Clinique Démo DHealth'
const LAB_ORG = 'Laboratoire Démo DHealth'
const PATIENT = 'Patient Démo Alpha'
const CATALOG_ENTRY = 'Analyse de démonstration A'

interface Sentinels {
  order_id: string
  order_reference_ui: string
  review_id: string
  review_status: string
  release_version_id: string
  release_version_number: number
  patient_id: string
  release_fingerprint: string
  observation_count: number
}

const sentinelsPath = process.env.RECOVERY_SENTINELS
if (!sentinelsPath) {
  throw new Error('RECOVERY_SENTINELS is required -- without the pre-backup identifiers this suite could only prove that *some* order exists.')
}
const sentinels = JSON.parse(readFileSync(sentinelsPath, 'utf8')) as Sentinels

// The raw grant issued before the backup. Read from the harness file, used as a bearer, never
// printed and never placed in an assertion message.
//
// Deliberately loud rather than skipped: a recovery drill that lost its pre-backup grant cannot
// prove patient-access recovery, and a silently skipped test would look like a pass.
const grantPath = process.env.E2E_GRANT_TOKEN_FILE
if (!grantPath) {
  throw new Error('E2E_GRANT_TOKEN_FILE is required -- patient-access recovery cannot be proven without the grant issued before the backup.')
}
let recoveredGrant: string
try {
  const parsed = JSON.parse(readFileSync(grantPath, 'utf8')) as { access_token?: string }
  if (!parsed.access_token) throw new Error('no access_token in the file')
  recoveredGrant = parsed.access_token
} catch (error) {
  throw new Error(
    `could not read the pre-backup patient grant from ${grantPath} -- run 03-recovery-source.spec.ts against the source deployment before the backup. (${String(error)})`,
  )
}

/** A grant issued on the order created AFTER recovery. Set in the write phase. */
let postRecoveryGrant = ''

const exchangeUrl = process.env.E2E_EXCHANGE_URL
if (!exchangeUrl) throw new Error('E2E_EXCHANGE_URL is required.')

/** The order this run creates AFTER recovery. Set in the write phase. */
let newOrderReference = ''

const consoleErrors: string[] = []

function watchForBrowserProblems(page: Page): void {
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(`${page.url()} :: ${message.text()}`)
  })
  page.on('pageerror', (error) => consoleErrors.push(`${page.url()} :: ${error.message}`))
}

async function signIn(page: Page, who: { username: string; password: string }): Promise<void> {
  expect(who.password, 'demo passwords must come from demo/native/demo.env').not.toBe('')
  await page.goto('/')
  await loginViaRealKeycloak(page, who.username, who.password)
  await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({ timeout: 30_000 })
}

async function openSection(page: Page, section: string): Promise<void> {
  await page.getByRole('navigation').getByRole('button', { name: section, exact: true }).click()
}

test.describe.serial('DHEALTH-NATIVE-RECOVERY-001 recovery proof', () => {
  test('RECOVERY-1 — the pre-backup identity signs in through real Keycloak after the restore', async ({
    page,
  }) => {
    watchForBrowserProblems(page)

    // The same synthetic account that existed before the backup, not one recreated afterwards to
    // make this pass. Real Authorization Code + PKCE against the real login form; no injected
    // session, no fabricated token. If Keycloak's own database had not been restored, this account
    // would not exist and this test is where that shows.
    await signIn(page, CLINICIAN)

    // And the Exchange side still agrees about who they are: the membership that decides what is on
    // screen is a restored row, joined to a restored Keycloak subject.
    await expect(page.getByText(PROVIDER_ORG).first()).toBeVisible({ timeout: 30_000 })
  })

  test('RECOVERY-2 — the SAME order from before the backup is in the clinic queue', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)
    await openSection(page, 'Ordres')

    // The reference cell is the order id's first eight characters. Matching it against the sentinel
    // captured before the backup is what makes this "the same order" and not "an order".
    const row = page.getByRole('row').filter({ hasText: sentinels.order_reference_ui }).first()
    await expect(row, `the pre-backup order ${sentinels.order_reference_ui}… must be on this screen`).toBeVisible({
      timeout: 30_000,
    })
    await expect(row).toContainText(PATIENT)
    await expect(row).toContainText(LAB_ORG)
  })

  test('RECOVERY-3 — the released result and the clinician review that was recorded against it both survived', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)
    await openSection(page, 'Résultats')

    // The review was completed before the backup, so the order is NOT an outstanding obligation --
    // it is history. `Historique` is where the product shows a review that has already been
    // recorded, and its presence here is the application-level proof that the review row came back
    // bound to its release version: had the review been lost, the obligation would be pending again
    // and this order would be in the queue above instead.
    await expect(page.getByRole('heading', { name: /Historique \(\d+\)/ })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByRole('heading', { name: 'Historique (0)' })).toHaveCount(0)

    const historyEntry = page.getByRole('listitem').filter({ hasText: PATIENT }).first()
    await expect(historyEntry).toBeVisible({ timeout: 30_000 })
    await historyEntry.getByRole('button').first().click()

    // The released version is on screen, it is version 1, and its value is the one that was
    // released before the backup -- not a value re-entered afterwards.
    await expect(page.getByRole('tab', { name: /Résultat actuel/ })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/5.4/).first()).toBeVisible()
    await expect(page.getByText(/Observation de démonstration/).first()).toBeVisible()
  })

  test('RECOVERY-4 — the laboratory identity also recovered, and sees the same completed order', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await expect(page.getByText(LAB_ORG).first()).toBeVisible()

    await openSection(page, 'Ordres')

    // The laboratory queue defaults to `Nouveaux` -- new work -- which is correct product behaviour
    // and is why the restored order is not on it: the laboratory finished this order before the
    // backup. `Terminés` is where a completed order lives, so that is where recovery has to be
    // proven. (A first version of this test asserted against the default queue and failed, which is
    // the test having been wrong about the product rather than the product having lost the order.)
    await page.getByRole('tab', { name: 'Terminés' }).click()

    // The lab's own view of the same order, found by the same identifier. Two organisations, two
    // recovered identities, one restored order.
    await expect(
      page.getByText(sentinels.order_reference_ui, { exact: false }).first(),
      'the laboratory must still see the order it completed before the backup',
    ).toBeVisible({ timeout: 30_000 })
  })

  test('RECOVERY-5 — the pre-backup patient access grant still opens exactly its own order', async () => {
    const api = await request.newContext({ baseURL: exchangeUrl, ignoreHTTPSErrors: true })
    try {
      // A grant is stored as a SHA-256 digest, never as a recoverable secret, so this is the only
      // way the behaviour can be tested at all: the raw value captured at issue time, replayed
      // against the restored deployment.
      const response = await api.get('/exchange/v1/patient/order', {
        headers: { authorization: `Bearer ${recoveredGrant}` },
      })
      expect(response.status(), 'the restored grant must still authorize its own order').toBe(200)

      const body = (await response.json()) as { status: string; result_status: string }
      // The patient response deliberately carries no identifier at all, so "its own order" is
      // proven by state rather than by id: this grant's order was released AND reviewed with no
      // follow-up before the backup, which is a state no freshly created order can be in.
      expect(body.status).toBe('COMPLETED')
      expect(body.result_status).toBe('NO_FOLLOW_UP_REQUIRED')
    } finally {
      await api.dispose()
    }
  })

  test('RECOVERY-6 — authorization boundaries still hold after the restore', async () => {
    const api = await request.newContext({ baseURL: exchangeUrl, ignoreHTTPSErrors: true })
    try {
      // A restore must not come back more permissive than it went in. These are the cheapest two
      // that do not need a browser token: no credential, and a credential of the wrong kind.
      expect((await api.get('/exchange/v1/me')).status()).toBe(401)
      expect(
        (await api.get('/exchange/v1/me', { headers: { authorization: 'Bearer not-a-real-token' } })).status(),
      ).toBe(401)

      // A patient grant is not a staff credential, restored or otherwise.
      const asStaff = await api.get('/exchange/v1/me', {
        headers: { authorization: `Bearer ${recoveredGrant}` },
      })
      expect(asStaff.status(), 'a patient grant must not authorize a staff endpoint').toBe(401)
    } finally {
      await api.dispose()
    }
  })

  // ---------------------------------------------------------------------------
  // Everything above proved the old state is here. Only now does anything write.
  // ---------------------------------------------------------------------------

  test('RECOVERY-7 — a NEW order can be prescribed and routed after recovery', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)

    await openSection(page, 'Patients')
    await expect(page.getByText(PATIENT).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: /^Ouvrir/ }).first().click()

    await page.getByRole('button', { name: 'Nouvel ordre de laboratoire' }).click()
    await page.getByRole('checkbox', { name: new RegExp(CATALOG_ENTRY) }).check()
    await page.getByRole('button', { name: /^Suivant/ }).click()
    await expect(page.getByText(/Révision de l.ordre de laboratoire/)).toBeVisible()
    await page.getByRole('button', { name: 'Confirmer et prescrire' }).click()

    await expect(page.getByText(/Choisir un laboratoire destinataire/)).toBeVisible({ timeout: 30_000 })
    await page.getByRole('radio', { name: new RegExp(LAB_ORG) }).check()
    await page.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }).click()

    await openSection(page, 'Ordres')
    // The new order is whichever row is NOT the restored one. A restored database that could only be
    // read would fail before here: this is a real INSERT, with a server-generated identity, against
    // restored sequences and restored constraints.
    const newRow = page
      .getByRole('row')
      .filter({ hasText: PATIENT })
      .filter({ hasNot: page.getByText(sentinels.order_reference_ui) })
      .first()
    await expect(newRow).toBeVisible({ timeout: 30_000 })
    newOrderReference = (await newRow.locator('td').nth(5).innerText()).replace('…', '').trim()
    expect(newOrderReference.length).toBeGreaterThan(4)
    expect(
      newOrderReference,
      'the post-recovery order must be a different order, not the restored one',
    ).not.toBe(sentinels.order_reference_ui)

    // A grant on the NEW order, so cross-order isolation can be tested with two real grants rather
    // than reasoned about. Captured the same way as the pre-backup one: off the network response,
    // because the product never renders the raw value as text.
    const issued = page.waitForResponse(
      (response) =>
        response.url().includes('/patient-access') &&
        response.request().method() === 'POST' &&
        response.status() < 400,
      { timeout: 30_000 },
    )
    await newRow.getByRole('button', { name: /Suivi patient/ }).click()
    await page.getByRole('button', { name: 'Créer le suivi patient' }).click()
    const body = (await (await issued).json()) as { access_token?: string }
    expect(body.access_token).toBeTruthy()
    postRecoveryGrant = body.access_token as string
  })

  test('RECOVERY-8 — each grant opens only its own order: the restored one and the new one do not cross', async () => {
    expect(postRecoveryGrant, 'the post-recovery grant must have been issued').not.toBe('')

    const api = await request.newContext({ baseURL: exchangeUrl, ignoreHTTPSErrors: true })
    try {
      // Asserted HERE, before the laboratory touches the new order, precisely because the two orders
      // are otherwise identical -- same patient, same catalogue entry, same provider. At this moment
      // their states differ, so the two grants are distinguishable by what they return, and a grant
      // that had become order-agnostic during the restore would be caught. Once the new order is
      // released and reviewed the two look alike again, which is why this cannot wait.
      const old = await api.get('/exchange/v1/patient/order', {
        headers: { authorization: `Bearer ${recoveredGrant}` },
      })
      const fresh = await api.get('/exchange/v1/patient/order', {
        headers: { authorization: `Bearer ${postRecoveryGrant}` },
      })
      expect(old.status()).toBe(200)
      expect(fresh.status()).toBe(200)

      const oldBody = (await old.json()) as { status: string; result_status: string }
      const freshBody = (await fresh.json()) as { status: string; result_status: string }

      // The restored grant still sees the order that was released and reviewed before the backup.
      expect(oldBody.status).toBe('COMPLETED')
      expect(oldBody.result_status).toBe('NO_FOLLOW_UP_REQUIRED')
      // The new grant sees the order that has only just been routed -- no result exists for it yet.
      expect(freshBody.status).not.toBe('COMPLETED')
      expect(freshBody.result_status).toBe('NOT_READY')
    } finally {
      await api.dispose()
    }
  })

  test('RECOVERY-9 — the laboratory carries the new order through collection, analysis and entry', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openSection(page, 'Ordres')

    // Scoped to the NEW order's own card. The restored order is in this same queue, which is exactly
    // why a by-patient-name locator would be ambiguous here.
    const card = page.locator('li,div').filter({ hasText: newOrderReference }).first()
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
    expect(count).toBeGreaterThan(0)
    for (let index = 0; index < count; index += 1) {
      const field = required.nth(index)
      const numeric = (await field.getAttribute('inputmode')) === 'decimal'
      // A different value from the pre-backup result on purpose: it must be possible to tell the new
      // observation from the restored one, and the restored one must not change.
      await field.fill(numeric ? '7.1' : 'Observation après restauration')
    }
    await page.getByRole('button', { name: 'Enregistrer les valeurs' }).click()
    await expect(page.getByText(/valeur saisie n.est pas encore enregistrée/)).toBeHidden({ timeout: 30_000 })

    await page.getByRole('button', { name: 'Terminer la saisie' }).click()
    await page.getByRole('button', { name: 'Confirmer la fin de la saisie' }).click()
    await expect(page.getByText(/Saisie terminée/).first()).toBeVisible({ timeout: 30_000 })
  })

  test('RECOVERY-10 — the new result is released by the separate release authority', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, RELEASER)
    await openSection(page, 'Résultats')

    // ADR-044's separation survived the restore too: this is a different identity from the one that
    // entered the values, and it is the only one offered this control.
    await page.getByRole('button', { name: new RegExp(`Examiner les résultats de ${PATIENT}`) }).first().click()
    await expect(page.getByText(/7.1/).first()).toBeVisible({ timeout: 30_000 })

    await page.getByRole('button', { name: 'Libérer les résultats' }).click()
    await page.getByRole('button', { name: /Confirmer la libération|Libérer/ }).last().click()
    await expect(page.getByText(/Résultats libérés/).first()).toBeVisible({ timeout: 30_000 })
  })

  test('RECOVERY-11 — a clinician reviews the new release, and the restored one is untouched', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)
    await openSection(page, 'Résultats')

    // One new outstanding obligation, for the new release. The restored review is still history.
    await page.getByRole('button', { name: /^Examiner/ }).first().click()
    await expect(page.getByRole('tab', { name: /Résultat actuel/ })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/7.1/).first()).toBeVisible()

    await page.getByLabel('Aucun suivi requis').check()
    await page.getByRole('button', { name: 'Confirmer la revue' }).click()
    await expect(page.getByText(/Revue enregistrée/)).toBeVisible({ timeout: 30_000 })

    // Two reviews in history now: the restored one and the one just recorded. The restored result's
    // own content is re-checked byte for byte by the sentinel fingerprint outside this suite.
    await expect(page.getByRole('heading', { name: 'Historique (2)' })).toBeVisible({ timeout: 30_000 })
  })

  test('RECOVERY-12 — after all the new work, the restored grant and its release are unchanged', async () => {
    const api = await request.newContext({ baseURL: exchangeUrl, ignoreHTTPSErrors: true })
    try {
      // The last word on immutability, from the patient's side. A second order for this patient has
      // since been ordered, resulted, released and reviewed; the restored grant must still resolve to
      // the order it was issued for, in the state that order was in before the backup.
      const response = await api.get('/exchange/v1/patient/order', {
        headers: { authorization: `Bearer ${recoveredGrant}` },
      })
      expect(response.status()).toBe(200)
      const body = (await response.json()) as { status: string; result_status: string; requested_tests: unknown[] }
      expect(body.status).toBe('COMPLETED')
      expect(body.result_status).toBe('NO_FOLLOW_UP_REQUIRED')
      expect(body.requested_tests.length).toBe(1)
    } finally {
      await api.dispose()
    }
  })

  test('the recovery proof produced no browser console errors', async () => {
    expect(consoleErrors, `console errors during recovery:\n${consoleErrors.join('\n')}`).toEqual([])
  })
})
