import { useState } from 'react'
import type { ExchangeClient } from '../../api/exchangeClient'
import { LabProvider } from './LabContext'
import { LabShell, type LabTabId } from './LabShell'
import { LabHomePage } from './pages/LabHomePage'
import { LabOrdersPage } from './pages/LabOrdersPage'
import { LabReleasePage } from './pages/LabReleasePage'

export interface LabAppProps {
  client?: ExchangeClient
  initialTab?: LabTabId
  onLogout?: () => void
  onSwitchApp?: () => void
}

function LabContent({
  initialTab = 'home',
  onLogout,
  onSwitchApp,
}: {
  initialTab?: LabTabId
  onLogout?: () => void
  onSwitchApp?: () => void
}) {
  const [activeTab, setActiveTab] = useState<LabTabId>(initialTab)

  let pageContent
  switch (activeTab) {
    case 'home':
      pageContent = <LabHomePage />
      break
    case 'orders':
      pageContent = <LabOrdersPage />
      break
    case 'release':
      pageContent = <LabReleasePage />
      break
    default:
      pageContent = <LabHomePage />
  }

  return (
    <LabShell activeTab={activeTab} onSelectTab={setActiveTab} onLogout={onLogout} onSwitchApp={onSwitchApp}>
      {pageContent}
    </LabShell>
  )
}

export function LabApp({ client, initialTab = 'home', onLogout, onSwitchApp }: LabAppProps) {
  return (
    <LabProvider client={client}>
      <LabContent initialTab={initialTab} onLogout={onLogout} onSwitchApp={onSwitchApp} />
    </LabProvider>
  )
}
