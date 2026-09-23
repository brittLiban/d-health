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
 *   a runtime is reporting in                 (an observation, about right now)
 *
 * DH-CONNECT-AGENT-001 made the fourth one reachable. It did not add a way to
 * assert it: health is derived from what an agent reported, and there is no
 * [Marquer comme sain] control anywhere in this file, exactly as there is no
 * [Marquer comme qualifié]. A flag an operator can set is a flag somebody sets
 * at 2 a.m. to make an alarm stop, and the facility it unblocks then takes
 * clinical work through a connector nobody has heard from.
 *
 * What still cannot happen here: making a real facility production-routable.
 * That needs a qualified *vendor* adapter, and none exists.
 */

import { useCallback, useEffect, useState } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type { FacilityConnector } from '../../api/exchangeTypes'
import {
  capabilityEvidenceIsProven,
  capabilityEvidenceLabel,
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
        <strong>Aucun adaptateur éditeur qualifié n'existe.</strong> DHealth dispose désormais
        d'une exécution de livraison durable, mais le seul adaptateur disponible est synthétique
        : il sert à vérifier le chemin de livraison, il ne se connecte à aucun système réel.
        Aucun établissement ne peut donc être mis en service via DHealth Connect dans cette
        version.
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

      {/* Two lines, not one, because they are two different facts and this screen used to
          merge them.

          "Suite de qualification" is about the run: did every scenario the suite could
          execute behave correctly. "Qualification de production" is about the connector:
          may clinical work be routed to it. A run can legitimately pass while production
          qualification stays incomplete, because one required capability rests on the
          connector's own declaration and nothing has driven it. Showing only the first and
          calling it "Qualification réussie" is exactly how weak evidence gets read as
          strong. */}
      {latest !== null ? (
        <p>
          <strong>Suite de qualification</strong> —{' '}
          <span className={latest.result === 'PASSED' ? styles.done : styles.owed}>
            {latest.result === 'PASSED' ? 'Réussie' : 'Échouée'}
          </span>
        </p>
      ) : null}
      <p>
        <strong>Qualification de production</strong> —{' '}
        <strong className={verdict.qualified ? styles.done : styles.owed}>
          {/* Never colour alone: the word carries the state, the class only
              reinforces it. A greyscale screen must read identically. */}
          {verdict.qualified
            ? 'Qualifiée'
            : verdict.gap === 'REQUIRED_CAPABILITY_UNEXERCISED'
              ? 'Incomplète'
              : 'Non qualifiée'}
        </strong>{' '}
        — {verdict.detail}
      </p>
      {verdict.gap !== null ? (
        <p>
          <strong>Motif</strong> {qualificationGapLabel(verdict.gap)}
        </p>
      ) : null}
      {verdict.missing_capabilities.length > 0 ? (
        <>
          <h4 className={styles.subTitle}>Capacités obligatoires non satisfaites</h4>
          <ul className={styles.routeList}>
            {verdict.missing_capabilities.map((capability) => (
              <li key={capability} className={styles.owed}>
                {capabilityLabel(capability)} — non satisfaite
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {verdict.unexercised_capabilities.length > 0 ? (
        <>
          {/* Listed separately from the missing ones on purpose. These capabilities are not
              absent and the partner has nothing to fix: DHealth has no transport with which
              to drive them, which is its own gap and its own next ticket. */}
          <h4 className={styles.subTitle}>Capacité restant à démontrer</h4>
          <ul className={styles.routeList}>
            {verdict.unexercised_capabilities.map((capability) => (
              <li key={capability} className={styles.owed}>
                {capabilityLabel(capability)} — déclarée, non encore exercée
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3 className={styles.subTitle}>Exécution</h3>

      {/* The derivation's own reasons, printed verbatim. Rewriting them here would give the same
          fact two French spellings, and the one on this screen would be the one nobody updated. */}
      {connector.runtime.reasons.length > 0 ? (
        <ul className={styles.findingList}>
          {connector.runtime.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
      <dl className={styles.definitionList}>
        {/* State and service first, and as their own rows rather than one sentence. Two facts an
            operator reads separately deserve two lines -- and a value in its own element is a value
            a screen reader announces with its label rather than as part of a paragraph. */}
        <dt>État</dt>
        <dd>{healthStateLabel(connector.health_state)}</dd>
        <dt>En service</dt>
        <dd>{connector.operational ? 'Oui' : 'Non'}</dd>
        <dt>Dernier signal reçu</dt>
        {/* DHealth's receive time, labelled as such. An agent with a skewed clock must not be able
            to look recent, and an operator comparing two facilities needs one clock, not theirs. */}
        <dd>
          {connector.runtime.last_seen_at === null
            ? 'Jamais'
            : `${formatMoment(connector.runtime.last_seen_at)} (heure de réception DHealth)`}
        </dd>
        <dt>Agent</dt>
        <dd>
          {connector.runtime.agent_version === null
            ? 'Aucun agent ne s\u2019est annoncé'
            : `version ${connector.runtime.agent_version} — instance ${
                connector.runtime.runtime_instance_id ?? 'inconnue'
              }`}
        </dd>
        <dt>Système source joignable</dt>
        <dd>
          {connector.runtime.adapter_reachable === null
            ? 'Inconnu'
            : connector.runtime.adapter_reachable
              ? 'Oui'
              : 'Non'}
        </dd>
        <dt>Messages en attente de livraison</dt>
        <dd>
          {connector.runtime.pending_deliveries}
          {connector.runtime.oldest_pending_seconds !== null
            ? ` — le plus ancien depuis ${Math.floor(
                connector.runtime.oldest_pending_seconds / 60,
              )} min`
            : ''}
        </dd>
        <dt>Messages non livrables</dt>
        {/* Never "supprimés". A dead letter is an unresolved clinical message somebody has to look
            at, and the word has to say that -- DHealth does not invent a cancellation. */}
        <dd>{connector.runtime.dead_letters}</dd>
      </dl>
      <p className={styles.sectionLede}>
        Un connecteur qualifié n'est pas un connecteur en service, et un agent qui répond n'est pas
        un connecteur qualifié. La qualification prouve qu'une version du connecteur respecte le
        contrat&nbsp;; l'état ci-dessus dit seulement ce que DHealth observe aujourd'hui. Les deux
        sont nécessaires, et ni l'un ni l'autre ne suffit&nbsp;: le routage de production exige en
        plus un adaptateur éditeur qualifié, qui n'existe pas.
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
                    {/* Three outcomes, three words. A declared capability does not get the
                        success class -- it is not a success -- and it does not get the
                        failure wording either, because nothing failed. */}
                    <td
                      className={
                        capabilityEvidenceIsProven(capability.passed, capability.evidence_level)
                          ? styles.done
                          : styles.owed
                      }
                    >
                      {capabilityEvidenceLabel(capability.passed, capability.evidence_level)}
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
        {/* "Qualification de production", never "Qualification réussie". The suite may well
            have succeeded; this line is about whether the connector may take clinical work,
            and conflating the two is the misreading this correction exists to close. */}
        <li className={verdict.qualified ? styles.done : styles.owed}>
          {verdict.qualified
            ? 'Qualification de production — complète'
            : verdict.gap === 'REQUIRED_CAPABILITY_UNEXERCISED'
              ? 'Qualification de production — incomplète, capacité restant à démontrer'
              : `Qualification de production — ${qualificationGapLabel(verdict.gap).toLowerCase()}`}
        </li>
        {/* Now a real step with two outcomes rather than a permanent "not in this version".
            The state is derived; nothing on this page can advance it. */}
        <li className={connector.operational ? styles.done : styles.owed}>
          {connector.operational
            ? `Exécution du connecteur — agent actif (${healthStateLabel(connector.health_state)})`
            : `Exécution du connecteur — ${healthStateLabel(
                connector.health_state,
              ).toLowerCase()}`}
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
