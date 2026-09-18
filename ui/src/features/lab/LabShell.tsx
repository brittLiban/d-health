import type { ReactNode } from 'react'
import { useLab } from './LabContext'
import styles from './LabShell.module.css'

export type LabTabId = 'home' | 'orders'

export interface LabNavItem {
  id: LabTabId
  label: string
}

const LAB_NAV_ITEMS: LabNavItem[] = [
  { id: 'home', label: 'Accueil' },
  { id: 'orders', label: 'Ordres' },
]

export interface LabShellProps {
  activeTab: LabTabId
  onSelectTab: (tabId: LabTabId) => void
  children: ReactNode
  onLogout?: () => void
  onSwitchApp?: () => void
}

export function LabShell({ activeTab, onSelectTab, children, onLogout, onSwitchApp }: LabShellProps) {
  const { activeLab, availableLabs, setActiveLabId } = useLab()

  return (
    <div className={styles.shell}>
      <header className={styles.topBar}>
        <div className={styles.brandGroup}>
          <p className={styles.brand}>DHEALTH LABORATOIRE</p>
          {activeLab ? (
            <span className={styles.labBadge}>{activeLab.organization_name}</span>
          ) : null}
        </div>

        <div className={styles.actionsGroup}>
          {availableLabs.length > 1 ? (
            <select
              aria-label="Sélectionner le laboratoire"
              className={styles.labSelect}
              value={activeLab?.organization_id ?? ''}
              onChange={(e) => setActiveLabId(e.target.value)}
            >
              {availableLabs.map((l) => (
                <option key={l.organization_id} value={l.organization_id}>
                  {l.organization_name}
                </option>
              ))}
            </select>
          ) : null}

          {onSwitchApp ? (
            <button
              type="button"
              className={styles.logoutButton}
              onClick={onSwitchApp}
              aria-label="Passer à DHealth Clinique"
            >
              DHealth Clinique
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
        <nav className={styles.nav} aria-label="Navigation DHealth Laboratoire">
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'contents' }}>
            {LAB_NAV_ITEMS.map((item) => (
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

        <main id="lab-main-content" className={styles.main}>
          {children}
        </main>
      </div>
    </div>
  )
}
