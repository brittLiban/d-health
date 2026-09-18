import { createContext, useContext } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type { ExchangeIdentity, ExchangeMembershipSummary } from '../../api/exchangeTypes'

export type LabAuthStatus = 'loading' | 'unauthenticated' | 'ready' | 'no_labs' | 'error'

/**
 * DH-LAB-001 §10: capabilities derived from the *active* lab membership's own roles, never
 * invented, never a stand-in for backend authorization -- these gate which parts of the UI are
 * offered, not what the server ultimately permits. `LAB_TECH` and `LAB_RESULT_RELEASER` are
 * independent; a user may legitimately hold one, the other, or both.
 */
export interface LabCapabilities {
  canWorkOrders: boolean
  canReleaseResults: boolean
}

export interface LabContextValue {
  status: LabAuthStatus
  user: ExchangeIdentity | null
  activeLab: ExchangeMembershipSummary | null
  availableLabs: ExchangeMembershipSummary[]
  capabilities: LabCapabilities
  error: ExchangeApiError | null
  client: ExchangeClient
  setActiveLabId: (id: string) => void
  reload: () => Promise<void>
}

export const LabContext = createContext<LabContextValue | undefined>(undefined)

export function useLab(): LabContextValue {
  const context = useContext(LabContext)
  if (!context) {
    throw new Error('useLab must be used within a LabProvider')
  }
  return context
}

export { LabProvider, type LabProviderProps } from './LabProvider'
