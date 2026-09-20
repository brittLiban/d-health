/**
 * DH-PATIENT-001 §13/§22/§23/§43: the patient access URL contract, and the *only* place in this
 * repository that reads or writes the bearer secret's transport form.
 *
 * **Why a fragment.** The raw grant secret travels as `#access=<token>`, never as `?access=`,
 * `?token=`, or a path segment. A URL fragment is not transmitted to the HTTP server as part of
 * the request, so it cannot land in reverse-proxy access logs, server routing, or a referrer path.
 * Caddy's `Referrer-Policy: same-origin` helps, but it is not the control that matters here --
 * fragment-plus-scrub is.
 *
 * **Why the origin comes from the browser.** `buildPatientAccessUrl` takes the current
 * application origin as an argument rather than reading a configured hostname: DH-PATIENT-001 §13
 * requires the link to be constructed locally, and inventing a production hostname the deployment
 * may not use would produce QR codes that resolve nowhere. It also keeps this function pure and
 * therefore directly testable.
 */

/** The fragment key the patient app reads its credential from. */
const ACCESS_FRAGMENT_KEY = 'access'

/**
 * Upper bound on an accepted credential, mirroring the adapter's own
 * `_MAX_PATIENT_BEARER_TOKEN_LENGTH` DoS guard rather than inventing a second, different one.
 * `secrets.token_urlsafe(32)` produces exactly 43 characters; this is generous for any legitimate
 * token while refusing to carry an unbounded attacker-supplied string into application state.
 */
const MAX_ACCESS_TOKEN_LENGTH = 512

/**
 * URL-safe base64 alphabet -- exactly what `secrets.token_urlsafe` emits. A value failing this
 * shape check is discarded client-side, but note carefully what that is and is not: it is a
 * cheap malformed-input guard, **not** validation. The server remains the only authority on
 * whether a credential is valid, and every rejection the patient ever sees is the same generic
 * message regardless of which side refused it (§25).
 */
const ACCESS_TOKEN_SHAPE = /^[A-Za-z0-9_-]+$/

export function buildPatientAccessUrl(origin: string, rawToken: string): string {
  const normalizedOrigin = origin.replace(/\/+$/, '')
  return `${normalizedOrigin}/?app=patient#${ACCESS_FRAGMENT_KEY}=${rawToken}`
}

/**
 * Extract the credential from a location fragment, or `null` when there is none/it is malformed.
 *
 * Deliberately tolerant of the shapes a real browser produces (`#access=x`, `#/access=x` after a
 * router rewrite, `#a=1&access=x`) and deliberately intolerant of anything that is not a plausible
 * opaque token.
 */
export function readAccessTokenFromFragment(hash: string): string | null {
  const fragment = hash.startsWith('#') ? hash.slice(1) : hash
  if (!fragment) return null

  const params = new URLSearchParams(fragment.replace(/^\/+/, ''))
  const candidate = params.get(ACCESS_FRAGMENT_KEY)
  if (!candidate) return null
  if (candidate.length > MAX_ACCESS_TOKEN_LENGTH) return null
  if (!ACCESS_TOKEN_SHAPE.test(candidate)) return null
  return candidate
}

/**
 * DH-PATIENT-001 §22 step 4: remove the credential from the visible address bar without
 * navigating, reloading, or adding a history entry.
 *
 * `?app=patient` is deliberately preserved: a refresh must land back on the patient app (where it
 * shows the "scan again" message of §23), not on the clinician-facing surface. Idempotent by
 * construction -- calling it with no fragment present rewrites the URL to the identical string --
 * which matters because React StrictMode double-invokes effects in development.
 */
export function scrubAccessFragment(windowRef: Window = window): void {
  const { history, location } = windowRef
  if (!history?.replaceState) return
  history.replaceState(null, '', `${location.pathname}${location.search}`)
}
