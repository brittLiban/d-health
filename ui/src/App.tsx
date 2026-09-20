import { useState } from 'react'
import type { OpenMrsPatient } from './api/types'
import { AppShell, EmptyState, Navigation, type NavItem } from './design-system'
import { logoutLocalSessions, navigateToKeycloakEndSession } from './features/auth/logout'
import { ExchangeAuthGate, ExchangeAuthProvider, useExchangeAuth } from './features/auth/exchange'
import { MaternityBoardPage } from './features/maternity/MaternityBoardPage'
import { PatientApp } from './features/patient/PatientApp'
import { PatientSearchPage } from './features/patients/PatientSearchPage'
import { PatientWorkspace } from './features/patients/PatientWorkspace'
import { StaffApp } from './features/staff/StaffApp'

// French-first navigation for the reference-connector track. "Patients" and
// "Maternité" are functional; everything else is visible but honestly marked
// "Bientôt" rather than pretending to work
// (docs/UI-001-CLINICAL-UX.md §1: hiding is a UX convenience, not security;
// the same principle applies to feature availability).
//
// This list belongs to the preserved OpenMRS connector, not to the DHealth staff
// application. The staff navigation is derived from real authority and renders
// only sections that work — see features/staff/staffSections.ts.
const NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', label: 'Tableau de bord', available: false },
  { id: 'patients', label: 'Patients', available: true },
  { id: 'maternity', label: 'Maternité', available: true },
  { id: 'labour', label: 'Travail', available: false },
  { id: 'beds', label: 'Lits', available: false },
  { id: 'lab', label: 'Laboratoire', available: false },
  { id: 'results', label: 'Résultats', available: false },
]

/**
 * The three surfaces this bundle serves.
 *
 * `staff` is the single authenticated DHealth application (DH-MVP-POLISH-001).
 * It replaces what used to be two separate surfaces, `clinic` and `lab`: there
 * is no longer an application to choose, only an establishment to have active.
 * `maternity` is the preserved OpenMRS/OpenELIS reference connector, a distinct
 * track that keeps its own shell and its own realm. `patient` is the
 * order-scoped, unauthenticated patient status page.
 */
export type AppSurfaceId = 'staff' | 'maternity' | 'patient'

/**
 * `clinic` and `lab` are accepted and both resolve to `staff`.
 *
 * They are kept as aliases rather than removed because real URLs exist: the
 * Playwright specs open `/?app=clinic`, and anyone who bookmarked either surface
 * should land in the application rather than on a blank page. They name a
 * distinction the product no longer makes, so nothing new should use them.
 */
export type AppSurfaceAlias = AppSurfaceId | 'clinic' | 'lab'

function normalizeSurface(requested: string | null | undefined): AppSurfaceId | null {
  switch (requested) {
    case 'staff':
    case 'clinic':
    case 'lab':
      return 'staff'
    case 'maternity':
      return 'maternity'
    case 'patient':
      return 'patient'
    default:
      return null
  }
}

export interface AppProps {
  initialApp?: AppSurfaceAlias
}

/**
 * Consumes the shared native-auth layer and hands the staff application an
 * already-authenticated ExchangeClient — the staff app itself owns no OAuth/OIDC
 * (§9/AC7). Native logout terminates the Exchange Keycloak session specifically
 * (auth.logout), never the reference connector's OpenMRS/OpenELIS sessions.
 *
 * `appLabel` is the product, not a sub-application: the gate no longer has a
 * "DHealth Clinique" or "DHealth Laboratoire" to name, because a person's
 * memberships are not known until after they have signed in.
 */
function NativeStaff() {
  const auth = useExchangeAuth()
  return (
    <ExchangeAuthGate auth={auth} appLabel="DHealth">
      {auth.client ? <StaffApp client={auth.client} onLogout={auth.logout} /> : null}
    </ExchangeAuthGate>
  )
}

function App({ initialApp = 'staff' }: AppProps = {}) {
  const [activeApp, setActiveApp] = useState<AppSurfaceId>(() => {
    if (typeof window !== 'undefined' && window.location?.search) {
      const params = new URLSearchParams(window.location.search)
      const requested = normalizeSurface(params.get('app'))
      if (requested) return requested
    }
    return normalizeSurface(initialApp) ?? 'staff'
  })
  const [activeNavId, setActiveNavId] = useState('patients')
  const [selectedPatient, setSelectedPatient] = useState<OpenMrsPatient | null>(null)

  const handleSelectNav = (id: string) => {
    setActiveNavId(id)
    setSelectedPatient(null)
  }

  // §8: both local sessions first, then a real top-level navigation to
  // Keycloak's own end-session endpoint — UI-011 measured that neither
  // ordering can be skipped or collapsed into a background fetch.
  const handleLogout = () => {
    void logoutLocalSessions().finally(() => {
      navigateToKeycloakEndSession()
    })
  }

  // DHealth Patient (DH-PATIENT-001 §20/§21). This branch comes FIRST and returns before any
  // `ExchangeAuthProvider`/`ExchangeAuthGate` is constructed: a patient must never be redirected
  // to Keycloak, never need an account, and never be gated on an OIDC session (AC2). The
  // credential is the order-scoped `PatientAccessGrant` in the URL fragment, nothing else.
  if (activeApp === 'patient') {
    return <PatientApp />
  }

  // The single authenticated DHealth staff application, and the default surface
  // of this bundle (DH-MVP-POLISH-001 §25). Authenticates against the
  // dhealth-exchange realm, never the reference connector's daher realm —
  // ADR-050, ADR-049.
  if (activeApp === 'staff') {
    return (
      <ExchangeAuthProvider>
        <NativeStaff />
      </ExchangeAuthProvider>
    )
  }

  // Reference Connector track: OpenMRS/Maternity UI (ADR-042/043 preserved)
  let content
  if (activeNavId === 'patients') {
    content = selectedPatient ? (
      <PatientWorkspace patient={selectedPatient} onBack={() => setSelectedPatient(null)} />
    ) : (
      <PatientSearchPage onOpenPatient={setSelectedPatient} />
    )
  } else if (activeNavId === 'maternity') {
    // Unmounting MaternityBoardPage when a patient is opened, and
    // remounting it on "back", is what makes §9's refetch-on-entry work:
    // useMaternityBoard's mount effect fires again on remount — no polling,
    // no focus listener, the same pattern the Patients tab already uses.
    content = selectedPatient ? (
      <PatientWorkspace
        patient={selectedPatient}
        onBack={() => setSelectedPatient(null)}
        backLabel="← Retour à Maternité"
      />
    ) : (
      <MaternityBoardPage onOpenPatient={setSelectedPatient} />
    )
  } else {
    content = (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState title="Cet écran n'est pas encore disponible">
          Cette section sera activée dans une prochaine version.
        </EmptyState>
      </div>
    )
  }

  return (
    <AppShell
      navigation={<Navigation items={NAV_ITEMS} activeId={activeNavId} onSelect={handleSelectNav} />}
      onLogout={handleLogout}
      headerActions={
        // One link, to one application. This used to be two buttons, "DHealth
        // Clinique (Natif)" and "DHealth Laboratoire (Natif)", which asked the
        // user to know in advance which product their role belonged to. There is
        // now a single staff application that works that out from their
        // memberships. Leaving the connector is not a logout: unmounting the
        // Exchange auth provider drops only in-memory token state and must never
        // call Keycloak's end-session endpoint (§16/§7).
        <button
          type="button"
          onClick={() => setActiveApp('staff')}
          aria-label="Ouvrir l'application DHealth"
          style={{
            background: 'transparent',
            border: '1px solid var(--color-primary-contrast)',
            color: 'var(--color-primary-contrast)',
            borderRadius: 'var(--radius-sm)',
            minHeight: 'var(--touch-target-min)',
            padding: '0 var(--space-3)',
            cursor: 'pointer',
            fontFamily: 'inherit',
            fontSize: 'var(--font-size-base)',
          }}
        >
          Application DHealth
        </button>
      }
    >
      {content}
    </AppShell>
  )
}

export default App
