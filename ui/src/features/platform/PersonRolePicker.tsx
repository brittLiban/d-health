/**
 * DH-MVP-ONBOARD-001 — find a person, decide what they may do.
 *
 * The same three gestures appear in two places: when a facility is enrolled and
 * its first administrator is attached, and when somebody is added to a facility
 * that already exists. They are the same gestures because they end in the same
 * thing — a membership with roles — so this is one component used twice rather
 * than two that will drift.
 *
 * It decides nothing. The search finds people who have already signed in and
 * grants them nothing; the role checkboxes are a presentation of
 * `ROLE_CHOICES_BY_TYPE` and not a gate. Whether a role may actually be granted
 * to an organization of this type is the server's rule, and it refuses an illegal
 * pairing regardless of what this screen offered.
 */

import { useState } from 'react'
import type { ExchangeClient } from '../../api/exchangeClient'
import type { IdentityLookupResult } from '../../api/exchangeTypes'
import { Button, TextInput } from '../../design-system'
import styles from './Platform.module.css'

export interface PersonRolePickerProps {
  client: ExchangeClient
  /** The roles this facility type can grant, already in French. */
  roleChoices: { role: string; label: string }[]
  selected: IdentityLookupResult | null
  onSelect: (person: IdentityLookupResult) => void
  /** The roles being asked for. Never the roles the person ends up with. */
  roles: string[]
  onRolesChange: (roles: string[]) => void
  /**
   * Roles the selected person already holds in this organization. They render
   * ticked and disabled: this screen adds authority and never removes it, and a
   * checkbox that cannot be unticked says that more honestly than its absence.
   */
  heldRoles?: string[]
  /**
   * People already in this organization, with their membership status, so a
   * search hit can say which. "Déjà dans l'équipe" about somebody whose access
   * was revoked would be the wrong fact.
   */
  existingMembers?: { identity_id: string; note: string }[]
  onError: (message: string | null) => void
}

export function PersonRolePicker({
  client,
  roleChoices,
  selected,
  onSelect,
  roles,
  onRolesChange,
  heldRoles = [],
  existingMembers = [],
  onError,
}: PersonRolePickerProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<IdentityLookupResult[] | null>(null)
  const [searching, setSearching] = useState(false)

  const search = async () => {
    if (query.trim().length < 3) {
      setResults(null)
      onError('Saisissez au moins trois caractères pour rechercher une personne.')
      return
    }
    setSearching(true)
    onError(null)
    try {
      setResults(await client.lookupIdentities(query.trim()))
    } catch {
      onError("La recherche n'a pas abouti. Vous pouvez réessayer.")
    } finally {
      setSearching(false)
    }
  }

  const memberNote = (identityId: string) =>
    existingMembers.find((member) => member.identity_id === identityId)?.note ?? null
  const alreadyMember = selected !== null && memberNote(selected.identity_id) !== null

  return (
    <div className={styles.stepBody}>
      {/* The bootstrap this ticket does not automate: there is no invitation
          system and no account provisioning, so the person must have signed in
          once before they can be attached. Saying so plainly is better than a
          search that mysteriously finds nobody. */}
      <p>
        Cette personne doit se connecter à DHealth une première fois avant de pouvoir être ajoutée.
        DHealth n'envoie pas d'invitation par courriel.
      </p>
      <div className={styles.searchRow}>
        <TextInput
          label="Rechercher une personne"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Button variant="secondary" onClick={() => void search()} disabled={searching}>
          Rechercher
        </Button>
      </div>

      {results !== null ? (
        results.length === 0 ? (
          <p>
            Aucune personne trouvée. Si elle ne s'est jamais connectée à DHealth, elle doit le faire
            une première fois.
          </p>
        ) : (
          <ul className={styles.resultList}>
            {results.map((result) => (
              <li key={result.identity_id}>
                <button
                  type="button"
                  className={
                    selected?.identity_id === result.identity_id
                      ? styles.resultSelected
                      : styles.resultRow
                  }
                  onClick={() => onSelect(result)}
                >
                  <span>{result.display_name ?? 'Nom non renseigné'}</span>
                  <span className={styles.facilityMeta}>
                    {result.email ?? ''}
                    {memberNote(result.identity_id) ? (
                      // Selecting them is still useful -- it is how roles get
                      // added -- but calling it "ajouter" afterwards would
                      // misdescribe somebody who was already there. And the note
                      // carries their status: "déjà dans l'équipe" about somebody
                      // whose access was revoked would be the wrong fact.
                      <> · {memberNote(result.identity_id)}</>
                    ) : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : null}

      {selected ? (
        <fieldset className={styles.fieldset}>
          <legend>
            {alreadyMember ? 'Rôles à ajouter pour ' : 'Rôles pour '}
            {selected.display_name ?? 'cette personne'}
          </legend>
          {roleChoices.map((choice) => {
            const held = heldRoles.includes(choice.role)
            return (
              <label key={choice.role} className={styles.radioRow}>
                <input
                  type="checkbox"
                  checked={held || roles.includes(choice.role)}
                  disabled={held}
                  onChange={(event) =>
                    onRolesChange(
                      event.target.checked
                        ? [...roles, choice.role]
                        : roles.filter((r) => r !== choice.role),
                    )
                  }
                />
                {choice.label}
                {held ? <span className={styles.facilityMeta}> — déjà accordé</span> : null}
              </label>
            )
          })}
          {/* Administration is not an operational authority, and the wording says
              so rather than leaving an operator to assume otherwise. */}
          <p className={styles.facilityMeta}>
            « Administration » permet de gérer l'établissement. Elle ne donne aucun droit clinique
            ni de laboratoire.
          </p>
        </fieldset>
      ) : null}
    </div>
  )
}
