import { expect, test, type Page } from '@playwright/test'
import { loginViaRealKeycloak } from '../support/login'

/**
 * DHEALTH-NATIVE-MVP-1.0 — one synthetic diagnostic order, clinic to laboratory to release to
 * clinician review, performed by a real browser against the real native stack.
 *
 * Everything the product considers a clinical transition is made by clicking the real control a
 * staff user clicks. Nothing here writes to PostgreSQL, calls `/exchange/v1/*` directly to advance
 * a state, or injects a session. Each phase signs out and signs in again through the real Keycloak
 * form, because "the same order is visible to the other role" is only worth asserting when the
 * other role's authority is really established.
 *
 * The baseline it runs against is DH-DEMO-001's: `Clinique Démo DHealth`, `Laboratoire Démo
 * DHealth`, catalogue entry `Analyse de démonstration A`, patient `Patient Démo Alpha`, and the
 * three demo accounts. All synthetic, all obviously so.
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

/** The order this run creates, carried between phases. Set in phase A, read by every phase after. */
let orderReference = ''

/**
 * Console errors and failed requests are findings, not noise (§8): a page that throws on the way
 * through the journey has not completed it, whatever the assertions say.
 */
const consoleErrors: string[] = []
const failedRequests: string[] = []

function watchForBrowserProblems(page: Page): void {
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(`${page.url()} :: ${message.text()}`)
  })
  page.on('pageerror', (error) => consoleErrors.push(`${page.url()} :: ${error.message}`))
  page.on('requestfailed', (request) => {
    // Navigation aborts during the Keycloak redirect dance are expected and are not failures.
    const failure = request.failure()?.errorText ?? ''
    if (failure.includes('ERR_ABORTED')) return
    failedRequests.push(`${request.method()} ${request.url()} :: ${failure}`)
  })
}

async function signIn(page: Page, who: { username: string; password: string }): Promise<void> {
  expect(who.password, 'demo passwords must come from demo/native/demo.env').not.toBe('')
  await page.goto('/')
  await loginViaRealKeycloak(page, who.username, who.password)
  await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({
    timeout: 30_000,
  })
}

/**
 * Each phase gets Playwright's own fresh browser context, so the identity change between them is a
 * genuinely new browser with no cookie, no storage and no token -- stronger isolation than clicking
 * "Se déconnecter" would give, and the reason no explicit sign-out step appears below. Every phase
 * therefore begins at the real Keycloak login form.
 */
async function openSection(page: Page, section: string): Promise<void> {
  await page.getByRole('button', { name: section, exact: true }).click()
}

test.describe.configure({ mode: 'serial' })

test.describe('DHEALTH-NATIVE-MVP-1.0 golden journey', () => {
  test('PHASE A — a clinician signs in, prescribes an order and routes it to the laboratory', async ({
    page,
  }) => {
    watchForBrowserProblems(page)

    await signIn(page, CLINICIAN)
    // The membership the token actually carries decides what is on screen; the demo clinician
    // belongs to the provider organization and to no other.
    await expect(page.getByText(PROVIDER_ORG).first()).toBeVisible()

    await openSection(page, 'Patients')
    await expect(page.getByText(PATIENT).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: /^Ouvrir/ }).first().click()

    await page.getByRole('button', { name: 'Nouvel ordre de laboratoire' }).click()
    await page.getByRole('checkbox', { name: new RegExp(CATALOG_ENTRY) }).check()
    await page.getByRole('button', { name: /^Suivant/ }).click()

    // The review step restates patient, provider and prescriber before anything is created.
    await expect(page.getByText(/Révision de l.ordre de laboratoire/)).toBeVisible()
    await page.getByRole('button', { name: 'Confirmer et prescrire' }).click()

    // Routing is a separate, explicit act: an order exists before a laboratory has been chosen.
    await expect(page.getByText(/Choisir un laboratoire destinataire/)).toBeVisible({
      timeout: 30_000,
    })
    await page.getByRole('radio', { name: new RegExp(LAB_ORG) }).check()
    await page.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }).click()

    await openSection(page, 'Ordres')
    const row = page.getByRole('row').filter({ hasText: PATIENT }).first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    await expect(row).toContainText(LAB_ORG)

    // The order reference the clinic screen shows is what correlates every later phase. Read from
    // the rendered table, never from the database.
    // Column order: Patient, Analyses, Statut, Laboratoire destinataire, Date de création,
    // Référence de l'ordre, Parcours.
    const reference = await row.locator('td').nth(5).innerText()
    orderReference = reference.replace('…', '').trim()
    expect(orderReference.length).toBeGreaterThan(4)
  })

  test('PHASE B — a laboratory technician accepts the same order, records collection, starts analysis and enters results', async ({
    page,
  }) => {
    watchForBrowserProblems(page)

    await signIn(page, LAB_TECH)
    await expect(page.getByText(LAB_ORG).first()).toBeVisible()

    await openSection(page, 'Ordres')
    // The SAME order, identified by the reference the clinic screen showed.
    const card = page.locator('li,div').filter({ hasText: orderReference }).first()
    await expect(card).toBeVisible({ timeout: 30_000 })

    await page.getByRole('button', { name: new RegExp(`Examiner l.ordre de ${PATIENT}`) }).click()
    await page.getByRole('button', { name: "Accepter l'ordre" }).click()

    await page.getByRole('button', { name: 'Enregistrer la collecte' }).click()
    await page.getByRole('button', { name: 'Confirmer la collecte' }).click()
    await expect(page.getByText('Collecte enregistrée').first()).toBeVisible({ timeout: 30_000 })

    await page.getByRole('button', { name: "Commencer l'analyse" }).click()
    await page.getByRole('button', { name: 'Commencer', exact: true }).click()

    // Structured entry: the fields come from the pinned template, not from this test, and EVERY
    // definition the template marks required has to be answered. The demo template has two (one
    // numeric, one free text); filling only the first leaves "Terminer la saisie" correctly
    // disabled, which is the product refusing to call an incomplete result complete.
    const required = page.getByLabel(/Requis/)
    // `count()` does not auto-wait, and the entry form renders after the workspace re-reads the
    // order it has just moved into processing.
    await expect(required.first()).toBeVisible({ timeout: 30_000 })
    const count = await required.count()
    expect(count, 'the demo template declares required observations').toBeGreaterThan(0)
    for (let index = 0; index < count; index += 1) {
      const field = required.nth(index)
      const numeric = (await field.getAttribute('inputmode')) === 'decimal'
      await field.fill(numeric ? '5.4' : 'Observation de démonstration')
    }
    await page.getByRole('button', { name: 'Enregistrer les valeurs' }).click()
    await expect(page.getByText(/valeur saisie n.est pas encore enregistrée/)).toBeHidden({
      timeout: 30_000,
    })

    await page.getByRole('button', { name: 'Terminer la saisie' }).click()
    await page.getByRole('button', { name: 'Confirmer la fin de la saisie' }).click()
    await expect(page.getByText(/Saisie terminée/).first()).toBeVisible({ timeout: 30_000 })
  })

  test('PHASE B2 — the technician who entered the result cannot release it', async ({ page }) => {
    watchForBrowserProblems(page)

    await signIn(page, LAB_TECH)
    // ADR-044 keeps entry and release as separate authorities. The demo technician holds LAB_TECH
    // and not LAB_RESULT_RELEASER, so the section that releases does not exist for them -- absence
    // is not the access control (the server refuses too), but it is what the product promises.
    await expect(page.getByRole('button', { name: 'Ordres', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Résultats', exact: true })).toHaveCount(0)
  })

  test('PHASE C — a releaser releases the result explicitly', async ({ page }) => {
    watchForBrowserProblems(page)

    await signIn(page, RELEASER)
    await openSection(page, 'Résultats')

    await page.getByRole('button', { name: new RegExp(`Examiner les résultats de ${PATIENT}`) }).click()
    await expect(page.getByText(/5.4/).first()).toBeVisible({ timeout: 30_000 })

    await page.getByRole('button', { name: 'Libérer les résultats' }).click()
    await page.getByRole('button', { name: /Confirmer la libération|Libérer/ }).last().click()
    await expect(page.getByText(/Résultats libérés/).first()).toBeVisible({ timeout: 30_000 })
  })

  test('PHASE D — the clinic sees the released version and the clinician reviews that exact version', async ({
    page,
  }) => {
    watchForBrowserProblems(page)

    await signIn(page, CLINICIAN)
    await openSection(page, 'Résultats')

    await expect(page.getByText(PATIENT).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: /^Examiner/ }).first().click()

    // DH-USABILITY-001 §13: the version on screen says whether it is the one that counts.
    await expect(page.getByRole('tab', { name: /Résultat actuel/ })).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.getByText(/5.4/).first()).toBeVisible()

    await page.getByLabel('Aucun suivi requis').check()
    await page.getByRole('button', { name: 'Confirmer la revue' }).click()
    await expect(page.getByText(/Revue enregistrée/)).toBeVisible({ timeout: 30_000 })
  })

  test('the journey produced no browser console errors and no failed requests', async () => {
    expect(consoleErrors, `console errors during the journey:\n${consoleErrors.join('\n')}`).toEqual(
      [],
    )
    expect(
      failedRequests,
      `failed network requests during the journey:\n${failedRequests.join('\n')}`,
    ).toEqual([])
  })
})
