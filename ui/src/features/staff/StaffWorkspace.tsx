import { useMemo, useState } from 'react'
import { Button, EmptyState, ErrorState, LoadingSkeleton } from '../../design-system'
import type { ExchangeApiError } from '../../api/exchangeClient'
import type { ErrorCode } from '../../api/types'
import type { LabOrder, PatientCreateRequest } from '../../api/exchangeTypes'
import { ClinicProvider } from '../clinic/ClinicContext'
import { FacilitySettingsPage } from '../facility/FacilitySettingsPage'
import { ClinicHomePage } from '../clinic/pages/ClinicHomePage'
import { ClinicOrdersPage } from '../clinic/pages/ClinicOrdersPage'
import { ClinicPatientsPage } from '../clinic/pages/ClinicPatientsPage'
import { ClinicResultsPage } from '../clinic/pages/ClinicResultsPage'
import { LabProvider } from '../lab/LabContext'
import { LabHomePage } from '../lab/pages/LabHomePage'
import { LabOrdersPage } from '../lab/pages/LabOrdersPage'
import { LabReleasePage } from '../lab/pages/LabReleasePage'
import { TeamAccessPage } from '../team/TeamAccessPage'
import { ClinicVisitsPage } from '../visits/ClinicVisitsPage'
import { BillingPage } from '../billing/BillingPage'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { StaffShell } from './StaffShell'
import {
  availablePlatformSections,
  availableSections,
  resolveSection,
  type PlatformSectionId,
  type StaffSectionId,
} from './staffSections'
import { PlatformWorkspace } from '../platform/PlatformWorkspace'

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
  /**
   * Starts a real sign-in. Supplied by `App.tsx` from the auth layer, because a
   * 401 on `/me` does not travel back up to `ExchangeAuthGate` on its own —
   * retrying the request would simply fail again.
   *
   * When it is absent there is deliberately no button. Offering "Se reconnecter"
   * with nothing behind it would be a control that looks like it works and does
   * not, which is the one thing this surface must never do.
   */
  onReauthenticate?: () => void
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
export function StaffWorkspace({
  initialSection,
  onLogout,
  onReauthenticate,
}: StaffWorkspaceProps) {
  const workspace = useWorkspace()
  const { status, activeOrganization, capabilities, error, reload, platformAdmin, scope } =
    workspace

  const [requestedSection, setRequestedSection] = useState<StaffSectionId | null>(
    initialSection ?? null,
  )

  const [pendingPatientCreations, setPendingPatientCreations] = useState<
    Record<string, PendingPatientCreation>
  >({})
  const [pendingOrderMutations, setPendingOrderMutations] = useState<
    Record<string, PendingOrderMutation>
  >({})

  /**
   * DHEALTH-CLINIC-VISIT-001: the visit a clinician is ordering for, while they are in `Patients`.
   *
   * Held at this level because it crosses two sections, and cleared by `ClinicPatientsPage` once it
   * has been consumed so that returning to the directory later is an ordinary visit-free visit. It
   * is *not* the durable record of the linkage — the backend freezes that on the order intent, and
   * the browser's own pending-order storage carries it across a reload. This is only the navigation
   * hand-off.
   */
  const [visitOrderContext, setVisitOrderContext] = useState<{
    patientId: string
    visitId: string
  } | null>(null)

  // Platform navigation state, held here beside the staff equivalent rather than
  // inside the platform tree, so switching scope and back does not lose where the
  // operator was.
  const [requestedPlatformSection, setRequestedPlatformSection] =
    useState<PlatformSectionId>("facilities")
  const [openFacilityId, setOpenFacilityId] = useState<string | null>(null)

  const sections = useMemo(
    () => availableSections(activeOrganization, capabilities),
    [activeOrganization, capabilities],
  )
  const activeSection = resolveSection(requestedSection, sections)

  const platformSections = useMemo(
    () => availablePlatformSections(platformAdmin),
    [platformAdmin],
  )
  const activePlatformSection: PlatformSectionId =
    platformSections.some((s) => s.id === requestedPlatformSection)
      ? requestedPlatformSection
      : "facilities"

  const handleLogout = () => {
    // Drops the in-memory reconciliation records for this browser session. They
    // are deliberately not made durable here: a logout cannot establish whether
    // an earlier create committed, and the durable half of DH-CLINIC-003C's
    // recovery lives in its own storage module, scoped to user and organization.
    setPendingPatientCreations({})
    setPendingOrderMutations({})
    setVisitOrderContext(null)
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
            arrive here — a 401 becomes the `unauthenticated` status handled just
            below — so the remaining exchange codes map onto it directly, and anything
            unrecognised is reported as a server failure rather than guessed at. */}
        <ErrorState code={errorCodeForDisplay(error)} onRetry={() => void reload()} />
      </div>
    )
  }

  // A 401 is its own situation and must not be described as an empty account:
  // telling someone whose token merely expired that no establishment is attached
  // to their account would send them to an administrator to fix a problem they do
  // not have.
  //
  // Retrying the request is *not* the remedy here, and this is why the button is
  // conditional. A 401 from `/me` does not travel back up to `ExchangeAuthGate`,
  // so calling `reload()` would re-issue the same request, receive the same 401
  // and return to this screen — a control that appears to work and does not. The
  // only thing that helps is a real sign-in, which belongs to the auth layer and
  // arrives here as `onReauthenticate`. Without it, the honest answer is the
  // sentence alone.
  if (status === 'unauthenticated') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState
          title="Votre session a expiré"
          action={
            onReauthenticate ? (
              <Button variant="primary" onClick={onReauthenticate}>
                Se reconnecter
              </Button>
            ) : undefined
          }
        >
          Reconnectez-vous pour reprendre votre travail.
        </EmptyState>
      </div>
    )
  }

  // DH-MVP-ONBOARD-001: platform scope, before the organization branches below.
  // A platform administrator may legitimately hold no membership at all, so this
  // must come first — otherwise the `no_organizations` branch would tell someone
  // who administers the whole network that nothing is attached to their account.
  // `activeOrganization` stays null throughout: network authority is not a
  // membership and never borrows one.
  if (scope === 'platform' && platformAdmin) {
    return (
      <StaffShell
        sections={platformSections}
        activeSection={activePlatformSection}
        onSelectSection={(section) => setRequestedPlatformSection(section as PlatformSectionId)}
        onLogout={handleLogout}
      >
        {/* Keyed on the scope for the same reason the organization subtree is
            keyed on its id: leaving platform scope must discard everything the
            network surfaces were holding. */}
        <div key="platform-scope">
          <PlatformWorkspace
            section={activePlatformSection}
            client={workspace.client}
            onOpenFacility={setOpenFacilityId}
            openFacilityId={openFacilityId}
          />
        </div>
      </StaffShell>
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
          {/* DH-MVP-ONBOARD-001: this screen is also the first thing a facility's
              intended administrator sees. There is no invitation system, so the
              bootstrap is that they sign in once and a *platform* administrator
              then attaches them — telling them to ask an administrator of their
              own facility sends them to somebody who does not exist yet.
              But the same screen also catches somebody whose membership was
              revoked or whose organization was suspended, so it must not claim
              this is a first sign-in either. It says what is true in both
              readings and names both ways out. */}
          Votre compte est connu de DHealth, mais aucun établissement actif n'y est rattaché. Un
          administrateur de la plateforme peut vous rattacher à votre établissement, ou en rétablir
          l'accès.
        </EmptyState>
      </div>
    )
  }

  const isProvider = activeOrganization.organization_type === 'PROVIDER'

  // DHEALTH-NATIVE-ACCESS-001: Équipe et accès sits *above* the provider/laboratory
  // branch, deliberately. Staff access administration is the same act at both kinds
  // of facility — the roles differ, and the screen reads them from the active
  // membership's own organization type — so it is one component rather than a
  // provider copy and a laboratory copy that would drift apart. It is also outside
  // `ClinicProvider`/`LabProvider`: those load clinical context this surface must not
  // pull in (§18), and wrapping it in either would fetch patients and orders to
  // administer a staff list.
  if (activeSection === 'team') {
    return (
      <StaffShell
        sections={sections}
        activeSection={activeSection}
        onSelectSection={(section) => setRequestedSection(section as StaffSectionId)}
        onLogout={handleLogout}
      >
        <div key={activeOrganization.organization_id}>
          <TeamAccessPage />
        </div>
      </StaffShell>
    )
  }

  // DHEALTH-BILLING-001: Facturation sits above the provider/laboratory branch for exactly the
  // reasons Équipe et accès does. Billing is the same act at both kinds of facility once a patient
  // is chosen — the two differ only in how they find one, which the page itself handles from the
  // active membership's own organization type. And it is outside `ClinicProvider`/`LabProvider`
  // deliberately: those load clinical context a cashier must not pull in, and wrapping the till in
  // either would fetch patients, orders and results in order to take a payment. A clerk holds no
  // clinical authority, so those fetches would also simply be refused.
  // DHEALTH-FACILITY-PROFILE-001: outside `ClinicProvider`/`LabProvider` for the same reason
  // `Facturation` is. Configuring the facility's own record needs no clinical context, and
  // wrapping it in either provider would fetch patients, orders and results in order to show a
  // timezone — fetches a `facility.configure` holder with no clinical authority would be refused
  // anyway. It is also before the provider/laboratory branch because the facility's record is the
  // same subject at both kinds of facility.
  if (activeSection === 'facility') {
    return (
      <StaffShell
        sections={sections}
        activeSection={activeSection}
        onSelectSection={(section) => setRequestedSection(section as StaffSectionId)}
        onLogout={handleLogout}
      >
        <div key={activeOrganization.organization_id}>
          <FacilitySettingsPage />
        </div>
      </StaffShell>
    )
  }

  if (activeSection === 'billing' || activeSection === 'prices') {
    return (
      <StaffShell
        sections={sections}
        activeSection={activeSection}
        onSelectSection={(section) => setRequestedSection(section as StaffSectionId)}
        onLogout={handleLogout}
      >
        <div key={activeOrganization.organization_id}>
          <BillingPage surface={activeSection === 'prices' ? 'prices' : 'till'} />
        </div>
      </StaffShell>
    )
  }

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
            orderContext={visitOrderContext}
            onOrderContextConsumed={() => setVisitOrderContext(null)}
          />
        )
        break
      case 'visits':
        content = (
          <ClinicVisitsPage
            onOrderForVisit={(context) => {
              // DHEALTH-CLINIC-VISIT-001: the hand-off lives here, in the one component that owns
              // both sections, rather than inside either page. `Visites` does not know that
              // `Patients` exists and `Patients` does not know it was opened from a visit — it is
              // handed a patient and a visit and behaves normally.
              setVisitOrderContext(context)
              setRequestedSection('patients')
            }}
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
      onSelectSection={(section) => setRequestedSection(section as StaffSectionId)}
      onLogout={handleLogout}
    >
      {/* See the note above: this key is the organization-switch safety mechanism. */}
      <div key={activeOrganization.organization_id}>{content}</div>
    </StaffShell>
  )
}
