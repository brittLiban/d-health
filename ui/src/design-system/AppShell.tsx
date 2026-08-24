import type { ReactNode } from 'react'
import styles from './AppShell.module.css'

interface AppShellProps {
  navigation: ReactNode
  children: ReactNode
  onLogout?: () => void
}

export function AppShell({ navigation, children, onLogout }: AppShellProps) {
  return (
    <div className={styles.shell}>
      <header className={styles.topBar}>
        <p className={styles.brand}>DAHER HEALTH</p>
        {onLogout ? (
          <button type="button" className={styles.logoutButton} onClick={onLogout}>
            Se déconnecter
          </button>
        ) : null}
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
