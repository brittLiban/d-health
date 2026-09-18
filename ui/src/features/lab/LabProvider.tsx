import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  ExchangeApiError,
  ExchangeClient,
  exchangeClient as defaultExchangeClient,
} from '../../api/exchangeClient'
import type { ExchangeIdentity, ExchangeMembershipSummary } from '../../api/exchangeTypes'
import { LabContext, type LabAuthStatus, type LabCapabilities } from './LabContext'

export interface LabProviderProps {
  children: ReactNode
  client?: ExchangeClient
}

// DH-LAB-001 §9: eligibility is ACTIVE org + ACTIVE membership + LAB org type + at least one of
// the two lab roles -- never requiring both (§9's own explicit instruction).
function isEligibleLabMembership(m: ExchangeMembershipSummary): boolean {
  return (
    m.organization_type === 'LAB' &&
    m.organization_status === 'ACTIVE' &&
    m.membership_status === 'ACTIVE' &&
    (m.roles.includes('LAB_TECH') || m.roles.includes('LAB_RESULT_RELEASER'))
  )
}

function capabilitiesFor(m: ExchangeMembershipSummary | null): LabCapabilities {
  return {
    canWorkOrders: m !== null && m.roles.includes('LAB_TECH'),
    canReleaseResults: m !== null && m.roles.includes('LAB_RESULT_RELEASER'),
  }
}

export function LabProvider({ children, client = defaultExchangeClient }: LabProviderProps) {
  const [status, setStatus] = useState<LabAuthStatus>('loading')
  const [user, setUser] = useState<ExchangeIdentity | null>(null)
  const [availableLabs, setAvailableLabs] = useState<ExchangeMembershipSummary[]>([])
  const [activeLabId, setActiveLabIdState] = useState<string | null>(null)
  const [error, setError] = useState<ExchangeApiError | null>(null)

  const reload = useCallback(async () => {
    setStatus('loading')
    setError(null)

    try {
      const meResponse = await client.getMe()
      setUser({
        identity_id: meResponse.identity_id,
        issuer: meResponse.issuer,
        subject: meResponse.subject,
        display_name: meResponse.display_name,
        email: meResponse.email,
        platform_admin: meResponse.platform_admin,
      })

      const validLabs = meResponse.memberships.filter(isEligibleLabMembership)
      setAvailableLabs(validLabs)

      if (validLabs.length === 0) {
        setStatus('no_labs')
        setActiveLabIdState(null)
      } else {
        setStatus('ready')
        setActiveLabIdState((current) => {
          if (current && validLabs.some((l) => l.organization_id === current)) {
            return current
          }
          return validLabs[0].organization_id
        })
      }
    } catch (err) {
      if (err instanceof ExchangeApiError) {
        setError(err)
        setStatus(err.status === 401 ? 'unauthenticated' : 'error')
      } else {
        const fallbackError = new ExchangeApiError(
          0,
          'NETWORK_ERROR',
          err instanceof Error ? err.message : 'Erreur inattendue.',
        )
        setError(fallbackError)
        setStatus('error')
      }
    }
  }, [client])

  useEffect(() => {
    let isMounted = true

    client
      .getMe()
      .then((meResponse) => {
        if (!isMounted) return
        setUser({
          identity_id: meResponse.identity_id,
          issuer: meResponse.issuer,
          subject: meResponse.subject,
          display_name: meResponse.display_name,
          email: meResponse.email,
          platform_admin: meResponse.platform_admin,
        })

        const validLabs = meResponse.memberships.filter(isEligibleLabMembership)
        setAvailableLabs(validLabs)

        if (validLabs.length === 0) {
          setStatus('no_labs')
          setActiveLabIdState(null)
        } else {
          setStatus('ready')
          setActiveLabIdState((current) => {
            if (current && validLabs.some((l) => l.organization_id === current)) {
              return current
            }
            return validLabs[0].organization_id
          })
        }
      })
      .catch((err) => {
        if (!isMounted) return
        if (err instanceof ExchangeApiError) {
          setError(err)
          setStatus(err.status === 401 ? 'unauthenticated' : 'error')
        } else {
          const fallbackError = new ExchangeApiError(
            0,
            'NETWORK_ERROR',
            err instanceof Error ? err.message : 'Erreur inattendue.',
          )
          setError(fallbackError)
          setStatus('error')
        }
      })

    return () => {
      isMounted = false
    }
  }, [client])

  const activeLab = availableLabs.find((l) => l.organization_id === activeLabId) ?? null

  const handleSelectLabId = (id: string) => {
    if (availableLabs.some((l) => l.organization_id === id)) {
      setActiveLabIdState(id)
    }
  }

  return (
    <LabContext.Provider
      value={{
        status,
        user,
        activeLab,
        availableLabs,
        capabilities: capabilitiesFor(activeLab),
        error,
        client,
        setActiveLabId: handleSelectLabId,
        reload,
      }}
    >
      {children}
    </LabContext.Provider>
  )
}
