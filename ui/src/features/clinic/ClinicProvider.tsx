import { useMemo, type ReactNode } from 'react'
import type { ExchangeMembershipSummary } from '../../api/exchangeTypes'
import { useOptionalWorkspace } from '../workspace/WorkspaceContext'
import { ClinicContext, type ClinicAuthStatus, type ClinicContextValue } from './ClinicContext'

export interface ClinicProviderProps {
  children: ReactNode
}

/**
 * DH-MVP-POLISH-001 — a projection of the staff workspace, not a session of its
 * own.
 *
 * This provider used to fetch `/me` itself, with the eligibility rule written
 * out twice inside it, and `LabProvider` did the same thing again next door.
 * `WorkspaceProvider` now loads the session exactly once and this adapter
 * re-expresses it in the shape the provider-side pages already consume, so
 * `useClinic()` keeps its contract and the pages behind it are untouched.
 *
 * It deliberately has no `client` prop and no fetch. A second way to establish a
 * session is a second thing that can disagree with the first about who is signed
 * in and where — which is the whole defect this ticket set out to remove. Mounted
 * without a workspace above it, this throws rather than quietly starting its own
 * session; tests mount the workspace (see `src/test/renderWithWorkspace.tsx`).
 */
export function ClinicProvider({ children }: ClinicProviderProps) {
  const workspace = useOptionalWorkspace()

  if (!workspace) {
    throw new Error(
      'ClinicProvider projects the staff workspace and must be rendered inside a ' +
        'WorkspaceProvider. It does not load a session of its own.',
    )
  }

  const value = useMemo<ClinicContextValue>(() => {
    const availableClinics = workspace.organizations.filter(
      (m: ExchangeMembershipSummary) => m.organization_type === 'PROVIDER',
    )

    // Only a *provider* organization can be the active clinic. When a laboratory
    // is active there is no active clinic, even though the same person may well
    // hold a clinician membership elsewhere — presenting one here would
    // misstate whose authority the next request would carry.
    const activeProvider =
      workspace.activeOrganization?.organization_type === 'PROVIDER'
        ? workspace.activeOrganization
        : null

    let status: ClinicAuthStatus
    switch (workspace.status) {
      case 'loading':
        status = 'loading'
        break
      case 'unauthenticated':
        status = 'unauthenticated'
        break
      case 'error':
        status = 'error'
        break
      case 'no_organizations':
        status = 'no_clinics'
        break
      case 'ready':
        // A loaded session with no provider organization active is, from a
        // provider page's point of view, the same situation as holding no clinic
        // at all: there is nothing here it can act on.
        status = activeProvider ? 'ready' : 'no_clinics'
        break
    }

    return {
      status,
      user: workspace.user,
      activeProvider,
      availableClinics,
      error: workspace.error,
      client: workspace.client,
      setActiveProviderId: workspace.setActiveOrganizationId,
      reload: workspace.reload,
    }
  }, [workspace])

  return <ClinicContext.Provider value={value}>{children}</ClinicContext.Provider>
}
