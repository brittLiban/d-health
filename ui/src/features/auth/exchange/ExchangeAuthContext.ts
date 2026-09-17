import { createContext, useContext } from 'react'
import type { ExchangeClient } from '../../../api/exchangeClient'

export type ExchangeAuthStatus = 'initializing' | 'authenticating' | 'authenticated' | 'error'

export interface ExchangeAuthContextValue {
  status: ExchangeAuthStatus
  error: string | null
  /** Non-null only when status === 'authenticated'. */
  client: ExchangeClient | null
  login: () => void
  logout: () => void
  /** Re-runs initialization from scratch after a config/init failure. */
  retry: () => void
}

export const ExchangeAuthContext = createContext<ExchangeAuthContextValue | undefined>(undefined)

export function useExchangeAuth(): ExchangeAuthContextValue {
  const context = useContext(ExchangeAuthContext)
  if (!context) {
    throw new Error('useExchangeAuth must be used within an ExchangeAuthProvider')
  }
  return context
}
