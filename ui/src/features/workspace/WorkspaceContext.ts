/**
 * DH-MVP-POLISH-001 — the one staff session context.
 *
 * There is a single authenticated DHealth staff application. It does not ask
 * anyone to choose between "DHealth Clinic" and "DHealth Lab": the person signs
 * in once, and the organization they have active plus the roles they hold there
 * decide which workflows exist. `/me` is loaded once, here, and everything else
 * projects from it.
 *
 * `generation` increments on every change of active organization. It exists so
 * an org-scoped async surface can tell whether the response it is holding still
 * belongs to the organization on screen. The primary defence against a late
 * response rendering under the wrong organization is remounting the org-scoped
 * subtree on a `key`, which runs every existing cleanup and drops every piece of
 * scoped state; `generation` is the second line, for anything that legitimately
 * outlives a remount.
 */

import { createContext, useContext } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type {
  ExchangeIdentity,
  ExchangeMembershipSummary,
} from '../../api/exchangeTypes'
import type { WorkspaceCapabilities } from './workspaceCapabilities'

/**
 * `no_organizations` replaces the old per-app `no_clinics`/`no_labs`. One
 * status, because the honest statement is about the account, not about which
 * application someone happened to open: a user who holds only a laboratory
 * membership must never be told that no *clinic* is active.
 */
export type WorkspaceStatus =
  | 'loading'
  | 'unauthenticated'
  | 'ready'
  | 'no_organizations'
  | 'error'

/**
 * DH-MVP-ONBOARD-001 — platform scope is not an organization.
 *
 * A DHealth platform administrator may legitimately hold no membership anywhere:
 * they administer the network, not a facility. The temptation is to invent a
 * "DHealth Headquarters" organization so the existing shell has something to put
 * in `activeOrganization`, and that would be a lie with consequences — every
 * capability check, every audit row and every org-scoped read would then be
 * answering questions about a facility that does not exist.
 *
 * So scope is explicit. In `platform` scope `activeOrganization` is genuinely
 * `null`, and the network surfaces read from platform endpoints that take no
 * organization. A person holding both platform authority and memberships keeps
 * both, and switches between them deliberately; platform context never
 * masquerades as a membership.
 */
export type WorkspaceScope = 'organization' | 'platform'

export interface WorkspaceContextValue {
  status: WorkspaceStatus
  user: ExchangeIdentity | null
  /** Every membership that unlocks a workflow this build renders, both types. */
  organizations: ExchangeMembershipSummary[]
  activeOrganization: ExchangeMembershipSummary | null
  capabilities: WorkspaceCapabilities
  error: ExchangeApiError | null
  client: ExchangeClient
  /** True when this identity holds DHealth platform authority. Never derived
   *  from a membership, and never implied by ORG_ADMIN anywhere. */
  platformAdmin: boolean
  /** Which of the two scopes is on screen. `platform` only ever when
   *  `platformAdmin` is true; `activeOrganization` is null throughout it. */
  scope: WorkspaceScope
  setScope: (scope: WorkspaceScope) => void
  /** Bumped on every accepted change of active organization, and on every scope
   *  change, because both invalidate every scoped async surface on screen. */
  generation: number
  setActiveOrganizationId: (organizationId: string) => void
  reload: () => Promise<void>
}

export const WorkspaceContext = createContext<WorkspaceContextValue | undefined>(
  undefined,
)

export function useWorkspace(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext)
  if (!context) {
    throw new Error('useWorkspace must be used within a WorkspaceProvider')
  }
  return context
}

/**
 * Non-throwing form, for the two provider adapters that must be able to say
 * "no workspace above me" as a diagnosable error rather than crash inside a
 * hook call.
 */
export function useOptionalWorkspace(): WorkspaceContextValue | undefined {
  return useContext(WorkspaceContext)
}

export type { WorkspaceCapabilities } from './workspaceCapabilities'
