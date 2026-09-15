import type { ReactNode } from 'react'
import { useClinic } from './ClinicContext'
import styles from './ClinicShell.module.css'

export type ClinicTabId = 'home' | 'patients' | 'orders' | 'results'

export interface ClinicNavItem {
  id: ClinicTabId
  label: string
}

const CLINIC_NAV_ITEMS: ClinicNavItem[] = [
  { id: 'home', label: 'Accueil' },
  { id: 'patients', label: 'Patients' },
  { id: 'orders', label: 'Ordres de laboratoire' },
  { id: 'results', label: 'Résultats' },
]

export interface ClinicShellProps {
  activeTab: ClinicTabId
  onSelectTab: (tabId: ClinicTabId) => void
  children: ReactNode
  onLogout?: () => void
  onSwitchApp?: () => void
}

export function ClinicShell({
  activeTab,
  onSelectTab,
  children,
  onLogout,
  onSwitchApp,
}: ClinicShellProps) {
  const { activeProvider, availableClinics, setActiveProviderId } = useClinic()

  return (
    <div className={styles.shell}>
      <header className={styles.topBar}>
        <div className={styles.brandGroup}>
          <p className={styles.brand}>DHEALTH CLINIQUE</p>
          {activeProvider ? (
            <span className={styles.clinicBadge}>{activeProvider.organization_name}</span>
          ) : null}
        </div>

        <div className={styles.actionsGroup}>
          {availableClinics.length > 1 ? (
            <select
              aria-label="Sélectionner l'établissement"
              className={styles.clinicSelect}
              value={activeProvider?.organization_id ?? ''}
              onChange={(e) => setActiveProviderId(e.target.value)}
            >
              {availableClinics.map((c) => (
                <option key={c.organization_id} value={c.organization_id}>
                  {c.organization_name}
                </option>
              ))}
            </select>
          ) : null}

          {onSwitchApp ? (
            <button
              type="button"
              className={styles.logoutButton}
              onClick={onSwitchApp}
              aria-label="Passer au connecteur Maternité"
            >
              Mode Maternité (OpenMRS)
            </button>
          ) : null}

          {onLogout ? (
            <button
              type="button"
              className={styles.logoutButton}
              onClick={onLogout}
              aria-label="Se déconnecter"
            >
              Se déconnecter
            </button>
          ) : null}
        </div>
      </header>

      <div className={styles.body}>
        <nav className={styles.nav} aria-label="Navigation DHealth Clinique">
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'contents' }}>
            {CLINIC_NAV_ITEMS.map((item) => (
              <li key={item.id} style={{ display: 'contents' }}>
                <button
                  type="button"
                  className={[styles.item, item.id === activeTab ? styles.active : ''].join(' ')}
                  aria-current={item.id === activeTab ? 'page' : undefined}
                  onClick={() => onSelectTab(item.id)}
                >
                  <span>{item.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <main id="clinic-main-content" className={styles.main}>
          {children}
        </main>
      </div>
    </div>
  )
}
