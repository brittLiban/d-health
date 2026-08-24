import { useState } from 'react'
import type { OpenMrsPatient } from './api/types'
import { AppShell, EmptyState, Navigation, type NavItem } from './design-system'
import { logoutLocalSessions, navigateToKeycloakEndSession } from './features/auth/logout'
import { PatientSearchPage } from './features/patients/PatientSearchPage'
import { PatientWorkspace } from './features/patients/PatientWorkspace'

// French-first navigation, per the shell in this ticket's brief. Only
// "Patients" is functional here — everything else is visible but honestly
// marked "Bientôt" rather than pretending to work
// (docs/UI-001-CLINICAL-UX.md §1: hiding is a UX convenience, not security;
// the same principle applies to feature availability).
const NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', label: 'Tableau de bord', available: false },
  { id: 'patients', label: 'Patients', available: true },
  { id: 'maternity', label: 'Maternité', available: false },
  { id: 'labour', label: 'Travail', available: false },
  { id: 'beds', label: 'Lits', available: false },
  { id: 'lab', label: 'Laboratoire', available: false },
  { id: 'results', label: 'Résultats', available: false },
]

function App() {
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

  let content
  if (activeNavId === 'patients') {
    content = selectedPatient ? (
      <PatientWorkspace patient={selectedPatient} onBack={() => setSelectedPatient(null)} />
    ) : (
      <PatientSearchPage onOpenPatient={setSelectedPatient} />
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
    >
      {content}
    </AppShell>
  )
}

export default App
