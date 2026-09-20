import type { ExchangeClient } from '../../api/exchangeClient'
import { WorkspaceProvider } from '../workspace/WorkspaceProvider'
import { StaffWorkspace } from './StaffWorkspace'
import type { StaffSectionId } from './staffSections'

export interface StaffAppProps {
  /**
   * An already-authenticated client. `App.tsx` supplies the one built by
   * `ExchangeAuthProvider`; this component owns no OAuth/OIDC code of its own,
   * exactly as ClinicApp and LabApp did not before it (ADR-050).
   */
  client?: ExchangeClient
  initialSection?: StaffSectionId
  onLogout?: () => void
}

/**
 * DH-MVP-POLISH-001 — the single authenticated DHealth staff application.
 *
 * Replaces `ClinicApp` and `LabApp`. There is no longer a choice of application
 * to make: one shell, one session, and the organization a person has active plus
 * the roles they hold there decide which workflows are visible. Everything that
 * used to be reached by switching applications is reached by switching
 * establishment, which is a change of context rather than of product.
 *
 * The composition is deliberately this thin — a session above a workspace — so
 * there is exactly one place that loads `/me` and exactly one place that decides
 * what a membership unlocks.
 */
export function StaffApp({ client, initialSection, onLogout }: StaffAppProps) {
  return (
    <WorkspaceProvider client={client}>
      <StaffWorkspace initialSection={initialSection} onLogout={onLogout} />
    </WorkspaceProvider>
  )
}
