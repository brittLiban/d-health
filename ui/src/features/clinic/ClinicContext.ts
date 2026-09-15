import { createContext, useContext } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type {
  ExchangeIdentity,
  ExchangeMembershipSummary,
} from '../../api/exchangeTypes'

export type ClinicAuthStatus =
  | 'loading'
  | 'unauthenticated'
  | 'ready'
  | 'no_clinics'
  | 'error'

export interface ClinicContextValue {
  status: ClinicAuthStatus
  user: ExchangeIdentity | null
  activeProvider: ExchangeMembershipSummary | null
  availableClinics: ExchangeMembershipSummary[]
  error: ExchangeApiError | null
  authToken: string | null
  client: ExchangeClient
  setActiveProviderId: (id: string) => void
  reload: () => Promise<void>
}

export const ClinicContext = createContext<ClinicContextValue | undefined>(undefined)

export function useClinic(): ClinicContextValue {
  const context = useContext(ClinicContext)
  if (!context) {
    throw new Error('useClinic must be used within a ClinicProvider')
  }
  return context
}

export { ClinicProvider, type ClinicProviderProps } from './ClinicProvider'
