import {
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import {
  ExchangeApiError,
  ExchangeClient,
  exchangeClient as defaultExchangeClient,
} from '../../api/exchangeClient'
import type {
  ExchangeIdentity,
  ExchangeMembershipSummary,
} from '../../api/exchangeTypes'
import {
  ClinicContext,
  type ClinicAuthStatus,
} from './ClinicContext'

export interface ClinicProviderProps {
  children: ReactNode
  client?: ExchangeClient
}

export function ClinicProvider({
  children,
  client = defaultExchangeClient,
}: ClinicProviderProps) {
  const [status, setStatus] = useState<ClinicAuthStatus>('loading')
  const [user, setUser] = useState<ExchangeIdentity | null>(null)
  const [availableClinics, setAvailableClinics] = useState<ExchangeMembershipSummary[]>([])
  const [activeProviderId, setActiveProviderId] = useState<string | null>(null)
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

      // Finding F-08: Filter strictly for active provider organizations with active clinician role
      const validClinics = meResponse.memberships.filter(
        (m) =>
          m.organization_type === 'PROVIDER' &&
          m.organization_status === 'ACTIVE' &&
          m.membership_status === 'ACTIVE' &&
          m.roles.includes('CLINICIAN'),
      )

      setAvailableClinics(validClinics)

      if (validClinics.length === 0) {
        setStatus('no_clinics')
        setActiveProviderId(null)
      } else {
        setStatus('ready')
        setActiveProviderId((current) => {
          if (current && validClinics.some((c) => c.organization_id === current)) {
            return current
          }
          return validClinics[0].organization_id
        })
      }
    } catch (err) {
      if (err instanceof ExchangeApiError) {
        setError(err)
        if (err.status === 401) {
          setStatus('unauthenticated')
        } else {
          setStatus('error')
        }
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

        const validClinics = meResponse.memberships.filter(
          (m) =>
            m.organization_type === 'PROVIDER' &&
            m.organization_status === 'ACTIVE' &&
            m.membership_status === 'ACTIVE' &&
            m.roles.includes('CLINICIAN'),
        )

        setAvailableClinics(validClinics)

        if (validClinics.length === 0) {
          setStatus('no_clinics')
          setActiveProviderId(null)
        } else {
          setStatus('ready')
          setActiveProviderId((current) => {
            if (current && validClinics.some((c) => c.organization_id === current)) {
              return current
            }
            return validClinics[0].organization_id
          })
        }
      })
      .catch((err) => {
        if (!isMounted) return
        if (err instanceof ExchangeApiError) {
          setError(err)
          if (err.status === 401) {
            setStatus('unauthenticated')
          } else {
            setStatus('error')
          }
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

  const activeProvider =
    availableClinics.find((c) => c.organization_id === activeProviderId) ?? null

  const handleSelectProviderId = (id: string) => {
    if (availableClinics.some((c) => c.organization_id === id)) {
      setActiveProviderId(id)
    }
  }

  return (
    <ClinicContext.Provider
      value={{
        status,
        user,
        activeProvider,
        availableClinics,
        error,
        client,
        setActiveProviderId: handleSelectProviderId,
        reload,
      }}
    >
      {children}
    </ClinicContext.Provider>
  )
}
