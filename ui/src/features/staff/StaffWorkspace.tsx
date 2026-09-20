import { useMemo, useState } from 'react'
import { Button, EmptyState, ErrorState, LoadingSkeleton } from '../../design-system'
import type { ExchangeApiError } from '../../api/exchangeClient'
import type { ErrorCode } from '../../api/types'
import type { LabOrder, PatientCreateRequest } from '../../api/exchangeTypes'
import { ClinicProvider } from '../clinic/ClinicContext'
import { ClinicHomePage } from '../clinic/pages/ClinicHomePage'
import { ClinicOrdersPage } from '../clinic/pages/ClinicOrdersPage'
import { ClinicPatientsPage } from '../clinic/pages/ClinicPatientsPage'
import { ClinicResultsPage } from '../clinic/pages/ClinicResultsPage'
import { LabProvider } from '../lab/LabContext'
import { LabHomePage } from '../lab/pages/LabHomePage'
import { LabOrdersPage } from '../lab/pages/LabOrdersPage'
import { LabReleasePage } from '../lab/pages/LabReleasePage'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { StaffShell } from './StaffShell'
import { availableSections, resolveSection, type StaffSectionId } from './staffSections'

/** DH-IDENTITY-001: a request identity only, never a patient identity (§13/§28). */
export interface PendingPatientCreation {
  body: PatientCreateRequest
  idempotencyKey: string
  confirmedPatientId?: string
  confirmedPatientReferenceId?: string
}

export interface PendingOrderMutation {
  userId: string
  providerId: string
  patientReferenceId: string
  catalogEntryIds: string[]
  idempotencyKey: string
  createdAt: number
  confirmedOrder?: LabOrder
}

export interface StaffWorkspaceProps {
  initialSection?: StaffSectionId
  onLogout?: () => void
}

/** Narrows an exchange error onto the shared design-system error catalogue. */
function errorCodeForDisplay(error: ExchangeApiError | null): ErrorCode {
  switch (error?.code) {
    case 'NETWORK_ERROR':
      return 'NETWORK_ERROR'
    case 'NOT_FOUND':
      return 'NOT_FOUND'
    case 'CONFLICT':
      return 'CONFLICT'
    case 'VALIDATION_ERROR':
      return 'VALIDATION_ERROR'
    default:
      return 'SERVER_ERROR'
  }
}

/**
 * DH-MVP-POLISH-001 — the one authenticated staff surface.
 *
 * ── The organization switch, and why the content is keyed ──
 *
 * Every page below holds organization-scoped state and organization-scoped
 * requests in flight. Changing establishment while a request is outstanding must
 * never let the answer to the old question render as though it answered the new
 * one: a list of Clinique A's orders displayed under Clinique B's name is a
 * clinical misstatement, not a cosmetic glitch.
 *
 * The defence is the `key` on the content subtree. Changing establishment
 * changes the key, so React unmounts the whole subtree and mounts a fresh one.
 * That runs every `isMounted = false` and `controller.abort()` cleanup the pages
 * already have, drops every piece of scoped state, closes every open drawer, and
 * leaves a late response from the previous organization with no live component to
 * reach. One line, and it covers surfaces written before this ticket as well as
 * any added after it — worth far more than auditing each page for a generation
 * check it might forget to make.
 *
 * ── What deliberately survives that remount ──
 *
 * The two pending-mutation maps are held *above* the keyed subtree. They are
 * DH-CLINIC-003C's recovery record: non-PHI request identities that let a later
 * session reconcile a create whose response was never seen. Each entry is keyed
 * by identity and organization together, so a remount cannot leak one
 * establishment's pending work into another's view, and switching away and back
 * recovers it instead of silently losing the ability to reconcile.
 */
export function StaffWorkspace({ initialSection, onLogout }: StaffWorkspaceProps) {
  const workspace = useWorkspace()
  const { status, activeOrganization, capabilities, error, reload } = workspace

  const [requestedSection, setRequestedSection] = useState<StaffSectionId | null>(
    initialSection ?? null,
  )

  const [pendingPatientCreations, setPendingPatientCreations] = useState<
    Record<string, PendingPatientCreation>
  >({})
  const [pendingOrderMutations, setPendingOrderMutations] = useState<
    Record<string, PendingOrderMutation>
  >({})

  const sections = useMemo(
    () => availableSections(activeOrganization, capabilities),
    [activeOrganization, capabilities],
  )
  const activeSection = resolveSection(requestedSection, sections)

  const handleLogout = () => {
    // Drops the in-memory reconciliation records for this browser session. They
    // are deliberately not made durable here: a logout cannot establish whether
    // an earlier create committed, and the durable half of DH-CLINIC-003C's
    // recovery lives in its own storage module, scoped to user and organization.
    setPendingPatientCreations({})
    setPendingOrderMutations({})
    onLogout?.()
  }

  const setPendingPatientCreation = (
    scopeKey: string,
    pending: PendingPatientCreation | null,
  ) => {
    setPendingPatientCreations((current) => {
      if (pending) return { ...current, [scopeKey]: pending }
      const next = { ...current }
      delete next[scopeKey]
      return next
    })
  }

  const setPendingOrderMutation = (scopeKey: string, pending: PendingOrderMutation | null) => {
    setPendingOrderMutations((current) => {
      if (pending) return { ...current, [scopeKey]: pending }
      const next = { ...current }
      delete next[scopeKey]
      return next
    })
  }

  if (status === 'loading') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <LoadingSkeleton />
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        {/* The shared catalogue's own wording, reached through the same
            component every other surface uses. `UNAUTHORIZED`/`FORBIDDEN` cannot
            arrive here — a 401 resolves to the `unauthenticated` status above —
            so the remaining exchange codes map onto it directly, and anything
            unrecognised is reported as a server failure rather than guessed at. */}
        <ErrorState code={errorCodeForDisplay(error)} onRetry={() => void reload()} />
      </div>
    )
  }

  // A 401 is its own situation and must not be described as an empty account.
  // Authentication itself belongs to the gate above this component's parent
  // (`ExchangeAuthGate`), so this does not offer a second sign-in — it states
  // truthfully what happened and lets the person retry, which is what re-drives
  // the gate. Telling someone whose token merely expired that no establishment is
  // attached to their account would be false, and would send them to an
  // administrator to fix a problem they do not have.
  if (status === 'unauthenticated') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState
          title="Votre session a expiré"
          action={
            <Button
              variant="primary"
              onClick={() => {
                void reload()
              }}
            >
              Se reconnecter
            </Button>
          }
        >
          Reconnectez-vous pour reprendre votre travail.
        </EmptyState>
      </div>
    )
  }

  if (status === 'no_organizations' || !activeOrganization) {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        {/* One neutral statement covering both organization types. Someone who
            holds only a laboratory membership must never be told that no
            *clinic* is active — which is what the old per-application wording
            said. */}
        <EmptyState
          title="Aucun établissement actif n'est rattaché à votre compte"
          action={
            <Button
              variant="secondary"
              onClick={() => {
                void reload()
              }}
            >
              Réessayer
            </Button>
          }
        >
          Un administrateur de votre établissement doit vous y rattacher avant que vous puissiez
          travailler ici.
        </EmptyState>
      </div>
    )
  }

  const isProvider = activeOrganization.organization_type === 'PROVIDER'

  let content
  if (isProvider) {
    switch (activeSection) {
      case 'patients':
        content = (
          <ClinicPatientsPage
            pendingCreations={pendingPatientCreations}
            onPendingCreationChange={setPendingPatientCreation}
            pendingOrders={pendingOrderMutations}
            onPendingOrderChange={setPendingOrderMutation}
          />
        )
        break
      case 'orders':
        content = <ClinicOrdersPage />
        break
      case 'results':
        content = <ClinicResultsPage />
        break
      default:
        content = (
          <ClinicHomePage onNavigate={setRequestedSection} />
        )
    }
    content = <ClinicProvider>{content}</ClinicProvider>
  } else {
    switch (activeSection) {
      case 'orders':
        content = <LabOrdersPage />
        break
      case 'results':
        content = <LabReleasePage />
        break
      default:
        content = <LabHomePage />
    }
    content = <LabProvider>{content}</LabProvider>
  }

  return (
    <StaffShell
      sections={sections}
      activeSection={activeSection}
      onSelectSection={setRequestedSection}
      onLogout={handleLogout}
    >
      {/* See the note above: this key is the organization-switch safety mechanism. */}
      <div key={activeOrganization.organization_id}>{content}</div>
    </StaffShell>
  )
}
