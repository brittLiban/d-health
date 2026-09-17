import type { Page, Request } from '@playwright/test'

/**
 * DH-CLINIC-004B: drives the REAL Keycloak login form served by the REAL
 * Keycloak instance this suite is pointed at (`loginTheme: "keycloak"`'s
 * default field ids -- `#username`/`#password`/`#kc-login`, unchanged by
 * this repo). No token injection, no localStorage seeding, no bypass: this
 * is the exact interactive path a clinician's browser takes.
 */
export async function loginViaRealKeycloak(page: Page, email: string, password: string): Promise<void> {
  await page.waitForURL(/\/realms\/dhealth-exchange\/protocol\/openid-connect\/auth/, {
    timeout: 20_000,
  })
  await page.locator('#username').fill(email)
  await page.locator('#password').fill(password)
  await page.locator('#kc-login').click()
  await page.waitForURL((url) => !url.hostname.startsWith('id.'), { timeout: 20_000 })
}

/**
 * Captures the bearer token off the first REAL outgoing `/exchange/v1/*`
 * request the authenticated app makes -- read directly from the network
 * layer, never exposed by application code, never logged. Callers decode
 * only specific claims (see jwt.ts) and must never print the return value.
 */
export function captureFirstExchangeBearerToken(page: Page): Promise<string> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('no /exchange/v1/* request observed in time')), 20_000)
    const handler = (request: Request) => {
      if (!request.url().includes('/exchange/v1/')) return
      const auth = request.headers().authorization
      if (!auth?.startsWith('Bearer ')) return
      clearTimeout(timeout)
      page.off('request', handler)
      resolve(auth.slice('Bearer '.length))
    }
    page.on('request', handler)
  })
}
