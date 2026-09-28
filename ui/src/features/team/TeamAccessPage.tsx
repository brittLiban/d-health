/**
 * DHEALTH-NATIVE-ACCESS-001 — Équipe et accès: who works here, and what they may do.
 *
 * The first facility-owned administration surface in DHealth. Before it, an
 * organization's administrator could be *granted* authority through the product and
 * could never withdraw it through the product: `FacilityTeam.tsx` said so in its own
 * docstring ("it does not remove anything… and it does not reactivate"), and
 * DHEALTH-NATIVE-SEC-001 recorded the consequence as a P1 — its qualification suite
 * had to reach into PostgreSQL with `psql` to produce a revoked membership, because
 * nothing an administrator could click would.
 *
 * ── Precision in what the copy claims ──
 *
 * The words on this screen are load-bearing, so two sentences that would be easy to
 * write are deliberately absent:
 *
 *   NOT "compte supprimé"  — nothing is deleted. The membership row survives, and
 *                            every result this person entered, reviewed or released
 *                            stays attributed to them. Deleting it would rewrite who
 *                            did what, which is the opposite of what an audit needs.
 *   NOT "utilisateur désactivé" — their DHealth sign-in still works, and their access
 *                            to *other* establishments is untouched. A physician who
 *                            works at two clinics does not lose their identity because
 *                            one of them let them go. Saying "utilisateur désactivé"
 *                            would tell an administrator they had done something far
 *                            larger than they did.
 *
 * What it says instead is what happened: access to *this* establishment was withdrawn.
 *
 * ── The confirmation gestures, and where they are not ──
 *
 * Three actions get an explicit restatement naming the person, the establishment and
 * the authority being removed: deactivating a membership, removing `ORG_ADMIN`, and
 * removing `LAB_RESULT_RELEASER` (ADR-044's release authority — the one whose loss
 * stops a laboratory being able to publish anything). Reactivation is confirmed too,
 * for a different reason: roles survive a deactivation, so restoring access restores
 * the authority the person left with, and that has to be stated before it happens
 * rather than discovered afterwards.
 *
 * Adding a role is one click and no dialogue. Making a routine grant ceremonious
 * trains people to click through the ceremony, which is exactly what must not happen
 * on the three above. Removing `CLINICIAN` or `LAB_TECH` is also one click: it takes
 * authority away, so the fail-safe direction is the easy one.
 *
 * ── This screen is not a security boundary ──
 *
 * Every control here is hidden or shown for usability. The server re-checks
 * `ORG_ADMIN` on every one of these requests, re-checks it *inside* the mutating
 * transaction, and refuses a hand-typed request from a clinician or a technician
 * regardless of what was rendered. It also refuses anything that would leave the
 * establishment with no active administrator — which is why the buttons here are
 * never disabled on that ground: the rule belongs to the backend, and a UI that
 * predicted it would eventually predict it wrongly.
 *
 * ── No clinical data ──
 *
 * Administering staff access needs no patients, no orders, no results and no visit
 * history, and this component loads none of them. The only request it makes is the
 * organization's own membership list.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ExchangeApiError } from '../../api/exchangeClient'
import type {
  MemberAccessResponse,
  MemberCapabilityView,
  OrganizationMembership,
} from '../../api/exchangeTypes'
import { Button, EmptyState, LoadingSkeleton } from '../../design-system'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { ROLE_CHOICES_BY_TYPE, roleLabel } from '../platform/onboardingVocabulary'
import styles from './Team.module.css'

/**
 * Which pending gesture is on screen, if any.
 *
 * One at a time by construction: an administrator confirming a withdrawal must be
 * reading one restatement, not choosing between several.
 */
type PendingAction =
  | { kind: 'deactivate'; member: OrganizationMembership }
  | { kind: 'reactivate'; member: OrganizationMembership }
  | { kind: 'removeRole'; member: OrganizationMembership; role: string }

/** The roles whose removal is restated before it happens. See the file header. */
const ROLES_REQUIRING_CONFIRMATION = new Set(['ORG_ADMIN', 'LAB_RESULT_RELEASER'])

function displayName(member: OrganizationMembership): string {
  // The login identity is a legitimate fallback and is already on this screen; a
  // membership with neither is a real state and must not render as blank.
  return member.display_name ?? member.email ?? 'Nom non renseigné'
}

/**
 * The server's own sentence wherever it said something specific and true, and a
 * DHealth sentence where it did not.
 *
 * The 409s matter most: "cette action laisserait l'établissement sans administrateur"
 * is a different situation from "cette personne n'a plus accès", and collapsing them
 * into "l'opération a échoué" would leave an administrator with no idea which of the
 * two happened or what to do next. The server's `detail` is used when it carries that
 * distinction, with a DHealth-worded lead so the reader is not handed a bare
 * backend string.
 */
function errorMessage(error: ExchangeApiError | undefined): string {
  if (error?.status === 403) {
    return "Vous n'avez plus les autorisations nécessaires dans cet établissement. Actualisez la page."
  }
  if (error?.status === 404) {
    return "Cette personne ne fait plus partie de cet établissement, ou l'établissement n'est plus accessible."
  }
  if (error?.status === 409) {
    return (
      error.detail ??
      "L'état de cet accès a changé entre-temps. Actualisez la liste et réessayez."
    )
  }
  if (error?.status === 422) {
    return error.detail ?? "Ce rôle ne peut pas être accordé dans cet établissement."
  }
  return "L'opération n'a pas abouti. Vous pouvez réessayer."
}

/**
 * The catalog, in the server's order, grouped under its own headings.
 *
 * The *order* is the server's: `CAPABILITY_CATALOG` lists capabilities in the order a facility
 * administrator reads them, and re-sorting here would throw that away and put `Facturation` before
 * `Patients` because F precedes P.
 */
function groupCapabilities(
  capabilities: readonly MemberCapabilityView[],
): [string, MemberCapabilityView[]][] {
  const groups = new Map<string, MemberCapabilityView[]>()
  for (const capability of capabilities) {
    const existing = groups.get(capability.group)
    if (existing) existing.push(capability)
    else groups.set(capability.group, [capability])
  }
  return [...groups.entries()]
}

export function TeamAccessPage() {
  const { activeOrganization, client, user, reload } = useWorkspace()

  const [members, setMembers] = useState<OrganizationMembership[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [pending, setPending] = useState<PendingAction | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<string | null>(null)

  // DHEALTH-ACCESS-CAPABILITIES-001: whose capability sheet is open, and what it says.
  //
  // One at a time, fetched on demand. The list endpoint deliberately does not carry every member's
  // capabilities: a staff list is read constantly and a capability sheet is opened occasionally,
  // and sending twelve capabilities per person on every list read would be paying for the rare
  // case on every request.
  const [openMember, setOpenMember] = useState<string | null>(null)
  const [access, setAccess] = useState<MemberAccessResponse | null>(null)

  const organizationId = activeOrganization?.organization_id ?? null
  const organizationName = activeOrganization?.organization_name ?? ''
  const organizationType = activeOrganization?.organization_type ?? ''

  const load = useCallback(async () => {
    if (!organizationId) return
    setLoadFailed(false)
    try {
      setMembers(await client.listMemberships(organizationId))
    } catch {
      setLoadFailed(true)
    }
  }, [client, organizationId])

  useEffect(() => {
    void load()
  }, [load])

  const roleChoices = useMemo(
    () => ROLE_CHOICES_BY_TYPE[organizationType] ?? [],
    [organizationType],
  )

  if (!activeOrganization || !organizationId) return null

  /**
   * Runs one mutation and re-reads the list from the server afterwards.
   *
   * The server's response for the single membership is deliberately not merged into
   * local state on its own: this screen's whole subject is *other people's* authority,
   * and a concurrent change by a second administrator must appear. Re-reading is one
   * request on a screen used a few times a year.
   *
   * `reload()` follows when the change touched the signed-in person, because the
   * masthead's own role line and this application's navigation both derive from `/me`.
   * An administrator who just removed their own `CLINICIAN` must not keep seeing a
   * Patients section the server would now refuse.
   */
  const run = async (
    action: () => Promise<OrganizationMembership>,
    describe: (member: OrganizationMembership) => string,
    affectsSelf: boolean,
  ) => {
    setBusy(true)
    setError(null)
    try {
      const updated = await action()
      setConfirmation(describe(updated))
      setPending(null)
      await load()
      if (affectsSelf) await reload()
    } catch (err) {
      setError(errorMessage(err as ExchangeApiError))
    } finally {
      setBusy(false)
    }
  }

  const isSelf = (member: OrganizationMembership) => member.identity_id === user?.identity_id

  /** Open (or close) one member's capability sheet, reading it fresh from the server. */
  const toggleAccess = async (member: OrganizationMembership) => {
    setError(null)
    setConfirmation(null)
    if (openMember === member.membership_id) {
      setOpenMember(null)
      setAccess(null)
      return
    }
    setOpenMember(member.membership_id)
    setAccess(null)
    try {
      setAccess(await client.getMemberAccess(organizationId, member.membership_id))
    } catch (err) {
      setError(errorMessage(err as ExchangeApiError))
      setOpenMember(null)
    }
  }

  /**
   * Grant, deny, or clear one capability, then re-read the sheet.
   *
   * Re-read rather than patched locally, for the reason the role mutations already give: this
   * screen's subject is *other people's* authority, a second administrator may be changing it at
   * the same time, and the source of a capability can change without its effective value changing
   * -- clearing a deny on somebody whose role grants it anyway leaves `effective` true and moves
   * `source` from `DENY` to `TEMPLATE:…`. Only the server knows that.
   */
  const changeCapability = async (
    member: OrganizationMembership,
    capability: MemberCapabilityView,
    action: 'GRANT' | 'DENY' | 'CLEAR',
  ) => {
    setBusy(true)
    setError(null)
    try {
      if (action === 'CLEAR') {
        await client.clearMemberCapability(organizationId, member.membership_id, capability.key)
      } else {
        await client.setMemberCapability(
          organizationId,
          member.membership_id,
          capability.key,
          action,
        )
      }
      setAccess(await client.getMemberAccess(organizationId, member.membership_id))
      setConfirmation(
        action === 'CLEAR'
          ? `${displayName(member)} : « ${capability.label} » suit de nouveau ses rôles.`
          : action === 'GRANT'
            ? `${displayName(member)} : « ${capability.label} » autorisé.`
            : `${displayName(member)} : « ${capability.label} » refusé.`,
      )
      await load()
      // The masthead and the navigation both derive from `/me`, so an administrator who just
      // changed their *own* authority must see the application follow -- including losing a
      // section they can no longer use.
      if (isSelf(member)) await reload()
    } catch (err) {
      setError(errorMessage(err as ExchangeApiError))
    } finally {
      setBusy(false)
    }
  }

  /** `✓ Autorisé · Via Clinicien`, `✓ Autorisé · Accordé directement`, `✗ Refusé · Exception
   *  individuelle`, `✗ Non autorisé`. §20: never only the answer, always the reason. */
  const describeSource = (capability: MemberCapabilityView): string => {
    if (capability.source === 'GRANT') return 'Accordé directement'
    if (capability.source === 'DENY') return 'Exception individuelle'
    if (capability.source?.startsWith('TEMPLATE:')) {
      const role = capability.source.slice('TEMPLATE:'.length)
      return `Via ${roleLabel(organizationType, role)}`
    }
    return "Aucun rôle ne l'accorde"
  }

  const addRole = (member: OrganizationMembership, role: string) =>
    run(
      () => client.addMembershipRole(organizationId, member.membership_id, role),
      (updated) =>
        `${displayName(updated)} : ${roleLabel(organizationType, role)} ajouté dans ${organizationName}.`,
      isSelf(member),
    )

  const removeRole = (member: OrganizationMembership, role: string) =>
    run(
      () => client.removeMembershipRole(organizationId, member.membership_id, role),
      (updated) =>
        `${displayName(updated)} : ${roleLabel(organizationType, role)} retiré dans ${organizationName}.`,
      isSelf(member),
    )

  const deactivate = (member: OrganizationMembership) =>
    run(
      () => client.deactivateMembership(organizationId, member.membership_id),
      (updated) =>
        // "n'a plus accès à", never "a été supprimé" or "a été désactivé": the record
        // is intact and the person's DHealth account is untouched.
        `${displayName(updated)} n'a plus accès à ${organizationName}.`,
      isSelf(member),
    )

  const reactivate = (member: OrganizationMembership) =>
    run(
      () => client.reactivateMembership(organizationId, member.membership_id),
      (updated) =>
        `${displayName(updated)} a de nouveau accès à ${organizationName}` +
        (updated.roles.length > 0
          ? ` avec : ${updated.roles.map((role) => roleLabel(organizationType, role)).join(' · ')}.`
          : // A reactivated membership holding no role is access to the establishment
            // and authority to do nothing in it. Said plainly rather than left as an
            // empty list the reader has to interpret.
            ", sans aucun rôle : cette personne ne peut encore rien y faire."),
      isSelf(member),
    )

  const requestRoleRemoval = (member: OrganizationMembership, role: string) => {
    setConfirmation(null)
    setError(null)
    if (ROLES_REQUIRING_CONFIRMATION.has(role)) {
      setPending({ kind: 'removeRole', member, role })
      return
    }
    void removeRole(member, role)
  }

  const activeAdministrators = (members ?? []).filter(
    (member) => member.status === 'ACTIVE' && member.roles.includes('ORG_ADMIN'),
  ).length

  return (
    <div className={styles.page}>
      <header>
        <h1 className={styles.pageTitle}>Équipe et accès</h1>
        <p className={styles.lede}>
          Les personnes rattachées à {organizationName} et ce que chacune peut y faire. Retirer un
          rôle ou un accès ici ne vaut que pour cet établissement : le compte DHealth de la personne
          reste actif, et ses accès dans d'autres établissements ne changent pas.
        </p>
      </header>

      {confirmation ? (
        <p role="status" className={styles.status}>
          {confirmation}
        </p>
      ) : null}

      {error ? (
        <p role="alert" className={styles.errorText}>
          {error}
        </p>
      ) : null}

      {pending ? <ConfirmPanel
        pending={pending}
        organizationName={organizationName}
        organizationType={organizationType}
        busy={busy}
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (pending.kind === 'deactivate') void deactivate(pending.member)
          else if (pending.kind === 'reactivate') void reactivate(pending.member)
          else void removeRole(pending.member, pending.role)
        }}
      /> : null}

      {loadFailed ? (
        <p role="alert">
          L'équipe n'a pas pu être chargée.{' '}
          <Button variant="secondary" onClick={() => void load()}>
            Réessayer
          </Button>
        </p>
      ) : members === null ? (
        <LoadingSkeleton />
      ) : members.length === 0 ? (
        <EmptyState title="Personne n'est rattaché à cet établissement">
          Tant que c'est le cas, personne ne peut y travailler.
        </EmptyState>
      ) : (
        <ul className={styles.memberList}>
          {members.map((member) => {
            const active = member.status === 'ACTIVE'
            const heldChoices = roleChoices.filter((choice) => member.roles.includes(choice.role))
            const availableChoices = roleChoices.filter(
              (choice) => !member.roles.includes(choice.role),
            )
            return (
              <li
                key={member.membership_id}
                className={`${styles.memberRow} ${active ? '' : styles.memberRowInactive}`}
              >
                <span className={styles.identity}>
                  <span className={styles.name}>
                    {displayName(member)}
                    {isSelf(member) ? ' (vous)' : ''}
                  </span>
                  {/* The login identity, which §7 keeps as a required staff field.
                      No phone, no address, nothing this screen does not need. */}
                  {member.email ? <span className={styles.meta}>{member.email}</span> : null}
                </span>

                <span className={active ? styles.active : styles.inactive}>
                  {/* "Accès actif" / "Accès retiré", not "Actif" / "Inactif": the
                      subject is the access, not the person. */}
                  {active ? 'Accès actif' : 'Accès retiré'}
                </span>

                <span className={styles.roleList}>
                  {heldChoices.length === 0 ? (
                    <span className={styles.noRoles}>
                      Aucun rôle — cette personne ne peut rien faire ici
                    </span>
                  ) : (
                    heldChoices.map((choice) => (
                      <span key={choice.role} className={styles.roleChip}>
                        {choice.label}
                        <Button
                          variant="secondary"
                          onClick={() => requestRoleRemoval(member, choice.role)}
                          disabled={busy}
                        >
                          {/* Worded, never an icon: an unlabelled × beside an
                              authority is the one control on this screen nobody
                              should have to guess at. */}
                          Retirer
                        </Button>
                      </span>
                    ))
                  )}
                </span>

                <span className={styles.actions}>
                  {active ? (
                    <>
                      {availableChoices.length > 0 ? (
                        <span className={styles.addPanel}>
                          {availableChoices.map((choice) => (
                            <Button
                              key={choice.role}
                              variant="secondary"
                              onClick={() => {
                                setConfirmation(null)
                                setError(null)
                                void addRole(member, choice.role)
                              }}
                              disabled={busy}
                            >
                              Ajouter {choice.label.toLocaleLowerCase('fr')}
                            </Button>
                          ))}
                        </span>
                      ) : null}
                      <Button
                        variant="secondary"
                        onClick={() => {
                          setConfirmation(null)
                          setError(null)
                          setPending({ kind: 'deactivate', member })
                        }}
                        disabled={busy}
                      >
                        Retirer l'accès
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setConfirmation(null)
                        setError(null)
                        setPending({ kind: 'reactivate', member })
                      }}
                      disabled={busy}
                    >
                      Rétablir l'accès
                    </Button>
                  )}
                  <Button variant="secondary" onClick={() => void toggleAccess(member)}>
                    {openMember === member.membership_id
                      ? 'Masquer les autorisations'
                      : 'Voir les autorisations'}
                  </Button>
                </span>

                {openMember === member.membership_id ? (
                  access === null ? (
                    <LoadingSkeleton />
                  ) : (
                    <section
                      className={styles.accessSheet}
                      aria-label={`Autorisations — ${displayName(member)}`}
                    >
                      <p className={styles.lede}>
                        Ce que cette personne peut faire ici. Un rôle est un modèle : vous pouvez
                        accorder une autorisation en plus, ou en retirer une que son rôle donne.
                      </p>
                      {groupCapabilities(access.capabilities).map(([group, entries]) => (
                        <div key={group}>
                          <h3 className={styles.groupTitle}>{group}</h3>
                          <ul className={styles.capabilityList}>
                            {entries.map((capability) => (
                              <li key={capability.key} className={styles.capabilityRow}>
                                <span className={styles.identity}>
                                  <span className={styles.name}>
                                    {capability.effective ? '✓ ' : '✗ '}
                                    {capability.label}
                                  </span>
                                  <span className={styles.meta}>{capability.description}</span>
                                  <span
                                    className={
                                      capability.effective ? styles.active : styles.inactive
                                    }
                                  >
                                    {describeSource(capability)}
                                  </span>
                                </span>
                                <span className={styles.actions}>
                                  {capability.source === 'GRANT' ||
                                  capability.source === 'DENY' ? (
                                    <Button
                                      variant="secondary"
                                      disabled={busy}
                                      onClick={() =>
                                        void changeCapability(member, capability, 'CLEAR')
                                      }
                                    >
                                      Suivre le rôle
                                    </Button>
                                  ) : null}
                                  {capability.effective ? (
                                    <Button
                                      variant="secondary"
                                      disabled={busy}
                                      onClick={() =>
                                        void changeCapability(member, capability, 'DENY')
                                      }
                                    >
                                      Refuser
                                    </Button>
                                  ) : (
                                    <Button
                                      variant="secondary"
                                      disabled={busy}
                                      onClick={() =>
                                        void changeCapability(member, capability, 'GRANT')
                                      }
                                    >
                                      Autoriser
                                    </Button>
                                  )}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </section>
                  )
                ) : null}
              </li>
            )
          })}
        </ul>
      )}

      {members !== null && members.length > 0 ? (
        <p className={styles.lede}>
          {/* Stated as a fact, not as a warning attached to a disabled button. The
              backend refuses the operation that would take this number to zero; this
              line is so an administrator can see it coming rather than meet it as a
              refusal. */}
          {activeAdministrators === 1
            ? "Cet établissement n'a qu'un seul administrateur actif. DHealth refusera de retirer ce rôle ou cet accès tant qu'une autre personne n'aura pas reçu le rôle Administration."
            : `Administrateurs actifs : ${activeAdministrators}.`}
        </p>
      ) : null}
    </div>
  )
}

/**
 * The restatement, separated out so the three confirmable gestures cannot each grow
 * their own wording.
 *
 * Every panel names the person, the establishment and the specific authority. None of
 * them says "Êtes-vous sûr ?", which asks nothing and tells nothing — a reader who
 * cannot see what is about to change from the text has not been given a chance to
 * catch a mistake.
 */
function ConfirmPanel({
  pending,
  organizationName,
  organizationType,
  busy,
  onCancel,
  onConfirm,
}: {
  pending: PendingAction
  organizationName: string
  organizationType: string
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const who = displayName(pending.member)

  let title: string
  let body: string
  let detail: string
  let confirmLabel: string

  if (pending.kind === 'deactivate') {
    title = "Retirer l'accès à cet établissement"
    body = `${who} n'aura plus accès à ${organizationName}.`
    detail =
      "Son compte DHealth reste actif et ses accès dans d'autres établissements ne changent pas. " +
      'Tout ce qu\'elle a déjà saisi, revu ou libéré reste enregistré à son nom. ' +
      (pending.member.roles.length > 0
        ? `Ses rôles (${pending.member.roles
            .map((role) => roleLabel(organizationType, role))
            .join(' · ')}) sont conservés et lui seraient rendus si son accès était rétabli.`
        : '')
    confirmLabel = "Retirer l'accès"
  } else if (pending.kind === 'reactivate') {
    title = "Rétablir l'accès à cet établissement"
    body = `${who} aura de nouveau accès à ${organizationName}.`
    detail =
      pending.member.roles.length > 0
        ? // The consequence §9 requires to be explicit: reactivation is not a blank
          // slate, it restores the authority the membership still carries.
          `Les rôles conservés lui seront rendus : ${pending.member.roles
            .map((role) => roleLabel(organizationType, role))
            .join(' · ')}. Pour lui rendre un accès plus limité, retirez d'abord les rôles concernés.`
        : "Cette personne n'a aucun rôle ici : elle retrouvera l'accès à l'établissement sans pouvoir y faire quoi que ce soit."
    confirmLabel = "Rétablir l'accès"
  } else {
    const label = roleLabel(organizationType, pending.role)
    title = `Retirer le rôle ${label}`
    body = `${who} perdra le rôle ${label} dans ${organizationName}.`
    detail =
      pending.role === 'ORG_ADMIN'
        ? "Cette personne ne pourra plus gérer l'équipe et les accès de cet établissement. Le changement prend effet immédiatement, sans qu'elle ait besoin de se reconnecter."
        : // LAB_RESULT_RELEASER: ADR-044 keeps release separate from entry, and this
          // is the authority whose loss stops results reaching anyone.
          'Cette personne ne pourra plus libérer de résultats. Elle pourra continuer à saisir des ' +
          'résultats si elle a le rôle Analyse. Le changement prend effet immédiatement, sans ' +
          "qu'elle ait besoin de se reconnecter."
    confirmLabel = `Retirer ${label}`
  }

  return (
    <section className={styles.confirmPanel} aria-labelledby="team-confirm-title">
      <h2 id="team-confirm-title" className={styles.confirmTitle}>
        {title}
      </h2>
      <p className={styles.confirmBody}>{body}</p>
      {detail ? <p className={styles.confirmDetail}>{detail}</p> : null}
      <div className={styles.confirmActions}>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>
          Annuler
        </Button>
        <Button onClick={onConfirm} disabled={busy}>
          {busy ? 'Enregistrement…' : confirmLabel}
        </Button>
      </div>
    </section>
  )
}
