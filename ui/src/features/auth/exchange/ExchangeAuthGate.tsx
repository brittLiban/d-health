import type { ReactNode } from 'react'
import { Button } from '../../../design-system/Button'
import { EmptyState } from '../../../design-system/EmptyState'
import type { ExchangeAuthContextValue } from './ExchangeAuthContext'

export interface ExchangeAuthGateProps {
  auth: ExchangeAuthContextValue
  /** Human-facing app name for truthful, non-jargon messaging (e.g. "DHealth Clinique"). */
  appLabel: string
  children: ReactNode
}

/**
 * Shared, presentational auth gate (DH-CLINIC-004A). Renders `children` only
 * once `auth.status === 'authenticated'` -- the data layer underneath (e.g.
 * ClinicProvider) must never mount, and therefore never call `/me`, before
 * that. No OAuth/OIDC jargon reaches the user (§24): failures are reported as
 * "your session could not be established," never as token/PKCE internals.
 */
export function ExchangeAuthGate({ auth, appLabel, children }: ExchangeAuthGateProps) {
  if (auth.status === 'authenticated' && auth.client) {
    return <>{children}</>
  }

  if (auth.status === 'error') {
    return (
      <div role="alert">
        <EmptyState
          title="Connexion impossible"
          action={
            <Button type="button" variant="secondary" onClick={auth.retry}>
              Réessayer
            </Button>
          }
        >
          Votre session {appLabel} n'a pas pu être établie ou a expiré. Veuillez réessayer.
        </EmptyState>
      </div>
    )
  }

  const message =
    auth.status === 'authenticating'
      ? `Redirection vers la connexion ${appLabel}…`
      : `Initialisation de la session ${appLabel}…`

  return (
    <div role="status" aria-live="polite">
      <EmptyState title="Connexion en cours">{message}</EmptyState>
    </div>
  )
}
