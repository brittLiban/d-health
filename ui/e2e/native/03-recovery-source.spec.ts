import { expect, test } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { loginViaRealKeycloak } from '../support/login'

/**
 * DHEALTH-NATIVE-RECOVERY-001 — the durable state the golden journey does NOT create, added to the
 * source deployment before it is backed up.
 *
 * Runs after `01-golden-journey.spec.ts` and before the backup. Its job is to make the archive
 * contain the things a disaster-recovery drill would otherwise never exercise: a patient-access
 * grant, and the patient-notification rows that are created in the SAME transaction as that grant
 * (an enrollment and an owed message). Both are made by clicking the real product, not by SQL.
 *
 * **No real provider is contacted, and cannot be.** The qualification environment runs
 * `DHEALTH_NOTIFICATION_PROVIDER=fake`, whose implementation has no network code of any kind --
 * `notify/fake.py` names the native demo stack running offline as the reason it exists. ADR-003's
 * gate is untouched: WhatsApp stays BLOCKED, not DONE. Nothing here is evidence about Meta.
 *
 * The raw grant token is captured off the network response, because the product deliberately never
 * renders it as text -- it goes into a QR code and a clipboard action only. It is written to the
 * file named by `E2E_GRANT_TOKEN_FILE`, which the drill puts under a harness directory it deletes
 * afterwards. It is never printed, never asserted against in a message, and never committed.
 */

const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}

const PATIENT = 'Patient Démo Alpha'
// A synthetic Djibouti mobile number. Not a real subscriber line, and it is never dialled: the fake
// transport has no network code.
const SYNTHETIC_DESTINATION = '+253 77 00 00 01'

const tokenFile = process.env.E2E_GRANT_TOKEN_FILE
if (!tokenFile) {
  throw new Error('E2E_GRANT_TOKEN_FILE is required -- this spec refuses to leave a grant token in its own output.')
}

test.describe('DHEALTH-NATIVE-RECOVERY-001 source state', () => {
  test('a clinician issues patient access with a notification, creating durable grant and notification state', async ({
    page,
  }) => {
    expect(CLINICIAN.password, 'demo passwords must come from demo/native/demo.env').not.toBe('')

    await page.goto('/')
    await loginViaRealKeycloak(page, CLINICIAN.username, CLINICIAN.password)
    await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({ timeout: 30_000 })

    await page.getByRole('navigation').getByRole('button', { name: 'Ordres', exact: true }).click()

    const row = page.getByRole('row').filter({ hasText: PATIENT }).first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    // The reference cell is the order's own id prefix (column 6), which is what the recovery proof
    // later looks for on this same screen.
    const reference = (await row.locator('td').nth(5).innerText()).replace('…', '').trim()
    expect(reference.length).toBeGreaterThan(4)

    await row.getByRole('button', { name: /Suivi patient/ }).click()

    // Capture the issued grant off the network. Same technique the authorization suite already uses
    // for bearer tokens: read from the transport, never from application code, never logged.
    const issued = page.waitForResponse(
      (response) =>
        response.url().includes('/patient-access') &&
        response.request().method() === 'POST' &&
        response.status() < 400,
      { timeout: 30_000 },
    )

    await page.getByRole('button', { name: 'Créer et envoyer au patient' }).click()
    await page.getByLabel(/Numéro du patient/).fill(SYNTHETIC_DESTINATION)
    await page.getByRole('checkbox', { name: /accepte de recevoir une notification/ }).check()
    await page.getByRole('button', { name: 'Envoyer le lien sécurisé' }).click()

    const body = (await (await issued).json()) as { access_token?: string; expires_at?: string }
    expect(body.access_token, 'the issue response carries the one-time raw grant').toBeTruthy()
    // 0600, and into the harness directory only. The drill deletes it; §26's cleanup check asserts
    // no such file survives teardown.
    writeFileSync(tokenFile, JSON.stringify({ access_token: body.access_token, order_reference: reference }), {
      mode: 0o600,
    })

    // The product's own confirmation that an active grant now exists -- asserted on screen rather
    // than inferred from the response we just read off the wire.
    await expect(page.getByText(/Accès patient actif|Copier le lien/).first()).toBeVisible({ timeout: 30_000 })
  })
})
