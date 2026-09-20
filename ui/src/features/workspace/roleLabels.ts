/**
 * DH-MVP-POLISH-001 — what a person's authority is called on screen.
 *
 * The masthead used to render `roles.join(' · ')`, which put `CLINICIAN`,
 * `LAB_TECH` and `LAB_RESULT_RELEASER` in front of the user verbatim. This
 * ticket spent its length removing technical vocabulary from workflow state —
 * `Rectificatif` instead of `Version 2 — Amendée`, `Établissement de soins`
 * instead of `PROVIDER` — and then left the authorization enum sitting in the
 * identity band. Same defect, different table.
 *
 * The role *values* are untouched: they are what the server authorizes on, what
 * `workspaceCapabilities` derives from, and what every test asserts. This module
 * is presentation only, and nothing downstream reads its output.
 *
 * On `ORG_ADMIN`: it names authority the backend genuinely grants, so it is
 * labelled rather than hidden — dropping a granted authority from the identity
 * band would be its own inaccuracy. It is not a promise of a screen: this build
 * has no facility-administration surface at all (that is DH-MVP-ADMIN-001), and
 * the identity band says what someone *is* here, not what this build can do.
 */

/** Presentation order, so a dual-role line always reads the same way regardless
 *  of the order the API happened to return the roles in. */
const ROLE_ORDER = ['CLINICIAN', 'LAB_TECH', 'LAB_RESULT_RELEASER', 'ORG_ADMIN'] as const

const ROLE_LABELS: Record<(typeof ROLE_ORDER)[number], string> = {
  CLINICIAN: 'Clinicien',
  LAB_TECH: 'Analyse',
  LAB_RESULT_RELEASER: 'Libération',
  ORG_ADMIN: 'Administration',
}

function isKnownRole(role: string): role is (typeof ROLE_ORDER)[number] {
  return role in ROLE_LABELS
}

/**
 * A single role's human label.
 *
 * An unrecognised value falls back to itself. That is deliberate: if the backend
 * adds a fifth role, showing its code is a visible prompt to label it here,
 * whereas silently dropping it would hide an authority the person actually holds
 * and nobody would notice. The test suite pins all four known values, so the
 * fallback cannot quietly become the normal path for an existing role.
 */
export function roleLabel(role: string): string {
  return isKnownRole(role) ? ROLE_LABELS[role] : role
}

/**
 * The identity band's role line: human labels, in a stable order, separated the
 * way the rest of this masthead separates facts.
 *
 * `LAB_TECH` + `LAB_RESULT_RELEASER` therefore reads `Analyse · Libération`,
 * which is the two distinct authorities ADR-044 keeps separate — not one merged
 * "lab" role, and not a claim that either implies the other.
 */
export function formatRoleLabels(roles: readonly string[]): string {
  const known = ROLE_ORDER.filter((role) => roles.includes(role)).map((role) => ROLE_LABELS[role])
  const unknown = roles.filter((role) => !isKnownRole(role))
  return [...known, ...unknown].join(' · ')
}
