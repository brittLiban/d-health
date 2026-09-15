import type { ReactNode } from 'react'
import styles from './AppShell.module.css'

interface AppShellProps {
  navigation: ReactNode
  children: ReactNode
  onLogout?: () => void
  headerActions?: ReactNode
}

export function AppShell({ navigation, children, onLogout, headerActions }: AppShellProps) {
  return (
    <div className={styles.shell}>
      <header className={styles.topBar}>
        <p className={styles.brand}>DAHER HEALTH</p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
          {headerActions}
          {onLogout ? (
            <button type="button" className={styles.logoutButton} onClick={onLogout}>
              Se déconnecter
            </button>
          ) : null}
        </div>
      </header>
      <div className={styles.body}>
        {navigation}
        <main id="main-content" className={styles.main}>
          {children}
        </main>
      </div>
    </div>
  )
}
