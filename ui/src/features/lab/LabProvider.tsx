import { useMemo, type ReactNode } from 'react'
import type { ExchangeMembershipSummary } from '../../api/exchangeTypes'
import { useOptionalWorkspace } from '../workspace/WorkspaceContext'
import { Capability, can } from '../workspace/workspaceCapabilities'
import { LabContext, type LabAuthStatus, type LabContextValue } from './LabContext'

export interface LabProviderProps {
  children: ReactNode
}

/**
 * DH-MVP-POLISH-001 — a projection of the staff workspace, not a session of its
 * own. The provider-side twin of `ClinicProvider`; see that file for why both
 * stopped loading `/me` themselves.
 *
 * `canWorkOrders` and `canReleaseResults` come straight from the workspace's
 * capability derivation, which reads the *active* membership's roles. ADR-044
 * keeps result entry and result release as separate authorities, so a LAB_TECH
 * who does not also hold LAB_RESULT_RELEASER records values and cannot release
 * them — here as at the server.
 */
export function LabProvider({ children }: LabProviderProps) {
  const workspace = useOptionalWorkspace()

  if (!workspace) {
    throw new Error(
      'LabProvider projects the staff workspace and must be rendered inside a ' +
        'WorkspaceProvider. It does not load a session of its own.',
    )
  }

  const value = useMemo<LabContextValue>(() => {
    const availableLabs = workspace.organizations.filter(
      (m: ExchangeMembershipSummary) => m.organization_type === 'LAB',
    )

    const activeLab =
      workspace.activeOrganization?.organization_type === 'LAB'
        ? workspace.activeOrganization
        : null

    let status: LabAuthStatus
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
        status = 'no_labs'
        break
      case 'ready':
        status = activeLab ? 'ready' : 'no_labs'
        break
    }

    return {
      status,
      user: workspace.user,
      activeLab,
      availableLabs,
      capabilities: {
        // DHEALTH-ACCESS-CAPABILITIES-001: asked of the server-resolved capability set rather
        // than a role-derived boolean. A technician granted `lab.result.release` directly gets
        // the release surface without holding LAB_RESULT_RELEASER, which is the point.
        canWorkOrders: can(workspace.capabilities, Capability.LAB_QUEUE_WORK),
        canReleaseResults: can(workspace.capabilities, Capability.LAB_RESULT_RELEASE),
      },
      error: workspace.error,
      client: workspace.client,
      setActiveLabId: workspace.setActiveOrganizationId,
      reload: workspace.reload,
    }
  }, [workspace])

  return <LabContext.Provider value={value}>{children}</LabContext.Provider>
}
