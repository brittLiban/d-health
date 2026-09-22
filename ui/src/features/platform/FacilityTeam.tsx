/**
 * DH-MVP-ONBOARD-001 — who works at this facility, and what they may do.
 *
 * Enrollment can attach a first administrator, but onboarding does not end there.
 * A facility hires a second technician. An administrator leaves and somebody else
 * takes over. A facility is enrolled before the person meant to run it has ever
 * signed in, so there was nobody to attach at the time. Until this section
 * existed, each of those needed the API directly and a person's identity UUID to
 * go with it — which is not onboarding, it is a workaround.
 *
 * The endpoints are the ordinary organization-admin ones, which a platform
 * administrator also satisfies. Nothing here is a new authority model; this is
 * the screen for a capability the backend already had.
 *
 * Two things it deliberately is not. It does not remove anything: roles are
 * ensure/add on the server and no endpoint in this API revokes a role or a
 * membership, so there is no "Retirer" that would quietly do nothing. And it does
 * not reactivate: an inactive membership is shown, because losing the record
 * would be worse, but switching it back on is a separate action this product does
 * not yet have.
 */

import { useCallback, useEffect, useState } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type {
  FacilityEnrollment,
  IdentityLookupResult,
  OrganizationMembership,
} from '../../api/exchangeTypes'
import { Button, LoadingSkeleton } from '../../design-system'
import { PersonRolePicker } from './PersonRolePicker'
import { ROLE_CHOICES_BY_TYPE, membershipStatusLabel, roleLabel } from './onboardingVocabulary'
import styles from './Platform.module.css'

export interface FacilityTeamProps {
  facility: FacilityEnrollment
  client: ExchangeClient
  /** Called after a membership changes, so readiness is re-read from the server. */
  onChanged: () => void
}

export function FacilityTeam({ facility, client, onChanged }: FacilityTeamProps) {
  const [members, setMembers] = useState<OrganizationMembership[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [adding, setAdding] = useState(false)
  const [selected, setSelected] = useState<IdentityLookupResult | null>(null)
  const [roles, setRoles] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<string | null>(null)

  const organizationId = facility.organization_id

  const load = useCallback(async () => {
    setLoadError(false)
    try {
      setMembers(await client.listMemberships(organizationId))
    } catch {
      setLoadError(true)
    }
  }, [client, organizationId])

  useEffect(() => {
    void load()
  }, [load])

  const roleChoices = ROLE_CHOICES_BY_TYPE[facility.organization_type] ?? []

  // What the selected person already has here, if anything. Read from the list
  // the server returned, never from what was ticked a moment ago.
  const existing = selected
    ? (members ?? []).find((member) => member.identity_id === selected.identity_id)
    : undefined
  const existingActive = existing?.status === 'ACTIVE' ? existing : undefined
  const existingNotActive =
    existing !== undefined && existing.status !== 'ACTIVE' ? existing : undefined

  const closePanel = () => {
    setAdding(false)
    setSelected(null)
    setRoles([])
    setError(null)
  }

  const submit = async () => {
    if (!selected || roles.length === 0) return
    setSubmitting(true)
    setError(null)
    try {
      // Only the newly ticked roles go up. The held ones are already there and
      // re-sending them would be harmless but would misstate the request as
      // "these are their roles now", which is not what this endpoint means.
      const outcome = await client.createMembershipWithOutcome(organizationId, {
        identity_id: selected.identity_id,
        roles,
      })
      const who = outcome.membership.display_name ?? outcome.membership.email ?? 'Cette personne'
      // 201 and 200 are different events and get different sentences. Saying
      // "ajouté" about somebody who was already on the team would describe
      // something that did not happen.
      setConfirmation(
        outcome.created
          ? `${who} fait maintenant partie de l'équipe.`
          : `Rôles ajoutés pour ${who}.`,
      )
      closePanel()
      await load()
      // Staffing is a readiness condition — attaching an administrator is what
      // clears "Aucun responsable désigné" — so the verdict above is re-read
      // rather than left showing the state from before this change.
      onChanged()
    } catch (err) {
      const apiError = err as ExchangeApiError
      setError(
        apiError?.status === 403
          ? "Votre compte ne dispose pas des autorisations nécessaires pour cet établissement."
          : apiError?.status === 404
            ? "Cette personne ne s'est pas encore connectée à DHealth, ou l'établissement n'est plus accessible."
            : // 409 and 422 both say something specific and true — an inactive
              // membership, or a role this facility type cannot grant. The
              // server's own words beat a generic failure.
              (apiError?.detail ??
                "Le rattachement n'a pas abouti. Vous pouvez réessayer."),
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section aria-labelledby="facility-team-heading">
      <h2 id="facility-team-heading" className={styles.sectionTitle}>
        Équipe et accès
      </h2>
      <p className={styles.sectionLede}>
        Les personnes rattachées à cet établissement et ce que chacune peut y faire. Ajouter un
        rôle ici accorde une autorisation ; rien sur cet écran n'en retire une.
      </p>

      {confirmation ? (
        <p role="status" className={styles.done}>
          {confirmation}
        </p>
      ) : null}

      {loadError ? (
        <p role="alert">
          L'équipe n'a pas pu être chargée.{' '}
          <Button variant="secondary" onClick={() => void load()}>
            Réessayer
          </Button>
        </p>
      ) : members === null ? (
        <LoadingSkeleton />
      ) : members.length === 0 ? (
        <p className={styles.owed}>
          Personne n'est rattaché à cet établissement. Tant que c'est le cas, personne ne peut
          l'administrer ni y travailler.
        </p>
      ) : (
        <ul className={styles.teamList}>
          {members.map((member) => (
            <li key={member.membership_id} className={styles.teamRow}>
              <span className={styles.routeIdentity}>
                <span className={styles.routeLabName}>
                  {member.display_name ?? member.email ?? 'Nom non renseigné'}
                </span>
                <span className={styles.facilityMeta}>
                  {member.roles.length > 0
                    ? member.roles
                        .map((role) => roleLabel(facility.organization_type, role))
                        .join(' · ')
                    : // A membership with no role is a real state and reads as
                      // access when left blank. It is not access.
                      'Aucun rôle — cette personne ne peut rien faire ici'}
                </span>
              </span>
              <span className={member.status === 'ACTIVE' ? styles.done : styles.owed}>
                {membershipStatusLabel(member.status)}
              </span>
            </li>
          ))}
        </ul>
      )}

      {adding ? (
        <div className={styles.confirmPanel}>
          <h3 className={styles.subTitle}>Ajouter une personne</h3>

          {error ? (
            <p role="alert" className={styles.errorText}>
              {error}
            </p>
          ) : null}

          <PersonRolePicker
            client={client}
            roleChoices={roleChoices}
            selected={selected}
            onSelect={(person) => {
              setSelected(person)
              setRoles([])
              setError(null)
            }}
            roles={roles}
            onRolesChange={setRoles}
            heldRoles={existingActive?.roles ?? []}
            existingMembers={(members ?? []).map((member) => ({
              identity_id: member.identity_id,
              note:
                member.status === 'ACTIVE'
                  ? "Déjà dans l'équipe"
                  : // Not "déjà dans l'équipe": their record is here, their
                    // access is not, and the two must not read the same.
                    membershipStatusLabel(member.status),
            }))}
            onError={setError}
          />

          {existingNotActive ? (
            // Said before the attempt rather than as a 409 afterwards. The 409
            // is still handled, because the list can be stale.
            <p role="note" className={styles.owed}>
              Cette personne n'a plus accès à cet établissement (
              {membershipStatusLabel(existingNotActive.status).toLocaleLowerCase('fr')}). Rétablir
              son accès n'est pas possible depuis cet écran.
            </p>
          ) : null}

          <div className={styles.confirmActions}>
            <Button variant="secondary" onClick={closePanel}>
              Annuler
            </Button>
            <Button
              onClick={() => void submit()}
              disabled={
                submitting || !selected || roles.length === 0 || existingNotActive !== undefined
              }
            >
              {/* Keyed off the membership existing at all, not off it being
                  active: "Ajouter la personne" about somebody already recorded
                  here would be wrong even when the attempt is refused. */}
              {submitting
                ? 'Enregistrement…'
                : existing
                  ? 'Ajouter les rôles'
                  : 'Ajouter la personne'}
            </Button>
          </div>
        </div>
      ) : (
        <div className={styles.routeActions}>
          <Button
            variant="secondary"
            onClick={() => {
              setConfirmation(null)
              setAdding(true)
            }}
          >
            Ajouter une personne
          </Button>
        </div>
      )}
    </section>
  )
}
