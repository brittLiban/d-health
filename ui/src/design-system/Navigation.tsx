import styles from './Navigation.module.css'

export interface NavItem {
  id: string
  label: string
  available: boolean
}

interface NavigationProps {
  items: NavItem[]
  activeId: string
  onSelect: (id: string) => void
}

// A nav item with available: false still navigates — to an honest "not yet
// built" screen (see App.tsx) — rather than silently doing nothing. Hiding
// navigation is a UX convenience, never a security boundary
// (docs/UI-001-CLINICAL-UX.md §1, §24a): the same applies to feature
// availability, which is why this never fakes a working destination.
export function Navigation({ items, activeId, onSelect }: NavigationProps) {
  return (
    <nav className={styles.nav} aria-label="Navigation principale">
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'contents' }}>
        {items.map((item) => (
          <li key={item.id} style={{ display: 'contents' }}>
            <button
              type="button"
              className={[styles.item, item.id === activeId ? styles.active : ''].join(' ')}
              aria-current={item.id === activeId ? 'page' : undefined}
              onClick={() => onSelect(item.id)}
            >
              <span>{item.label}</span>
              {!item.available ? <span className={styles.badge}>Bientôt</span> : null}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  )
}
