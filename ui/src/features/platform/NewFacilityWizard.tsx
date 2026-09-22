/**
 * DH-MVP-ONBOARD-001 — enrolling a facility, as a journey rather than a form.
 *
 * Enrollment is not "create organization, done". A partner agrees, the facility
 * is identified, an organization is created, somebody is made responsible for it,
 * and only then does how they participate get decided. A single page of fields
 * would flatten that into data entry and lose the order the real conversation has.
 *
 * The steps adapt: a Native facility is never asked what LIS it runs, and a
 * Connect facility is never walked through configuring a Native result workflow
 * it will not use. Asking either would be asking somebody to invent an answer.
 */

import { useState } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type {
  FacilityEnrollment,
  FacilityEnrollmentCreated,
  IdentityLookupResult,
  ParticipationMode,
} from '../../api/exchangeTypes'
import { Button, TextInput } from '../../design-system'
import { PersonRolePicker } from './PersonRolePicker'
import {
  ROLE_CHOICES_BY_TYPE,
  organizationTypeLabel,
  participationModeDescription,
  roleLabel,
} from './onboardingVocabulary'
import styles from './Platform.module.css'

export interface NewFacilityWizardProps {
  client: ExchangeClient
  onCancel: () => void
  onEnrolled: (facility: FacilityEnrollment) => void
}

type StepId = 'facility' | 'participation' | 'responsible' | 'review'

const STEP_LABELS: Record<StepId, string> = {
  facility: 'Établissement',
  participation: 'Mode de participation',
  responsible: 'Responsable',
  review: 'Validation',
}

const STEP_ORDER: StepId[] = ['facility', 'participation', 'responsible', 'review']

export function NewFacilityWizard({ client, onCancel, onEnrolled }: NewFacilityWizardProps) {
  const [step, setStep] = useState<StepId>('facility')
  const [name, setName] = useState('')
  const [organizationType, setOrganizationType] = useState<'PROVIDER' | 'LAB'>('PROVIDER')
  const [participationMode, setParticipationMode] = useState<ParticipationMode | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Set only from a successful response, and everything it renders comes from
  // that response rather than from this component's own form state.
  const [enrolled, setEnrolled] = useState<FacilityEnrollmentCreated | null>(null)

  // Step 3's identity lookup, in the same component the facility screen uses to
  // add somebody later. The search finds people who have already signed in; it
  // grants nothing, and this wizard makes that explicit by treating selection as
  // "who we will attach once the facility exists", never as access.
  const [selected, setSelected] = useState<IdentityLookupResult | null>(null)
  const [roles, setRoles] = useState<string[]>(['ORG_ADMIN'])

  const submit = async () => {
    if (!participationMode) return
    setSubmitting(true)
    setError(null)
    try {
      // One request, one transaction. The first administrator is attached by the
      // same call that creates the facility, so there is no window in which the
      // organization exists and the person meant to run it was never granted
      // anything -- and no partial outcome for this screen to explain.
      //
      // The roles go up exactly as chosen. Which roles are legal for which
      // organization type is the server's rule; restating it here would be a
      // second copy to drift, and hiding a control is not a gate anyway.
      const facility = await client.enrolFacility({
        name: name.trim(),
        organization_type: organizationType,
        participation_mode: participationMode,
        ...(selected && roles.length > 0
          ? { first_administrator: { identity_id: selected.identity_id, roles } }
          : {}),
      })
      // Deliberately not handing straight off to the facility screen. This is the
      // one moment the operator learns whether the person they selected actually
      // became an administrator, and it is reported from `facility` -- what the
      // server persisted -- never from `selected`, which is only what was asked
      // for. The two can disagree, and only one of them is true.
      setEnrolled(facility)
    } catch (err) {
      const apiError = err as ExchangeApiError
      setError(
        apiError?.status === 403
          ? "Votre compte ne dispose pas des autorisations d'administration de la plateforme."
          : apiError?.status === 422
            ? // The server refused something specific -- an illegal role for this
              // facility type, or a person who has never signed in. Its own words
              // are more useful than a generic failure, and nothing was created.
              `${apiError.detail} Aucun établissement n'a été créé.`
            : "L'établissement n'a pas pu être enregistré. Vous pouvez réessayer.",
      )
    } finally {
      setSubmitting(false)
    }
  }

  const index = STEP_ORDER.indexOf(step)
  const roleChoices = ROLE_CHOICES_BY_TYPE[organizationType] ?? []

  if (enrolled) {
    const attached = enrolled.first_administrator
    return (
      <section className={styles.page} aria-labelledby="wizard-done-heading">
        <h1 id="wizard-done-heading" className={styles.pageTitle}>
          {enrolled.organization_name} est enregistré
        </h1>
        <dl className={styles.definitionList}>
          <div>
            <dt>Responsable</dt>
            <dd>
              {attached ? (
                <>
                  <strong className={styles.done}>
                    {attached.display_name ?? 'Personne rattachée'} est rattaché
                  </strong>
                  {' — '}
                  {attached.roles
                    .map((role) => roleLabel(enrolled.organization_type, role))
                    .join(' · ')}
                </>
              ) : (
                // Never "rattaché" without a membership behind it. If the server
                // did not create one, this says so, whatever the operator
                // selected on the way here.
                <span className={styles.owed}>Aucun responsable n'est rattaché</span>
              )}
            </dd>
          </div>
        </dl>
        <p>
          L'établissement n'est pas encore prêt à recevoir des ordres : sa disponibilité dépend de
          sa configuration et, pour un système existant, d'un connecteur DHealth qualifié. La fiche
          de l'établissement indique ce qu'il reste à faire.
        </p>
        <div className={styles.wizardActions}>
          <Button onClick={() => onEnrolled(enrolled)}>Ouvrir la fiche de l'établissement</Button>
        </div>
      </section>
    )
  }

  return (
    <section className={styles.page} aria-labelledby="wizard-heading">
      <h1 id="wizard-heading" className={styles.pageTitle}>
        Enregistrer un établissement
      </h1>

      <ol className={styles.stepper} aria-label="Étapes de l'enregistrement">
        {STEP_ORDER.map((id, i) => (
          <li
            key={id}
            className={i === index ? styles.stepCurrent : i < index ? styles.done : styles.owed}
            aria-current={i === index ? 'step' : undefined}
          >
            {STEP_LABELS[id]}
          </li>
        ))}
      </ol>

      {error ? (
        <p role="alert" className={styles.errorText}>
          {error}
        </p>
      ) : null}

      {step === 'facility' ? (
        <div className={styles.stepBody}>
          <TextInput
            label="Nom de l'établissement"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <fieldset className={styles.fieldset}>
            <legend>Type d'établissement</legend>
            {(['PROVIDER', 'LAB'] as const).map((type) => (
              <label key={type} className={styles.radioRow}>
                <input
                  type="radio"
                  name="organization-type"
                  value={type}
                  checked={organizationType === type}
                  onChange={() => setOrganizationType(type)}
                />
                {organizationTypeLabel(type)}
              </label>
            ))}
          </fieldset>
        </div>
      ) : null}

      {step === 'participation' ? (
        <div className={styles.stepBody}>
          {/* Two real choices, presented at equal weight. Neither is the
              recommended one: a clinic that keeps its own system is not a lesser
              participant, and a clinic that adopts DHealth is not taking a
              shortcut. */}
          <fieldset className={styles.fieldset}>
            <legend>Comment cet établissement participera-t-il à DHealth ?</legend>
            {(['NATIVE', 'CONNECT'] as const).map((mode) => (
              <label key={mode} className={styles.choiceCard}>
                <input
                  type="radio"
                  name="participation-mode"
                  value={mode}
                  checked={participationMode === mode}
                  onChange={() => setParticipationMode(mode)}
                />
                <span>
                  <strong>
                    {mode === 'NATIVE' ? 'Utiliser DHealth' : 'Connecter un système existant'}
                  </strong>
                  <span className={styles.choiceDescription}>
                    {participationModeDescription(mode)}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
        </div>
      ) : null}

      {step === 'responsible' ? (
        <div className={styles.stepBody}>
          <h2 className={styles.subTitle}>Ajouter un responsable</h2>
          <PersonRolePicker
            client={client}
            roleChoices={roleChoices}
            selected={selected}
            onSelect={setSelected}
            roles={roles}
            onRolesChange={setRoles}
            onError={setError}
          />
        </div>
      ) : null}

      {step === 'review' ? (
        <div className={styles.stepBody}>
          <dl className={styles.definitionList}>
            <div>
              <dt>Établissement</dt>
              <dd>{name || 'Non renseigné'}</dd>
            </div>
            <div>
              <dt>Type</dt>
              <dd>{organizationTypeLabel(organizationType)}</dd>
            </div>
            <div>
              <dt>Participation</dt>
              <dd>
                {participationMode === 'NATIVE'
                  ? 'Utiliser DHealth'
                  : participationMode === 'CONNECT'
                    ? 'Connecter un système existant'
                    : 'Non choisi'}
              </dd>
            </div>
            <div>
              <dt>Responsable</dt>
              {/* Not "à rattacher après création": there is no screen that does
                  that, and promising one would be the same defect as offering an
                  action the product does not perform. What is true is that the
                  facility will have no designated administrator, which its own
                  readiness then reports. */}
              <dd>
                {selected ? (
                  <>
                    {selected.display_name ?? selected.email ?? 'Personne sélectionnée'}
                    {' — '}
                    {roles.length > 0
                      ? roles.map((role) => roleLabel(organizationType, role)).join(' · ')
                      : 'aucun rôle sélectionné'}
                  </>
                ) : (
                  'Aucun responsable ne sera rattaché'
                )}
              </dd>
            </div>
          </dl>
          {selected && roles.length === 0 ? (
            <p className={styles.errorText}>
              Sélectionnez au moins un rôle, ou revenez en arrière : une personne sans rôle ne
              serait rattachée à rien.
            </p>
          ) : null}
          <p>
            L'établissement sera créé avec un dossier d'adhésion ouvert. Il ne sera pas
            immédiatement prêt à recevoir des ordres : sa disponibilité dépend de sa configuration
            et, pour un système existant, d'un connecteur DHealth qualifié.
          </p>
        </div>
      ) : null}

      <div className={styles.wizardActions}>
        <Button variant="secondary" onClick={onCancel}>
          Annuler
        </Button>
        {index > 0 ? (
          <Button variant="secondary" onClick={() => setStep(STEP_ORDER[index - 1])}>
            Précédent
          </Button>
        ) : null}
        {step === 'review' ? (
          <Button onClick={() => void submit()} disabled={submitting || !participationMode || !name.trim()}>
            {submitting ? 'Enregistrement…' : "Enregistrer l'établissement"}
          </Button>
        ) : (
          <Button
            onClick={() => setStep(STEP_ORDER[index + 1])}
            disabled={
              (step === 'facility' && !name.trim()) ||
              (step === 'participation' && !participationMode)
            }
          >
            Continuer
          </Button>
        )}
      </div>
    </section>
  )
}
