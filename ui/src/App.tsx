import { useState } from 'react'
import type { OpenMrsPatient } from './api/types'
import { AppShell, EmptyState, Navigation, type NavItem } from './design-system'
import { logoutLocalSessions, navigateToKeycloakEndSession } from './features/auth/logout'
import { ExchangeAuthGate, ExchangeAuthProvider, useExchangeAuth } from './features/auth/exchange'
import { ClinicApp } from './features/clinic/ClinicApp'
import { MaternityBoardPage } from './features/maternity/MaternityBoardPage'
import { PatientSearchPage } from './features/patients/PatientSearchPage'
import { PatientWorkspace } from './features/patients/PatientWorkspace'

// French-first navigation, per the shell in this ticket's brief. "Patients"
// and "Maternité" are functional; everything else is visible but honestly
// marked "Bientôt" rather than pretending to work
// (docs/UI-001-CLINICAL-UX.md §1: hiding is a UX convenience, not security;
// the same principle applies to feature availability).
const NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', label: 'Tableau de bord', available: false },
  { id: 'patients', label: 'Patients', available: true },
  { id: 'maternity', label: 'Maternité', available: true },
  { id: 'labour', label: 'Travail', available: false },
  { id: 'beds', label: 'Lits', available: false },
  { id: 'lab', label: 'Laboratoire', available: false },
  { id: 'results', label: 'Résultats', available: false },
]

export interface AppProps {
  initialApp?: 'maternity' | 'clinic'
}

/**
 * Consumes the shared native-auth layer and hands ClinicApp an already-
 * authenticated ExchangeClient — Clinic itself owns no OAuth/OIDC (§9/AC7).
 * Native logout terminates the Exchange Keycloak session specifically
 * (auth.logout), never the reference-connector's OpenMRS/OpenELIS sessions.
 */
function NativeClinic({ onSwitchApp }: { onSwitchApp: () => void }) {
  const auth = useExchangeAuth()
  return (
    <ExchangeAuthGate auth={auth} appLabel="DHealth Clinique">
      {auth.client ? (
        <ClinicApp client={auth.client} onLogout={auth.logout} onSwitchApp={onSwitchApp} />
      ) : null}
    </ExchangeAuthGate>
  )
}

function App({ initialApp = 'maternity' }: AppProps = {}) {
  const [activeApp, setActiveApp] = useState<'maternity' | 'clinic'>(() => {
    if (typeof window !== 'undefined' && window.location?.search) {
      const params = new URLSearchParams(window.location.search)
      if (params.get('app') === 'clinic') return 'clinic'
    }
    return initialApp
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

  // Native DHealth Clinic surface (DH-CLINIC-001/004A). Authenticates against
  // the dhealth-exchange realm, never the reference-connector's daher realm —
  // ADR-050, ADR-049. Switching back to the connector app is NOT the same
  // operation as logging out of Clinic (§16): unmounting ExchangeAuthProvider
  // below simply drops its in-memory token state; it must never itself call
  // Keycloak's logout endpoint.
  if (activeApp === 'clinic') {
    return (
      <ExchangeAuthProvider>
        <NativeClinic onSwitchApp={() => setActiveApp('maternity')} />
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
        <button
          type="button"
          onClick={() => setActiveApp('clinic')}
          aria-label="Basculer vers DHealth Clinique"
          style={{
            background: 'transparent',
            border: '1px solid var(--color-primary-contrast)',
            color: 'var(--color-primary-contrast)',
            borderRadius: '4px',
            padding: 'var(--space-1) var(--space-3)',
            cursor: 'pointer',
            fontSize: '0.9rem',
          }}
        >
          DHealth Clinique (Natif)
        </button>
      }
    >
      {content}
    </AppShell>
  )
}

export default App
