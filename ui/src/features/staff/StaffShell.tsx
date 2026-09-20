import type { ReactNode } from 'react'
import { useWorkspace } from '../workspace/WorkspaceContext'
import type { StaffSection, StaffSectionId } from './staffSections'
import styles from './StaffShell.module.css'

export const STAFF_MAIN_CONTENT_ID = 'staff-main-content'

export interface StaffShellProps {
  sections: readonly StaffSection[]
  activeSection: StaffSectionId | null
  onSelectSection: (section: StaffSectionId) => void
  children: ReactNode
  onLogout?: () => void
}

/**
 * How a laboratory organization is described to the person using it. The word
 * matters: someone needs to know at a glance whether the action they are about
 * to take carries a care provider's authority or a laboratory's.
 */
function organizationKind(organizationType: string): string {
  if (organizationType === 'PROVIDER') return 'Établissement de soins'
  if (organizationType === 'LAB') return "Laboratoire d'analyses"
  return 'Établissement'
}

/**
 * The relay: two endpoints and a transfer between them. The product's one idea,
 * drawn once. Inline rather than an asset so it cannot fail to load, and
 * `aria-hidden` because the wordmark beside it already says the name.
 */
function BrandMark() {
  return (
    <svg
      className={styles.brandMark}
      width="22"
      height="22"
      viewBox="0 0 28 28"
      fill="none"
      aria-hidden="true"
    >
      <path d="M4 14h20" stroke="var(--color-primary)" strokeWidth="2" />
      <circle cx="4" cy="14" r="2.5" fill="var(--color-primary)" />
      <circle cx="14" cy="14" r="5" stroke="var(--color-primary)" strokeWidth="2" />
      <circle cx="14" cy="14" r="2" fill="var(--color-primary)" />
      <circle
        cx="24"
        cy="14"
        r="2.5"
        fill="var(--color-surface)"
        stroke="var(--color-primary)"
        strokeWidth="2"
      />
    </svg>
  )
}

/**
 * DH-MVP-POLISH-001 — one shell for the whole authenticated staff application.
 *
 * Replaces `ClinicShell` and `LabShell`, and with them the application-switch
 * buttons. Staff do not choose between products called DHealth Clinic and
 * DHealth Lab: they sign in once, and what they see follows from the
 * organization they have active and the roles they hold there. Changing
 * establishment changes context, never application.
 *
 * The masthead therefore carries the product name once, and then states the
 * thing that actually varies: which establishment the next request will be made
 * against, of what kind, and as whom. Acting under the wrong organization is the
 * most consequential mistake available here, so that is never hidden in a menu.
 */
export function StaffShell({
  sections,
  activeSection,
  onSelectSection,
  children,
  onLogout,
}: StaffShellProps) {
  const { user, organizations, activeOrganization, setActiveOrganizationId } = useWorkspace()

  return (
    <div className={styles.shell}>
      <a className={styles.skipLink} href={'#' + STAFF_MAIN_CONTENT_ID}>
        Aller au contenu principal
      </a>

      <header className={styles.masthead} aria-label="Session et établissement actif">
        <p className={styles.brand}>
          <BrandMark />
          DHealth
        </p>

        {activeOrganization ? (
          <div className={styles.identity}>
            <span className={styles.organization}>{activeOrganization.organization_name}</span>
            <span className={styles.organizationKind}>
              {organizationKind(activeOrganization.organization_type)}
            </span>
          </div>
        ) : null}

        <div className={styles.actions}>
          {organizations.length > 1 ? (
            <div className={styles.switcher}>
              <label className={styles.switcherLabel} htmlFor="staff-organization-switcher">
                Établissement actif
              </label>
              <select
                id="staff-organization-switcher"
                className={styles.select}
                value={activeOrganization?.organization_id ?? ''}
                onChange={(event) => setActiveOrganizationId(event.target.value)}
              >
                {organizations.map((organization) => (
                  <option key={organization.organization_id} value={organization.organization_id}>
                    {organization.organization_name}
                  </option>
                ))}
              </select>
            </div>
          ) : null}

          {user ? (
            <div className={styles.person}>
              <span className={styles.personName}>{user.display_name ?? user.email ?? 'Compte'}</span>
              {activeOrganization ? (
                // The roles actually held at the active organization, listed as the
                // server names them. Not a job title, and never a role the person
                // holds somewhere else.
                <span className={styles.personRoles}>{activeOrganization.roles.join(' · ')}</span>
              ) : null}
            </div>
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
        {/* Only sections this membership can actually use are rendered, so the
            navigation never lists an item that leads nowhere. */}
        <nav className={styles.nav} aria-label="Navigation principale">
          <ul className={styles.navList}>
            {sections.map((section) => (
              <li key={section.id}>
                <button
                  type="button"
                  className={[styles.item, section.id === activeSection ? styles.active : '']
                    .join(' ')
                    .trim()}
                  aria-current={section.id === activeSection ? 'page' : undefined}
                  onClick={() => onSelectSection(section.id)}
                >
                  {section.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <main id={STAFF_MAIN_CONTENT_ID} className={styles.main}>
          {children}
        </main>
      </div>
    </div>
  )
}
