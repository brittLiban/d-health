import type Keycloak from 'keycloak-js'

/** How close to expiry (seconds) a token must be before `updateToken` refreshes it. */
export const TOKEN_REFRESH_WINDOW_SECONDS = 30

/**
 * Builds the `getAuthToken` callback ExchangeClient calls before every
 * request. Single-flight: concurrent callers while a refresh is already in
 * progress share that one attempt rather than each starting an independent
 * refresh (ticket §10). Fails closed: a refresh failure returns `null` --
 * never the prior, possibly-expired token -- and reports the failure via
 * `onRefreshFailure` so the surrounding auth provider can transition its own
 * status truthfully rather than silently continuing.
 */
export function createExchangeTokenProvider(
  keycloak: Keycloak,
  onRefreshFailure: () => void,
): () => Promise<string | null> {
  let inFlight: Promise<string | null> | null = null

  return function getAuthToken(): Promise<string | null> {
    if (!keycloak.authenticated || !keycloak.token) {
      return Promise.resolve(null)
    }

    if (inFlight) {
      return inFlight
    }

    inFlight = (async () => {
      try {
        await keycloak.updateToken(TOKEN_REFRESH_WINDOW_SECONDS)
        return keycloak.token ?? null
      } catch {
        onRefreshFailure()
        return null
      } finally {
        inFlight = null
      }
    })()

    return inFlight
  }
}
