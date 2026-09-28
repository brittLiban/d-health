import {
  expect,
  request as apiRequest,
  test,
  type APIRequestContext,
  type Browser,
  type Page,
} from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import { exchangeSql } from '../support/securityAdmin'
import { waitForEdge } from '../support/edgeReadiness'

/**
 * DHEALTH-BILLING-001 — the till, against the real stack, in a real browser.
 *
 * ── The question this file answers ──
 *
 * Not "does the billing API work" — `adapter/tests/test_dhealthbilling001_billing.py` answers that
 * in 48 tests and the PostgreSQL qualification answers the concurrency half in 12 more. This file
 * answers **can a cashier actually take money in DHealth end to end**, in both kinds of facility:
 * find the person, see what this facility performed, issue the invoice, take the payment, and hand
 * over a receipt — with the money provably recorded in the database afterwards.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated, no session injected, no auth state written into browser storage. Every
 * financial act below is performed by clicking the product, by a cashier who signed in through a
 * real Authorization Code + PKCE flow. `exchangeSql` appears only to read state *back* and assert
 * that the database says what the screen said — never to cause a change the product could not.
 *
 * The one exception is BILL-L's *setup*: a laboratory can only bill an order it has taken, and this
 * suite's claim is about the till rather than about ordering, so the order is created and accepted
 * through the API by the people entitled to do it. Everything billing-related is still clicked.
 *
 * ── The phases ──
 *
 *   BILL-A   a cashier signs in, reaches Facturation, and reaches nothing clinical
 *   BILL-C1  the clinic journey: find patient → consultation → invoice → part payment → settled
 *   BILL-P   an administrator changes a price, and the invoice already issued does not move
 *   BILL-L1  the laboratory journey: its own order → invoice → payment → receipt
 *   BILL-S   the P0 invariant on the real stack: a released result with the bill unpaid
 *   BILL-R   the refusals, in both directions
 *
 * ── State this suite leaves behind, stated rather than discovered ──
 *
 * An invoice and a payment cannot be deleted — there is no endpoint, deliberately, because financial
 * history is append-only. So this suite does **not** clean up after itself and cannot.
 * `scripts/demo/reset.sh` clears invoices, lines, payments, the number sequence and clinic service
 * prices as workflow state (`demo_seed._WORKFLOW_TABLES`, extended by this ticket), which is the
 * supported way back to the baseline before running it again.
 *
 * All patient, service and price data here is the demo baseline's synthetic data. No real patient
 * data is used. No money moves: DHealth records that a payment was taken and contacts no bank, card
 * network or mobile-money provider.
 */

const EXCHANGE = process.env.E2E_EXCHANGE_URL ?? 'https://exchange.localhost'
const API = `${EXCHANGE}/exchange/v1`

const CLERK = {
  username: process.env.DEMO_CLERK_USERNAME ?? 'demo-clerk',
  password: process.env.DEMO_CLERK_PASSWORD ?? '',
}
const LAB_CLERK = {
  username: process.env.DEMO_LAB_CLERK_USERNAME ?? 'demo-lab-clerk',
  password: process.env.DEMO_LAB_CLERK_PASSWORD ?? '',
}
const ADMIN = {
  username: process.env.DEMO_ADMIN_USERNAME ?? 'demo-admin',
  password: process.env.DEMO_ADMIN_PASSWORD ?? '',
}
const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}
const LAB_TECH = {
  username: process.env.DEMO_LAB_TECH_USERNAME ?? 'demo-lab-tech',
  password: process.env.DEMO_LAB_TECH_PASSWORD ?? '',
}

interface Membership {
  organization_id: string
  organization_name: string
  organization_type: string
  organization_status: string
  membership_status: string
  roles: string[]
}

interface Session {
  token: string
  identityId: string
  memberships: Membership[]
}

function api(): Promise<APIRequestContext> {
  return apiRequest.newContext({ ignoreHTTPSErrors: true })
}

/** A real browser login, returning the bearer token the application itself put on the wire. */
async function signIn(page: Page, who: { username: string; password: string }): Promise<Session> {
  expect(who.password, `${who.username}'s password must come from demo/native/demo.env`).not.toBe('')
  const tokenPromise = captureFirstExchangeBearerToken(page)
  await page.goto('/')
  await loginViaRealKeycloak(page, who.username, who.password)
  const token = await tokenPromise
  const ctx = await api()
  try {
    const me = await ctx.get(`${API}/me`, { headers: { Authorization: `Bearer ${token}` } })
    expect(me.status(), 'a real login must authorize /me').toBe(200)
    const body = await me.json()
    return {
      token,
      identityId: body.identity_id as string,
      memberships: body.memberships as Membership[],
    }
  } finally {
    await ctx.dispose()
  }
}

/**
 * A sign-in in its own browser context, for the phases that need several people.
 *
 * `signIn` on an already-authenticated page does not reach Keycloak at all — the session is still
 * live, so `goto('/')` lands straight in the application and the login helper waits for a form that
 * never appears. BILL-L1 and BILL-S each involve three different identities, so each gets its own
 * context: separate cookies, separate storage, and a real Authorization Code + PKCE flow every
 * time, which is also a more honest model of three people at three workstations.
 */
async function freshSignIn(browser: Browser, who: { username: string; password: string }) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true })
  const page = await context.newPage()
  const session = await signIn(page, who)
  return { session, page, context }
}

function auth(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` }
}

function orgOf(session: Session, type: 'PROVIDER' | 'LAB'): Membership {
  const found = session.memberships.find((m) => m.organization_type === type)
  if (!found) throw new Error(`${type} membership missing from /me -- is the demo baseline seeded?`)
  return found
}

async function get(token: string, path: string) {
  const ctx = await api()
  try {
    const response = await ctx.get(`${API}${path}`, { headers: auth(token) })
    return { status: response.status(), text: await response.text() }
  } finally {
    await ctx.dispose()
  }
}

/**
 * Opens `Facturation` the way a cashier does: by clicking the navigation.
 *
 * Scoped to the navigation landmark, not the page. `BILLING_CLERK`'s French label is also
 * "Facturation", so `Équipe et accès` renders buttons carrying that word for every staff member's
 * role chips — an unscoped locator matched three elements there and failed strict mode. The same
 * word legitimately names a section and a role, which is the point of labelling them consistently;
 * the test has to say which one it means.
 */
async function openBilling(page: Page) {
  const nav = page.getByRole('navigation', { name: 'Navigation principale' })
  await nav.getByRole('button', { name: 'Facturation', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Facturation', level: 1 })).toBeVisible()
}

/**
 * The money figure inside one panel, as the screen renders it.
 *
 * `\s` rather than the narrow no-break space the formatter actually emits: the DOM carries U+202F
 * so a total cannot wrap mid-number, and any text comparison normalizes it to an ordinary space.
 * A matcher carrying the real character finds nothing and the failure prints two strings that look
 * identical, which is a bad hour.
 */
function money(amount: string): RegExp {
  // Unanchored: `toContainText` tests the element's *whole* text, and a price sits beside the name
  // of the thing it prices ("Consultation générale3 000,00 DJF"). An anchored pattern matched
  // nothing there and looked like a rendering bug.
  return new RegExp(`${amount.replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/, '\\s')}\\sDJF`)
}

const RUN = `${Date.now()}`
let clerk: Session
let providerId = ''
let labId = ''
let invoiceNumber = ''
let labInvoiceNumber = ''
let labOrderId = ''

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  await waitForEdge()
})

test.describe('DHEALTH-BILLING-001 — a facility takes money, end to end', () => {
  test('BILL-A: a cashier reaches Facturation and nothing clinical', async ({ page }) => {
    clerk = await signIn(page, CLERK)
    providerId = orgOf(clerk, 'PROVIDER').organization_id

    // The membership is BILLING_CLERK and nothing else. That is the whole point of the role
    // existing (ADR-066 §2), and it is also the configuration that would have shown an empty
    // workspace had `isUsableMembership` not learned about it.
    expect(orgOf(clerk, 'PROVIDER').roles).toEqual(['BILLING_CLERK'])

    await openBilling(page)

    // Every clinical section is absent, not disabled. A navigation entry that cannot be used is a
    // promise the product has not kept.
    //
    // Scoped to the navigation and `exact`, so these are assertions about *sections*. An absence
    // check that names something the page never had is vacuous and silently green — this loop
    // originally spelled `Aujourd’hui` with a typographic apostrophe against a label rendered with
    // an ASCII one, and proved nothing for it.
    const nav = page.getByRole('navigation', { name: 'Navigation principale' })
    for (const section of [
      "Aujourd'hui",
      'Visites',
      'Patients',
      'Ordres',
      'Résultats',
      'Équipe et accès',
    ]) {
      await expect(nav.getByRole('button', { name: section, exact: true })).toHaveCount(0)
    }
    // The positive control for the loop above: the one section this account *does* have is found by
    // the same locator shape, so a query that matches nothing cannot pass for an absence.
    await expect(nav.getByRole('button', { name: 'Facturation', exact: true })).toHaveCount(1)

    // ...and the price editor is absent too: setting prices is the administrator's capability.
    await expect(page.getByRole('heading', { name: 'Services et tarifs' })).toHaveCount(0)
  })

  test('BILL-C1: find the patient, invoice a consultation, and settle it', async ({ page }) => {
    await signIn(page, CLERK)
    await openBilling(page)

    // The lookup returns the date of birth beside the name, which is the reason it returns more
    // than a name at all: two people sharing one is ordinary, and the clerk confirms the date with
    // the person in front of them before charging anybody.
    const search = page.getByRole('form', { name: 'Rechercher le patient' })
    await search.getByLabel('Nom du patient').fill('Démo')
    await search.getByRole('button', { name: 'Rechercher' }).click()
    const match = page.getByRole('listitem').filter({ hasText: 'Patient Démo Alpha' }).first()
    await expect(match).toContainText('né(e) le')
    await match.getByRole('button', { name: 'Choisir' }).click()

    // What this clinic performed, and only that. The routed laboratory's tests are not offered
    // here and could not be invoiced if they were (ADR-066 §3).
    const consultation = page
      .getByRole('listitem')
      .filter({ hasText: 'Consultation générale' })
      .first()
    await expect(consultation).toContainText(money('3000.00'))
    await consultation.getByRole('button', { name: 'Ajouter' }).click()
    await page.getByRole('button', { name: 'Créer la facture' }).click()

    const panel = page.getByRole('region', { name: 'Facture', exact: true })
    await expect(panel).toBeVisible()
    await expect(panel).toContainText(/FAC-\d{4}-\d{6}/)
    invoiceNumber = ((await panel.textContent()) ?? '').match(/FAC-\d{4}-\d{6}/)?.[0] ?? ''
    expect(invoiceNumber, 'the invoice must carry a facility reference').not.toBe('')
    await expect(panel.getByText('À régler', { exact: true })).toBeVisible()

    // A part payment. The balance the screen shows is the server's own figure — nothing on this
    // page subtracts anything.
    const pay = page.getByRole('form', { name: 'Enregistrer un paiement' })
    await pay.getByLabel('Montant reçu').fill('1000.00')
    await pay.getByLabel('Moyen').selectOption('CASH')
    await pay.getByRole('button', { name: 'Enregistrer le paiement' }).click()
    await expect(panel.getByText('Partiellement réglée', { exact: true })).toBeVisible()
    await expect(panel.getByText(money('2000.00'))).toBeVisible()
    await expect(panel).toContainText(`${invoiceNumber}-R1`)

    // The remainder settles it exactly, and the payment form goes away rather than offering an
    // action the server would refuse.
    const pay2 = page.getByRole('form', { name: 'Enregistrer un paiement' })
    await pay2.getByLabel('Montant reçu').fill('2000.00')
    await pay2.getByRole('button', { name: 'Enregistrer le paiement' }).click()
    await expect(panel.getByText('Soldée', { exact: true })).toBeVisible()
    await expect(panel).toContainText(`${invoiceNumber}-R2`)
    await expect(page.getByRole('form', { name: 'Enregistrer un paiement' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Imprimer le reçu' })).toBeVisible()

    // The database says what the screen said. Two payments, exact amounts, no stored balance.
    const row = exchangeSql(
      `SELECT (SELECT count(*) FROM exchange_payments p JOIN exchange_invoices i ON i.id=p.invoice_id
                WHERE i.number='${invoiceNumber}' AND i.organization_id='${providerId}')
           || '|' || (SELECT sum(p.amount) FROM exchange_payments p JOIN exchange_invoices i
                        ON i.id=p.invoice_id WHERE i.number='${invoiceNumber}' AND i.organization_id='${providerId}')
           || '|' || (SELECT sum(l.quantity * l.unit_amount) FROM exchange_invoice_lines l
                        JOIN exchange_invoices i ON i.id=l.invoice_id WHERE i.number='${invoiceNumber}' AND i.organization_id='${providerId}')`,
    )
    expect(row).toBe('2|3000.00|3000.00')
  })

  test('BILL-P: an administrator reprices, and the issued invoice does not move', async ({ page }) => {
    await signIn(page, ADMIN)
    await openBilling(page)

    // The administrator's half of the screen: prices, and no till.
    await expect(page.getByRole('heading', { name: 'Services et tarifs' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Nouvelle facture' })).toHaveCount(0)
    await expect(page.getByRole('form', { name: 'Rechercher le patient' })).toHaveCount(0)

    const form = page.getByRole('form', { name: 'Changer le tarif — Consultation générale' })
    await form.getByLabel('Nouveau tarif').fill('4500.00')
    await form.getByRole('button', { name: 'Changer' }).click()
    await expect(page.getByText(/ne changent pas/)).toBeVisible()

    // The price list moved; the invoice issued minutes ago did not. This is the claim that makes a
    // printed invoice trustworthy, and it is read from the database rather than from the screen.
    const frozen = exchangeSql(
      `SELECT l.unit_amount FROM exchange_invoice_lines l JOIN exchange_invoices i
         ON i.id=l.invoice_id WHERE i.number='${invoiceNumber}' AND i.organization_id='${providerId}'`,
    )
    expect(frozen).toBe('3000.00')
    const current = exchangeSql(
      `SELECT p.amount FROM exchange_clinic_service_prices p JOIN exchange_clinic_services s
         ON s.id=p.clinic_service_id
        WHERE s.name='Consultation générale' AND p.superseded_at IS NULL`,
    )
    expect(current).toBe('4500.00')
  })

  test('BILL-L1: a laboratory invoices the test it performed', async ({ browser }) => {
    // Setup through the API, by the people entitled to do it: this suite's claim is about the till,
    // not about ordering, and a laboratory can only bill an order it has actually taken.
    //
    // Three identities, three browser contexts. Signing a second person in on the first person's
    // page never reaches Keycloak at all — the session is still live — so the login helper waits
    // for a form that never appears and times out.
    const clinic = await freshSignIn(browser, CLINICIAN)
    const clinicianSession = clinic.session
    const provider = orgOf(clinicianSession, 'PROVIDER').organization_id
    const ctx = await api()
    try {
      // The baseline patient's existing reference and the demo catalogue entry, read rather than
      // recreated: `POST /patients` refuses a reused idempotency key whose content differs, so
      // registering the same person again is the wrong way to obtain a reference.
      const referenceId = exchangeSql(
        `SELECT r.id FROM exchange_patient_references r JOIN exchange_patients p
           ON p.id = r.patient_id WHERE p.display_name = 'Patient Démo Alpha' LIMIT 1`,
      )
      const entryId = exchangeSql(
        `SELECT id FROM exchange_test_catalog_entries WHERE code = 'DEMO-TEST-A' LIMIT 1`,
      )
      expect(referenceId, 'the baseline patient reference must exist').not.toBe('')
      expect(entryId, 'the demo catalogue entry must exist').not.toBe('')

      const intent = await ctx.post(`${API}/organizations/${provider}/order-intents`, {
        headers: { ...auth(clinicianSession.token), 'Idempotency-Key': crypto.randomUUID() },
        data: { patient_reference_id: referenceId, catalog_entry_ids: [entryId] },
      })
      expect(intent.status(), await intent.text()).toBe(201)
      const committed = await ctx.post(
        `${API}/organizations/${provider}/order-intents/${(await intent.json()).id}/commit`,
        { headers: { ...auth(clinicianSession.token), 'Idempotency-Key': crypto.randomUUID() } },
      )
      expect([200, 201], await committed.text()).toContain(committed.status())
      labOrderId = (await committed.json()).id

      // A committed order is `AWAITING_LAB_SELECTION` — routing is a separate, deliberate clinical
      // act (ADR-043's launch option (a)), not something commit does on the facility's behalf. The
      // laboratory has nothing to accept, and therefore nothing to bill, until it is assigned.
      labId = exchangeSql(
        `SELECT id FROM exchange_organizations WHERE name = 'Laboratoire Démo DHealth' LIMIT 1`,
      )
      expect(labId, 'the demo laboratory must exist').not.toBe('')
      const assigned = await ctx.post(
        `${API}/organizations/${provider}/orders/${labOrderId}/assign-lab`,
        {
          headers: auth(clinicianSession.token),
          data: { lab_organization_id: labId },
        },
      )
      expect([200, 201], await assigned.text()).toContain(assigned.status())
    } finally {
      await ctx.dispose()
    }

    // The laboratory accepts the work. Assignment alone is not performance, and until it accepts
    // there is nothing it may charge for (`BILLABLE_ORDER_STATUSES`).
    await clinic.context.close()

    const labTech = await freshSignIn(browser, LAB_TECH)
    expect(orgOf(labTech.session, 'LAB').organization_id).toBe(labId)
    const acceptCtx = await api()
    try {
      const accepted = await acceptCtx.post(
        `${API}/organizations/${labId}/lab-orders/${labOrderId}/accept`,
        { headers: auth(labTech.session.token) },
      )
      expect([200, 201], await accepted.text()).toContain(accepted.status())
    } finally {
      await acceptCtx.dispose()
      await labTech.context.close()
    }

    // Now the till, clicked.
    const till = await freshSignIn(browser, LAB_CLERK)
    const page = till.page
    await openBilling(page)

    // A laboratory clerk never searches for a patient: it holds no directory of its own (D-6 is
    // open) and works from the order in front of it.
    await expect(page.getByRole('form', { name: 'Rechercher le patient' })).toHaveCount(0)

    const order = page
      .getByRole('listitem')
      .filter({ hasText: 'Analyse de démonstration A' })
      .first()
    await order.getByRole('button', { name: 'Facturer' }).click()

    const test = page
      .getByRole('listitem')
      .filter({ hasText: 'Analyse de démonstration A' })
      .last()
    await test.getByRole('button', { name: 'Ajouter' }).click()
    await page.getByRole('button', { name: 'Créer la facture' }).click()

    const panel = page.getByRole('region', { name: 'Facture', exact: true })
    await expect(panel).toContainText(/FAC-\d{4}-\d{6}/)
    labInvoiceNumber = ((await panel.textContent()) ?? '').match(/FAC-\d{4}-\d{6}/)?.[0] ?? ''

    // Its own sequence: the laboratory's first invoice is 000001 in its own facility, not a
    // continuation of the clinic's numbering.
    expect(labInvoiceNumber.endsWith('000001')).toBe(true)

    const pay = page.getByRole('form', { name: 'Enregistrer un paiement' })
    await pay.getByLabel('Montant reçu').fill('3500.00')
    await pay.getByLabel('Moyen').selectOption('MOBILE_MONEY')
    await pay.getByLabel('Référence (facultatif)').fill(`MM-${RUN}`)
    await pay.getByRole('button', { name: 'Enregistrer le paiement' }).click()
    await expect(panel.getByText('Soldée', { exact: true })).toBeVisible()
    await expect(panel).toContainText(`${labInvoiceNumber}-R1`)

    // The line is the laboratory's own work, bound to the order item rather than to a service.
    const source = exchangeSql(
      `SELECT l.source_type FROM exchange_invoice_lines l JOIN exchange_invoices i
         ON i.id=l.invoice_id WHERE i.number='${labInvoiceNumber}' AND i.organization_id='${labId}'`,
    )
    expect(source).toBe('LAB_ORDER_ITEM')
    await till.context.close()
  })

  test('BILL-S: a result releases with the bill unpaid, on the real stack', async ({ browser }) => {
    // The P0 invariant, exercised against the running product rather than only in the suite. An
    // outstanding balance is created first, then the whole clinical chain is driven to release.
    const till = await freshSignIn(browser, LAB_CLERK)
    const clerkSession = till.session
    const lab = orgOf(clerkSession, 'LAB').organization_id

    const unpaidCtx = await api()
    let unpaidNumber = ''
    try {
      const patientId = exchangeSql(
        `SELECT id FROM exchange_patients WHERE display_name='Patient Démo Alpha' LIMIT 1`,
      )
      const itemId = exchangeSql(
        `SELECT id FROM exchange_lab_order_items WHERE lab_order_id='${labOrderId}' LIMIT 1`,
      )
      const created = await unpaidCtx.post(`${API}/organizations/${lab}/billing/invoices`, {
        headers: { ...auth(clerkSession.token), 'Idempotency-Key': crypto.randomUUID() },
        data: {
          patient_id: patientId,
          lines: [{ source_type: 'LAB_ORDER_ITEM', source_id: itemId, quantity: 1 }],
        },
      })
      expect(created.status(), await created.text()).toBe(201)
      const body = await created.json()
      unpaidNumber = body.number
      expect(body.status).toBe('OPEN')
      expect(Number(body.balance)).toBeGreaterThan(0)
    } finally {
      await unpaidCtx.dispose()
    }

    await till.context.close()
    const labTech = await freshSignIn(browser, LAB_TECH)
    const tech = labTech.session
    const workCtx = await api()
    try {
      const base = `${API}/organizations/${labId}/lab-orders/${labOrderId}`
      for (const step of ['record-collection', 'start-processing']) {
        const response = await workCtx.post(`${base}/${step}`, { headers: auth(tech.token) })
        expect([200, 201], `${step}: ${await response.text()}`).toContain(response.status())
      }
      const itemId = exchangeSql(
        `SELECT id FROM exchange_lab_order_items WHERE lab_order_id='${labOrderId}' LIMIT 1`,
      )
      const entered = await workCtx.post(`${base}/items/${itemId}/result`, {
        headers: auth(tech.token),
        data: { result_type: 'NUMERIC', numeric_value: '4.2', numeric_unit: 'u/L' },
      })
      expect(entered.status(), await entered.text()).toBe(201)
    } finally {
      await workCtx.dispose()
      await labTech.context.close()
    }

    const releasing = await freshSignIn(browser, {
      username: process.env.DEMO_RELEASER_USERNAME ?? 'demo-releaser',
      password: process.env.DEMO_RELEASER_PASSWORD ?? '',
    })
    const releaser = releasing.session
    const releaseCtx = await api()
    try {
      const base = `${API}/organizations/${labId}/lab-orders/${labOrderId}`
      const inspection = await releaseCtx.get(`${base}/results`, { headers: auth(releaser.token) })
      expect(inspection.status()).toBe(200)
      const released = await releaseCtx.post(`${base}/release-results`, {
        headers: auth(releaser.token),
        data: { expected_result_fingerprint: (await inspection.json()).result_fingerprint },
      })
      // The whole ticket's P0 invariant, in one assertion against the running product: money owed,
      // result released anyway.
      expect(released.status(), await released.text()).toBe(201)
    } finally {
      await releaseCtx.dispose()
      await releasing.context.close()
    }

    const stillOwed = exchangeSql(
      `SELECT coalesce((SELECT sum(p.amount) FROM exchange_payments p JOIN exchange_invoices i
         ON i.id=p.invoice_id WHERE i.number='${unpaidNumber}' AND i.organization_id='${labId}'), 0)`,
    )
    expect(stillOwed).toBe('0')
  })

  test('BILL-R: the refusals, in both directions', async ({ browser }) => {
    // A clinician holds every clinical authority and no billing authority.
    const clinic = await freshSignIn(browser, CLINICIAN)
    const clinicianSession = clinic.session
    await expect(
      clinic.page
        .getByRole('navigation', { name: 'Navigation principale' })
        .getByRole('button', { name: 'Facturation', exact: true }),
    ).toHaveCount(0)
    const provider = orgOf(clinicianSession, 'PROVIDER').organization_id
    expect((await get(clinicianSession.token, `/organizations/${provider}/billing/invoices`)).status)
      .toBe(403)
    expect(
      (await get(clinicianSession.token, `/organizations/${provider}/billing/patients?q=D`)).status,
    ).toBe(403)

    // ...and a cashier reaches no clinical surface, including the directory their own lookup is
    // deliberately *not* a way into.
    await clinic.context.close()
    const tillSession = await freshSignIn(browser, CLERK)
    const clerkSession = tillSession.session
    expect((await get(clerkSession.token, `/organizations/${providerId}/patients`)).status).toBe(403)
    expect((await get(clerkSession.token, `/organizations/${providerId}/visits`)).status).toBe(403)

    // A clinic clerk asking the laboratory for anything is 404: no relationship at all, and the
    // answer must not confirm the laboratory exists.
    expect((await get(clerkSession.token, `/organizations/${labId}/billing/invoices`)).status).toBe(
      404,
    )

    // An empty query is refused rather than answered with the roster.
    expect(
      (await get(clerkSession.token, `/organizations/${providerId}/billing/patients?q=`)).status,
    ).toBe(422)
    await tillSession.context.close()
  })
})
