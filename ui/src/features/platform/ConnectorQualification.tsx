/**
 * DH-CONNECT-CONTRACT-001 §49/§76 — the connector installed at a facility, and
 * whether it has qualified.
 *
 * The temptation this screen exists to resist is a single green badge reading
 * "Connecté". Four different facts feed it, and an operator deciding whether a
 * laboratory can receive real orders needs all four separated:
 *
 *   a connector build is registered here      (bookkeeping)
 *   a qualification run passed                (evidence, about a past moment)
 *   that run still applies to what is installed (a present-tense question)
 *   a runtime is reporting in                 (false everywhere, in this release)
 *
 * The last one is why nothing on this screen can make a facility routable, and
 * why there is no [Marquer comme qualifié] button anywhere in this file. A
 * qualification is what running the suite produced; if an operator could assert
 * it, every downstream guarantee would quietly become a claim.
 */

import { useCallback, useEffect, useState } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type { FacilityConnector } from '../../api/exchangeTypes'
import {
  capabilityLabel,
  connectorStatusLabel,
  healthStateLabel,
  interfaceTypeLabel,
  qualificationGapLabel,
} from './onboardingVocabulary'
import type { InterfaceType } from '../../api/exchangeTypes'
import styles from './Platform.module.css'

export interface ConnectorQualificationProps {
  organizationId: string
  client: ExchangeClient
  /**
   * DHealth's own conclusion about this partner's software, from the enrollment.
   *
   * Needed only so the empty state does not contradict the banner above it. A facility marked
   * `NOT_SAFELY_INTEGRABLE` is being told, in red, that its current software exposes no interface
   * DHealth can safely exchange over — and "un connecteur doit être construit pour ce système"
   * printed underneath that would be this screen arguing with itself.
   */
  integrationFeasibility?: string | null
  /** Refresh the readiness panel above, which derives from the same rows. */
  onChanged: () => void
}

/** A fingerprint, shortened for the eye but never relabelled as something else. */
function shortFingerprint(value: string): string {
  return `${value.slice(0, 12)}…`
}

function formatMoment(value: string | null): string {
  if (value === null) return 'Non renseigné'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('fr-FR', { dateStyle: 'long', timeStyle: 'short' })
}

export function ConnectorQualification({
  organizationId,
  client,
  integrationFeasibility = null,
  onChanged,
}: ConnectorQualificationProps) {
  const [connector, setConnector] = useState<FacilityConnector | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [confirmingRetire, setConfirmingRetire] = useState(false)

  const load = useCallback(async () => {
    try {
      setConnector(await client.getFacilityConnector(organizationId))
      setError(null)
    } catch (err) {
      const apiError = err as ExchangeApiError
      setError(
        apiError?.status === 403
          ? 'Seule la plateforme DHealth peut consulter les connecteurs.'
          : "L'état du connecteur n'a pas pu être chargé.",
      )
    }
  }, [client, organizationId])

  useEffect(() => {
    void load()
  }, [load])

  const changeStatus = async (
    installationId: string,
    status: 'REGISTERED' | 'SUSPENDED' | 'RETIRED',
  ) => {
    setPending(true)
    setError(null)
    try {
      await client.setFacilityConnectorStatus(organizationId, installationId, status)
      setConfirmingRetire(false)
      await load()
      onChanged()
    } catch (err) {
      const apiError = err as ExchangeApiError
      setError(
        apiError?.status === 409
          ? "Un connecteur retiré ne peut plus être modifié. Enregistrez un nouveau connecteur."
          : "Le changement d'état n'a pas pu être enregistré. Vous pouvez réessayer.",
      )
    } finally {
      setPending(false)
    }
  }

  if (error !== null && connector === null) {
    return (
      <section aria-labelledby="connector-heading">
        <h2 id="connector-heading" className={styles.sectionTitle}>
          Connecteur DHealth
        </h2>
        <p role="alert" className={styles.errorText}>
          {error}
        </p>
      </section>
    )
  }

  if (connector === null) {
    return (
      <section aria-labelledby="connector-heading">
        <h2 id="connector-heading" className={styles.sectionTitle}>
          Connecteur DHealth
        </h2>
        <p>Chargement de l'état du connecteur…</p>
      </section>
    )
  }

  const { installation, verdict, qualification_history: history } = connector
  const latest = history.length > 0 ? history[0] : null

  return (
    <section aria-labelledby="connector-heading">
      <h2 id="connector-heading" className={styles.sectionTitle}>
        Connecteur DHealth
      </h2>

      {error ? (
        <p role="alert" className={styles.errorText}>
          {error}
        </p>
      ) : null}

      {/* Stated before anything else, because it is true regardless of everything
          below it and an operator who reads only one sentence should read this one. */}
      <p className={styles.nextStep}>
        <strong>Le transport DHealth Connect n'est pas construit.</strong> Aucun connecteur
        n'échange de données avec un système externe dans cette version. La qualification
        ci-dessous vérifie qu'un connecteur respecterait le contrat&nbsp;; elle ne met rien en
        service.
      </p>

      {installation === null ? (
        // The verdict below already states the absence in the server's own
        // words. Repeating it here would be two sentences saying one thing, so
        // this one says what follows from it instead -- and what follows is not
        // the same thing for a facility DHealth has concluded it cannot safely
        // exchange with at all.
        <p className={styles.owed}>
          {integrationFeasibility === 'NOT_SAFELY_INTEGRABLE'
            ? "Rien n'est installé ici, et rien ne peut l'être tant que le logiciel actuel " +
              "n'expose aucune interface permettant un échange sûr."
            : "Rien n'est installé ici. Un connecteur doit être construit pour ce système, puis " +
              'enregistré, puis qualifié.'}
        </p>
      ) : (
        <>
          <dl className={styles.definitionList}>
            <div>
              <dt>Connecteur</dt>
              <dd>
                {installation.connector_key} {installation.connector_version}
              </dd>
            </div>
            <div>
              <dt>Système source</dt>
              <dd>
                {installation.source_system}
                {installation.source_system_version
                  ? ` ${installation.source_system_version}`
                  : ''}
              </dd>
            </div>
            <div>
              <dt>Interface</dt>
              <dd>{interfaceTypeLabel(installation.interface_type as InterfaceType)}</dd>
            </div>
            <div>
              <dt>Contrat d'échange</dt>
              <dd>
                {installation.contract_version}
                {installation.contract_version !== verdict.current_contract_version
                  ? ` (contrat courant : ${verdict.current_contract_version})`
                  : ''}
              </dd>
            </div>
            <div>
              <dt>État de l'installation</dt>
              <dd>{connectorStatusLabel(installation.status)}</dd>
            </div>
            <div>
              {/* A digest, labelled as a digest. DHealth never receives the
                  configuration itself, which is exactly why a change is
                  detectable without DHealth holding a partner's credentials. */}
              <dt>Empreinte de configuration</dt>
              <dd>
                <code>{shortFingerprint(installation.configuration_fingerprint)}</code>
              </dd>
            </div>
            <div>
              <dt>Empreinte de correspondance des analyses</dt>
              <dd>
                <code>{shortFingerprint(installation.mapping_fingerprint)}</code>
              </dd>
            </div>
          </dl>

          {confirmingRetire ? (
            // Two gestures for retirement, matching route activation.
            //
            // Suspension is reversible and gets one click. Retirement is not: it is the only
            // irreversible action on this screen, and it is one word away from "Réactiver" in the
            // same row. The restatement names what becomes possible and what stays, so the second
            // gesture is made by somebody who has read it rather than by somebody who aimed badly.
            <div className={styles.confirmPanel}>
              <p id="connector-retire-restatement">
                Retirer <strong>{installation.connector_key}</strong> est définitif. Ce connecteur
                ne pourra plus être réactivé ni requalifié, et l'établissement pourra en enregistrer
                un autre. Les qualifications déjà enregistrées sont conservées.
              </p>
              <div className={styles.confirmActions}>
                <button type="button" onClick={() => setConfirmingRetire(false)}>
                  Annuler
                </button>
                <button
                  type="button"
                  aria-describedby="connector-retire-restatement"
                  disabled={pending}
                  onClick={() => void changeStatus(installation.installation_id, 'RETIRED')}
                >
                  Retirer définitivement
                </button>
              </div>
            </div>
          ) : (
            <div className={styles.routeActions}>
              {installation.status === 'REGISTERED' ? (
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => void changeStatus(installation.installation_id, 'SUSPENDED')}
                >
                  Suspendre le connecteur
                </button>
              ) : null}
              {installation.status === 'SUSPENDED' ? (
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => void changeStatus(installation.installation_id, 'REGISTERED')}
                >
                  Réactiver le connecteur
                </button>
              ) : null}
              <button type="button" disabled={pending} onClick={() => setConfirmingRetire(true)}>
                Retirer définitivement…
              </button>
            </div>
          )}
          <p className={styles.sectionLede}>
            Suspendre un connecteur est réversible et conserve sa qualification. Retirer est
            définitif et libère l'établissement pour en enregistrer un autre.
          </p>
        </>
      )}

      <h3 className={styles.subTitle}>Qualification</h3>
      <p>
        <strong className={verdict.qualified ? styles.done : styles.owed}>
          {/* Never colour alone: the word carries the state, the class only
              reinforces it. A greyscale screen must read identically. */}
          {verdict.qualified ? 'Qualifié' : 'Non qualifié'}
        </strong>{' '}
        — {verdict.detail}
      </p>
      {verdict.gap !== null ? (
        <p>
          <strong>Motif</strong> {qualificationGapLabel(verdict.gap)}
        </p>
      ) : null}
      {verdict.missing_capabilities.length > 0 ? (
        <ul className={styles.routeList}>
          {verdict.missing_capabilities.map((capability) => (
            <li key={capability} className={styles.owed}>
              {capabilityLabel(capability)} — non satisfaite
            </li>
          ))}
        </ul>
      ) : null}

      <p>
        <strong>En service</strong> — Non.
      </p>
      <p>
        <strong>État remonté par l'exécution</strong> — {healthStateLabel(connector.health_state)}.
      </p>
      <p className={styles.sectionLede}>
        Un connecteur qualifié n'est pas un connecteur en service. La qualification prouve qu'une
        version du connecteur respecte le contrat&nbsp;; seule une exécution qui remonte son état
        prouve qu'elle fonctionne aujourd'hui. Aucune exécution de ce type n'existe dans cette
        version, donc le routage de production reste refusé.
      </p>

      {latest !== null ? (
        <>
          <h3 className={styles.subTitle}>Dernière exécution</h3>
          <p>
            {latest.result === 'PASSED' ? 'Réussie' : 'Échouée'} le{' '}
            {formatMoment(latest.completed_at)} — suite {latest.suite_version}, connecteur{' '}
            {latest.connector_version}, contrat {latest.contract_version}.
          </p>
          {latest.evidence_summary ? <p>{latest.evidence_summary}</p> : null}
          <div className={styles.tableScroll}>
            <table className={styles.interfaceTable}>
              <caption className="visually-hidden">
                Capacités vérifiées lors de la dernière qualification
              </caption>
              <thead>
                <tr>
                  <th scope="col">Capacité</th>
                  <th scope="col">Résultat</th>
                  <th scope="col">Détail</th>
                </tr>
              </thead>
              <tbody>
                {latest.capabilities.map((capability) => (
                  <tr key={capability.capability}>
                    <th scope="row">{capabilityLabel(capability.capability)}</th>
                    <td className={capability.passed ? styles.done : styles.owed}>
                      {capability.passed ? 'Satisfaite' : 'Non satisfaite'}
                    </td>
                    <td>{capability.detail ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {/* The tail of the progression that used to be hardcoded in the discovery
          section. Real state now, except the last line, which is not state at
          all: production routing is refused for every CONNECT facility in this
          release and no combination of the lines above it changes that. */}
      {/* Not "Progression vers la production" a second time: the discovery
          section above already owns that heading, and two lists with the same
          title both restarting at 1 read as one list that lost its place. */}
      <h3 className={styles.subTitle}>Progression du connecteur</h3>
      <ol className={styles.progressList}>
        <li className={installation === null ? styles.owed : styles.done}>
          {installation === null
            ? 'Connecteur DHealth — non enregistré'
            : 'Connecteur DHealth enregistré'}
        </li>
        <li className={verdict.qualified ? styles.done : styles.owed}>
          {verdict.qualified
            ? 'Qualification réussie'
            : `Qualification — ${qualificationGapLabel(verdict.gap).toLowerCase()}`}
        </li>
        <li className={styles.owed}>
          Exécution du connecteur — aucun runtime dans cette version
        </li>
        <li className={styles.stopped}>Routage de production — désactivé</li>
      </ol>

      {history.length > 1 ? (
        <>
          <h3 className={styles.subTitle}>Historique des qualifications</h3>
          {/* Failures stay listed. "A-t-il déjà échoué, et sur quoi" is a question
              an operator has to be able to answer before trusting a later pass. */}
          <ul className={styles.routeList}>
            {history.map((record) => (
              <li key={record.qualification_id}>
                {formatMoment(record.completed_at)} —{' '}
                {record.result === 'PASSED' ? 'réussie' : 'échouée'} (connecteur{' '}
                {record.connector_version}, configuration{' '}
                <code>{shortFingerprint(record.configuration_fingerprint)}</code>)
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  )
}
