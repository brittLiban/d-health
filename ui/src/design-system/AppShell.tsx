import type { ReactNode } from 'react'
import styles from './AppShell.module.css'

interface AppShellProps {
  navigation: ReactNode
  children: ReactNode
}

export function AppShell({ navigation, children }: AppShellProps) {
  return (
    <div className={styles.shell}>
      <header className={styles.topBar}>
        <p className={styles.brand}>DAHER HEALTH</p>
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
