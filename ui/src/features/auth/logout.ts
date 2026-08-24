export type LogoutLegResult = 'ok' | 'failed' | 'skipped'

export interface CoordinatedLogoutResult {
  openmrs: LogoutLegResult
  openelis: LogoutLegResult
}

/**
 * DELETE /openmrs/ws/rest/v1/session — the same mechanism UI-009/UI-010
 * already proved genuinely invalidates OpenMRS's own JSESSIONID. Needs
 * X-Daher-App-Request (ADR-027's CSRF boundary at the app. origin) since
 * DELETE is a guarded verb there.
 */
async function logoutOpenMrs(): Promise<LogoutLegResult> {
  try {
    const response = await fetch('/openmrs/ws/rest/v1/session', {
      method: 'DELETE',
      headers: { 'X-Daher-App-Request': '1' },
    })
    return response.ok ? 'ok' : 'failed'
  } catch {
    return 'failed'
  }
}

/**
 * OpenELIS enforces real Spring CSRF on /Logout (UI-005, reconfirmed
 * UI-011): a token can only come from a page OpenELIS itself rendered for
 * an authenticated session, never invented client-side. If no session is
 * live here, Home won't carry one — that is reported as 'skipped', not
 * forced, since there is nothing to log out of.
 */
async function logoutOpenElis(): Promise<LogoutLegResult> {
  let csrfToken: string | undefined
  try {
    const homeResponse = await fetch('/OpenELIS-Global/Home', { credentials: 'same-origin' })
    if (!homeResponse.ok) return 'skipped'
    const html = await homeResponse.text()
    csrfToken = html.match(/name="_csrf" value="([^"]+)"/)?.[1]
  } catch {
    return 'failed'
  }
  if (!csrfToken) return 'skipped'

  try {
    const response = await fetch('/OpenELIS-Global/Logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ _csrf: csrfToken }),
    })
    return response.ok ? 'ok' : 'failed'
  } catch {
    return 'failed'
  }
}

/**
 * Closes both apps' own local sessions. Does NOT touch the Keycloak SSO
 * session — UI-005/UI-011 measured that neither local logout does that on
 * its own, and OpenMRS's own local session was separately measured
 * (UI-011) to survive a Keycloak-side logout just as independently, so
 * skipping either local call here would leave that app's session alive
 * regardless of what happens to Keycloak next.
 */
export async function logoutLocalSessions(): Promise<CoordinatedLogoutResult> {
  const openmrs = await logoutOpenMrs()
  const openelis = await logoutOpenElis()
  return { openmrs, openelis }
}

/**
 * A real top-level navigation, not a fetch: UI-005/UI-011 measured that
 * fetching Keycloak's end-session endpoint without the browser actually
 * following/submitting it leaves the SSO session alive. This is the one
 * click UI-011 §11 keeps rather than shortcuts.
 */
export function navigateToKeycloakEndSession(): void {
  const keycloakOrigin = window.location.hostname.replace(/^app\./, 'id.')
  const postLogoutRedirect = `${window.location.protocol}//${window.location.host}/`
  const url = new URL(`${window.location.protocol}//${keycloakOrigin}/realms/daher/protocol/openid-connect/logout`)
  url.searchParams.set('client_id', 'openmrs')
  url.searchParams.set('post_logout_redirect_uri', postLogoutRedirect)
  window.location.assign(url.toString())
}
