import { request as apiRequest } from '@playwright/test'

/**
 * DHEALTH-NATIVE-SEC-001: refuse to assert anything until the real edge is genuinely answering.
 *
 * Without this, a security suite is actively dangerous rather than merely flaky. Caddy answers
 * **502** for roughly half a minute after the adapter or Keycloak restarts, and a 502 satisfies a
 * naive `expect([401, 403, 404]).toContain(status)`-shaped assertion about as often as it does
 * not. "The boundary refused me" and "the upstream was not listening" must never be the same
 * observation, so every describe block gates on the edge first rather than trusting that the
 * previous block left it healthy.
 *
 * The readiness signal is deliberately a **401 from the API plus a 200 from Keycloak discovery**,
 * not "some response": 401 proves the request reached the adapter and was refused by the real
 * authentication path, and the discovery document proves the realm is being served, which is the
 * thing that lags longest after a restart.
 */
const APP = process.env.E2E_BASE_URL ?? 'https://app.localhost'
const ID = process.env.E2E_KEYCLOAK_PUBLIC_URL ?? 'https://id.localhost'

export async function waitForEdge(): Promise<void> {
  let lastSeen = 'no attempt completed'
  for (let attempt = 0; attempt < 80; attempt += 1) {
    // A FRESH context per attempt, deliberately. Reusing one `APIRequestContext` across a restart
    // window does not work: once a call fails with a connection error mid-bounce the context stays
    // poisoned and every later call throws too, so the loop reports "never became ready" about a
    // stack that recovered thirty seconds in. That cost one misdiagnosed run.
    const probe = await apiRequest.newContext({ ignoreHTTPSErrors: true })
    try {
      const me = await probe.get(`${APP}/exchange/v1/me`, { timeout: 8_000 })
      const disco = await probe.get(
        `${ID}/realms/dhealth-exchange/.well-known/openid-configuration`,
        { timeout: 8_000 },
      )
      lastSeen = `me=${me.status()} discovery=${disco.status()}`
      if (me.status() === 401 && disco.status() === 200) return
    } catch (error) {
      lastSeen = `connection error: ${(error as Error).message.split('\n')[0]}`
    } finally {
      await probe.dispose()
    }
    await new Promise((resolve) => setTimeout(resolve, 3_000))
  }
  throw new Error(
    `the native edge never became ready (last seen: ${lastSeen}); qualification cannot make honest claims`,
  )
}
