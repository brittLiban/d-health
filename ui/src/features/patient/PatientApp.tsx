/**
 * DH-PATIENT-001 §20-§23: DHealth Patient's root.
 *
 * **No OIDC, ever.** This component is mounted by `App.tsx` *before* any `ExchangeAuthProvider`
 * or `ExchangeAuthGate` exists in the tree, so no Keycloak adapter is ever constructed or
 * initialised on this path and no login redirect can occur (AC2). Authorization comes solely from
 * the order-scoped `PatientAccessGrant` the patient holds. Nothing in this file imports
 * `keycloak-js`, `features/auth/*`, or `ExchangeClient`.
 *
 * **The credential's whole life.** Read once from the URL fragment during the initial render,
 * held in component memory for as long as this component is mounted, and gone the moment it
 * unmounts. It is never written to `localStorage`, `sessionStorage`, IndexedDB, a cookie, a query
 * parameter, an analytics call, or the DOM (§23, AC6). The visible address bar is scrubbed
 * immediately after ingestion (§22, AC5).
 *
 * **The cost of that, accepted deliberately.** After the scrub, a page refresh has no credential
 * to find, so the patient is told to scan their QR again. That is the intended MVP trade-off
 * (§23): a link that survives refresh would have to be persisted somewhere, and there is no
 * storage location for a bearer secret on a shared or borrowed phone that this ticket is willing
 * to defend.
 */

import { useEffect, useRef, useState } from 'react'
import type { PatientOrder } from '../../api/exchangeTypes'
import styles from './Patient.module.css'
import { PatientAccessClient, PatientAccessError } from './patientAccessClient'
import { readAccessTokenFromFragment, scrubAccessFragment } from './patientAccessUrl'
import { PatientStatusPage } from './PatientStatusPage'

type LoadState =
  | { phase: 'no_token' }
  | { phase: 'loading' }
  | { phase: 'loaded'; order: PatientOrder }
  | { phase: 'invalid_access' }
  | { phase: 'unavailable' }

export interface PatientAppProps {
  client?: PatientAccessClient
  /** Test seam only -- production always reads the real browser location. */
  initialHash?: string
}

export function PatientApp({ client, initialHash }: PatientAppProps) {
  /*
   * Read in a lazy `useState` initialiser rather than in an effect. React StrictMode
   * double-invokes effects in development, so "read the fragment, then scrub it" inside a single
   * effect would have the second invocation find an already-scrubbed URL and conclude there is no
   * credential -- the patient app would be broken in dev only, in a way production would not
   * reproduce. Reading during render and scrubbing separately (an idempotent operation) keeps both
   * passes correct.
   */
  const [rawToken] = useState<string | null>(() =>
    readAccessTokenFromFragment(
      initialHash ?? (typeof window === 'undefined' ? '' : window.location.hash),
    ),
  )
  const [state, setState] = useState<LoadState>(rawToken ? { phase: 'loading' } : { phase: 'no_token' })
  const [reloadNonce, setReloadNonce] = useState(0)

  // A single client instance for this component's lifetime -- never a module-level singleton
  // holding patient state, and never shared with the clinician-side ExchangeClient.
  const clientRef = useRef<PatientAccessClient | null>(null)
  if (clientRef.current === null) {
    clientRef.current = client ?? new PatientAccessClient()
  }

  // §22 step 4: the credential leaves the address bar immediately, without a navigation, a
  // reload, or a new history entry.
  useEffect(() => {
    if (typeof window === 'undefined') return
    scrubAccessFragment(window)
  }, [])

  useEffect(() => {
    if (!rawToken) return
    const controller = new AbortController()
    let active = true

    setState({ phase: 'loading' })
    clientRef.current!
      .getOrder(rawToken, controller.signal)
      .then((order) => {
        // §50: a response arriving after unmount (or after the patient triggered a retry) must
        // never write to state.
        if (!active) return
        setState({ phase: 'loaded', order })
      })
      .catch((error: unknown) => {
        if (!active) return
        if (error instanceof DOMException && error.name === 'AbortError') return
        const kind = error instanceof PatientAccessError ? error.kind : 'unavailable'
        setState(kind === 'invalid_access' ? { phase: 'invalid_access' } : { phase: 'unavailable' })
      })

    return () => {
      active = false
      controller.abort()
    }
  }, [rawToken, reloadNonce])

  if (state.phase === 'loaded') {
    return <PatientStatusPage order={state.order} />
  }

  return (
    <main className={styles.screen}>
      <div className={styles.card}>
        <p className={styles.brand}>DHealth</p>
        <h1 className={styles.title}>Votre analyse</h1>
        <section role="status" aria-live="polite">
          {state.phase === 'loading' ? (
            <p className={styles.message}>Chargement…</p>
          ) : state.phase === 'no_token' ? (
            <p className={styles.message}>
              Pour rouvrir votre suivi, scannez de nouveau votre QR ou utilisez le lien fourni par
              votre clinique.
            </p>
          ) : state.phase === 'invalid_access' ? (
            /*
             * §25: one message for every credential failure -- unknown, malformed, expired,
             * revoked. An unauthenticated caller is never told which, and this component has no
             * information with which to tell them even if it wanted to.
             */
            <p className={styles.message}>
              Ce lien n’est plus valide. Contactez votre clinique pour obtenir un nouvel accès.
            </p>
          ) : (
            <>
              <p className={styles.message}>
                Connexion impossible pour le moment. Réessayez dans un instant.
              </p>
              <button
                type="button"
                className={styles.retry}
                onClick={() => setReloadNonce((value) => value + 1)}
              >
                Réessayer
              </button>
            </>
          )}
        </section>
      </div>
    </main>
  )
}
