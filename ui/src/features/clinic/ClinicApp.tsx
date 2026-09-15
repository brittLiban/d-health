import { useState } from 'react'
import type { ExchangeClient } from '../../api/exchangeClient'
import type { PatientReferenceBindRequest } from '../../api/exchangeTypes'
import { ClinicProvider } from './ClinicContext'
import { clearAllStoredPendingPatients } from './pendingPatientStorage'
import { ClinicShell, type ClinicTabId } from './ClinicShell'
import { ClinicHomePage } from './pages/ClinicHomePage'
import { ClinicOrdersPage } from './pages/ClinicOrdersPage'
import { ClinicPatientsPage } from './pages/ClinicPatientsPage'
import { ClinicResultsPage } from './pages/ClinicResultsPage'

export interface PendingPatientCreation {
  body: PatientReferenceBindRequest
}

export interface ClinicAppProps {
  client?: ExchangeClient
  authToken?: string | null
  initialTab?: ClinicTabId
  onLogout?: () => void
  onSwitchApp?: () => void
}

function ClinicContent({
  initialTab = 'home',
  onLogout,
  onSwitchApp,
}: {
  initialTab?: ClinicTabId
  onLogout?: () => void
  onSwitchApp?: () => void
}) {
  const [activeTab, setActiveTab] = useState<ClinicTabId>(initialTab)
  const [pendingPatientCreations, setPendingPatientCreations] = useState<
    Record<string, PendingPatientCreation>
  >({})

  const handleLogout = () => {
    clearAllStoredPendingPatients()
    onLogout?.()
  }

  const setPendingPatientCreation = (
    scopeKey: string,
    pending: PendingPatientCreation | null,
  ) => {
    setPendingPatientCreations((current) => {
      if (pending) {
        return { ...current, [scopeKey]: pending }
      }
      const next = { ...current }
      delete next[scopeKey]
      return next
    })
  }

  let pageContent
  switch (activeTab) {
    case 'home':
      pageContent = <ClinicHomePage onNavigate={setActiveTab} />
      break
    case 'patients':
      pageContent = (
        <ClinicPatientsPage
          pendingCreations={pendingPatientCreations}
          onPendingCreationChange={setPendingPatientCreation}
        />
      )
      break
    case 'orders':
      pageContent = <ClinicOrdersPage />
      break
    case 'results':
      pageContent = <ClinicResultsPage />
      break
    default:
      pageContent = <ClinicHomePage onNavigate={setActiveTab} />
  }

  return (
    <ClinicShell
      activeTab={activeTab}
      onSelectTab={setActiveTab}
      onLogout={handleLogout}
      onSwitchApp={onSwitchApp}
    >
      {pageContent}
    </ClinicShell>
  )
}

export function ClinicApp({
  client,
  authToken,
  initialTab = 'home',
  onLogout,
  onSwitchApp,
}: ClinicAppProps) {
  return (
    <ClinicProvider client={client} authToken={authToken}>
      <ClinicContent
        initialTab={initialTab}
        onLogout={onLogout}
        onSwitchApp={onSwitchApp}
      />
    </ClinicProvider>
  )
}
