import { expect, test } from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'

/**
 * DHEALTH-NATIVE-MVP-1.0 §16 — the boundaries the golden path does not exercise.
 *
 * A journey that succeeds proves the product can do the work. It says nothing about what the
 * product refuses, and refusal is the half that matters clinically. Each test here uses a **real**
 * token obtained by a **real** browser login and then asks the server for something that identity
 * has no authority over.
 *
 * Why these go through the API with a browser-obtained token rather than through the UI: the UI
 * does not offer a clinician a control for reading another organization's orders, which is the
 * point. The server is the access control, the UI is not, and the only way to ask the server the
 * hostile question is to ask it directly with a genuine credential. No token is fabricated, no
 * session is injected, and nothing here advances a clinical workflow.
 */

const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}
const LAB_TECH = {
  username: process.env.DEMO_LAB_TECH_USERNAME ?? 'demo-lab-tech',
  password: process.env.DEMO_LAB_TECH_PASSWORD ?? '',
}

const EXCHANGE_ORIGIN = process.env.E2E_EXCHANGE_URL ?? 'https://exchange.localhost'

interface Membership {
  organization_id: string
  organization_name: string
  organization_type: string
  roles: string[]
}

async function signInAndCapture(
  page: import('@playwright/test').Page,
  who: { username: string; password: string },
): Promise<{ token: string; memberships: Membership[] }> {
  expect(who.password, 'demo passwords must come from demo/native/demo.env').not.toBe('')
  const tokenPromise = captureFirstExchangeBearerToken(page)
  await page.goto('/')
  await loginViaRealKeycloak(page, who.username, who.password)
  const token = await tokenPromise
  const me = await page.request.get(`${EXCHANGE_ORIGIN}/exchange/v1/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(me.status()).toBe(200)
  return { token, memberships: (await me.json()).memberships as Membership[] }
}

test.describe('DHEALTH-NATIVE-MVP-1.0 authorization boundaries', () => {
  test('NEG-1: an unauthenticated browser cannot reach a staff workflow', async ({ page }) => {
    await page.goto('/')
    // The app has no anonymous staff surface: the first thing an unauthenticated browser meets is
    // a real Keycloak login, not a workspace with an empty queue in it.
    await page.waitForURL(/\/realms\/dhealth-exchange\/protocol\/openid-connect\/auth/, {
      timeout: 30_000,
    })
    await expect(page.getByRole('button', { name: 'Se déconnecter' })).toHaveCount(0)
  })

  test('NEG-2: an unauthenticated request to the Exchange is refused', async ({ request }) => {
    const response = await request.get(`${EXCHANGE_ORIGIN}/exchange/v1/me`)
    expect(response.status()).toBe(401)
  })

  test('NEG-3: a clinician cannot read the laboratory organization as if it were their provider', async ({
    page,
  }) => {
    const { token, memberships } = await signInAndCapture(page, CLINICIAN)
    const provider = memberships.find((m) => m.organization_type === 'PROVIDER')
    expect(provider, 'the demo clinician must hold a provider membership').toBeTruthy()

    // The laboratory this demo routes to is a real, different tenant. Its id is discovered the way
    // a clinician's browser legitimately learns it -- from the assignment on their own order --
    // and then used where they have no authority.
    const orders = await page.request.get(
      `${EXCHANGE_ORIGIN}/exchange/v1/organizations/${provider!.organization_id}/orders`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    expect(orders.status()).toBe(200)
    const assigned = (await orders.json()).find(
      (order: { lab_assignment: { lab_organization_id: string } | null }) => order.lab_assignment,
    )
    expect(assigned, 'the journey spec must have routed an order before this runs').toBeTruthy()
    const labId = assigned.lab_assignment.lab_organization_id

    // Reading the laboratory's own work queue with a provider clinician's token.
    const crossTenant = await page.request.get(
      `${EXCHANGE_ORIGIN}/exchange/v1/organizations/${labId}/lab-orders`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    expect([401, 403, 404]).toContain(crossTenant.status())

    // And the laboratory's orders as though the clinician were that organization's provider.
    const asOtherProvider = await page.request.get(
      `${EXCHANGE_ORIGIN}/exchange/v1/organizations/${labId}/orders`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    expect([401, 403, 404]).toContain(asOtherProvider.status())
  })

  test('NEG-4: a laboratory technician cannot act as the provider that sent the work', async ({
    page,
  }) => {
    const { token, memberships } = await signInAndCapture(page, LAB_TECH)
    const lab = memberships.find((m) => m.organization_type === 'LAB')
    expect(lab, 'the demo technician must hold a laboratory membership').toBeTruthy()
    expect(lab!.roles).toContain('LAB_TECH')
    // ADR-044: entry and release are separate authorities, and this identity holds only the first.
    expect(lab!.roles).not.toContain('LAB_RESULT_RELEASER')

    // Ordering is a provider act. A laboratory identity asking a provider surface for its own
    // organization is asking for an authority its membership does not carry.
    const asProvider = await page.request.get(
      `${EXCHANGE_ORIGIN}/exchange/v1/organizations/${lab!.organization_id}/orders`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    expect([401, 403, 404]).toContain(asProvider.status())
  })

  test('NEG-5: a fabricated organization id is refused rather than answered emptily', async ({
    page,
  }) => {
    const { token } = await signInAndCapture(page, CLINICIAN)
    // A syntactically valid UUID belonging to nothing. An empty 200 here would mean the surface
    // answers questions about tenants it has not checked.
    const invented = '00000000-0000-4000-8000-000000000000'
    const response = await page.request.get(
      `${EXCHANGE_ORIGIN}/exchange/v1/organizations/${invented}/orders`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    expect([401, 403, 404]).toContain(response.status())
  })
})
