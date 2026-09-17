import { useState } from 'react'
import type { ExchangeClient } from '../../api/exchangeClient'
import type { LabOrder, PatientReferenceBindRequest } from '../../api/exchangeTypes'
import { ClinicProvider } from './ClinicContext'
import { ClinicShell, type ClinicTabId } from './ClinicShell'
import { ClinicHomePage } from './pages/ClinicHomePage'
import { ClinicOrdersPage } from './pages/ClinicOrdersPage'
import { ClinicPatientsPage } from './pages/ClinicPatientsPage'
import { ClinicResultsPage } from './pages/ClinicResultsPage'

export interface PendingPatientCreation {
  body: PatientReferenceBindRequest
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

export interface ClinicAppProps {
  client?: ExchangeClient
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
  const [pendingOrderMutations, setPendingOrderMutations] = useState<
    Record<string, PendingOrderMutation>
  >({})

  const handleLogout = () => {
    // Do not erase durable, non-PHI reconciliation identities. A logout cannot
    // establish whether an earlier create POST committed; the next authenticated
    // session in this exact user/provider scope must be able to reconcile it.
    setPendingPatientCreations({})
    setPendingOrderMutations({})
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

  const setPendingOrderMutation = (
    scopeKey: string,
    pending: PendingOrderMutation | null,
  ) => {
    setPendingOrderMutations((current) => {
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
          pendingOrders={pendingOrderMutations}
          onPendingOrderChange={setPendingOrderMutation}
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
  initialTab = 'home',
  onLogout,
  onSwitchApp,
}: ClinicAppProps) {
  return (
    <ClinicProvider client={client}>
      <ClinicContent
        initialTab={initialTab}
        onLogout={onLogout}
        onSwitchApp={onSwitchApp}
      />
    </ClinicProvider>
  )
}
