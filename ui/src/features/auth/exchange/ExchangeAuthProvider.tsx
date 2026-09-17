import Keycloak from 'keycloak-js'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ExchangeClient } from '../../../api/exchangeClient'
import { ExchangeAuthContext, type ExchangeAuthStatus } from './ExchangeAuthContext'
import { ExchangeAuthConfigError, resolveExchangeAuthConfig } from './exchangeAuthConfig'
import { createExchangeTokenProvider } from './exchangeTokenProvider'

export interface ExchangeAuthProviderProps {
  children: ReactNode
}

/**
 * Shared native-DHealth browser authentication foundation (DH-CLINIC-004A,
 * ADR-050). Owns the OIDC session against the `dhealth-exchange` realm's
 * public `dhealth-native-ui` client (Authorization Code + PKCE S256, via the
 * official `keycloak-js` adapter -- no hand-rolled OAuth). Clinic (and later
 * Lab) consume the resulting authenticated `ExchangeClient`; neither owns
 * OAuth itself.
 *
 * `onLoad: 'check-sso'`, not `'login-required'`: initialization must never
 * itself perform a full-page redirect as a side effect, or every truthful
 * loading/error state this ticket requires becomes unreachable and untestable
 * in jsdom. Login is a deliberate follow-up action this provider takes only
 * after `init()` has resolved and found no existing SSO session.
 */
export function ExchangeAuthProvider({ children }: ExchangeAuthProviderProps) {
  const [status, setStatus] = useState<ExchangeAuthStatus>('initializing')
  const [error, setError] = useState<string | null>(null)
  const [client, setClient] = useState<ExchangeClient | null>(null)
  const [attempt, setAttempt] = useState(0)
  const keycloakRef = useRef<Keycloak | null>(null)

  useEffect(() => {
    let cancelled = false

    let config
    try {
      config = resolveExchangeAuthConfig()
    } catch (err) {
      setStatus('error')
      setError(
        err instanceof ExchangeAuthConfigError
          ? err.message
          : 'Configuration DHealth Exchange invalide.',
      )
      return
    }

    const keycloak = new Keycloak(config)
    keycloakRef.current = keycloak

    const handleRefreshFailure = () => {
      if (cancelled) return
      setClient(null)
      setStatus('error')
      setError('La session DHealth Exchange a expiré. Veuillez vous reconnecter.')
    }

    keycloak.onAuthRefreshError = handleRefreshFailure
    keycloak.onAuthLogout = () => {
      if (cancelled) return
      setClient(null)
      setStatus('error')
      setError('Session DHealth Exchange terminée.')
    }

    keycloak
      .init({
        onLoad: 'check-sso',
        pkceMethod: 'S256',
        checkLoginIframe: false,
      })
      .then((authenticated) => {
        if (cancelled) return
        if (!authenticated) {
          setStatus('authenticating')
          void keycloak.login()
          return
        }
        setClient(
          new ExchangeClient({
            getAuthToken: createExchangeTokenProvider(keycloak, handleRefreshFailure),
          }),
        )
        setStatus('authenticated')
      })
      .catch(() => {
        if (cancelled) return
        setStatus('error')
        setError("Initialisation de l'authentification DHealth Exchange impossible.")
      })

    return () => {
      cancelled = true
    }
  }, [attempt])

  const login = useCallback(() => {
    void keycloakRef.current?.login()
  }, [])

  const logout = useCallback(() => {
    const keycloak = keycloakRef.current
    if (!keycloak) return
    const redirectUri =
      typeof window !== 'undefined'
        ? `${window.location.protocol}//${window.location.host}/`
        : undefined
    void keycloak.logout({ redirectUri })
  }, [])

  const retry = useCallback(() => {
    // Reset state here, from the event that caused the change, rather than
    // synchronously at the top of the effect -- keeps the effect a pure
    // reaction to `attempt` changing instead of also owning the reset step.
    setStatus('initializing')
    setError(null)
    setClient(null)
    setAttempt((n) => n + 1)
  }, [])

  return (
    <ExchangeAuthContext.Provider
      value={{
        status,
        error,
        client: status === 'authenticated' ? client : null,
        login,
        logout,
        retry,
      }}
    >
      {children}
    </ExchangeAuthContext.Provider>
  )
}
