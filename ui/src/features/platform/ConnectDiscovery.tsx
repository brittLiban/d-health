/**
 * DH-MVP-ONBOARD-001 — what DHealth knows about a partner's existing software.
 *
 * The one thing this screen must never do is let three different facts look like
 * one. A partner saying "we have FHIR" is a claim. DHealth having checked that
 * the endpoint exists is a second, stronger fact. A DHealth connector existing
 * and having passed qualification is a third that does not exist for anyone yet.
 *
 * So the progress list below always ends at "Connecteur DHealth — à construire",
 * and no interface state can move it. That is not a placeholder to be filled in
 * later by this UI; it is the truthful stopping point until DH-CONNECT-CONTRACT-001
 * builds something real.
 */

import { useState } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type {
  FacilityEnrollment,
  InterfaceType,
  InterfaceVerificationState,
} from '../../api/exchangeTypes'
import {
  hostingModelLabel,
  interfaceTypeLabel,
  verificationStateLabel,
} from './onboardingVocabulary'
import styles from './Platform.module.css'

export interface ConnectDiscoveryProps {
  facility: FacilityEnrollment
  client: ExchangeClient
  onChanged: () => void
}

const INTERFACE_CHOICES: InterfaceType[] = [
  'REST_API',
  'FHIR',
  'HL7_V2',
  'WEBHOOK',
  'SFTP',
  'FILE_IMPORT_EXPORT',
  'DATABASE_READ',
  'OTHER',
  'UNKNOWN',
]

const VERIFICATION_CHOICES: InterfaceVerificationState[] = [
  'REPORTED',
  'DOCUMENTED',
  'VERIFIED',
  'UNSUPPORTED',
]

/** A discovery field, or an explicit statement that nobody knows yet. */
function field(value: string | null): string {
  return value?.trim() ? value : 'Non renseigné'
}

export function ConnectDiscovery({ facility, client, onChanged }: ConnectDiscoveryProps) {
  const [pending, setPending] = useState<InterfaceType | null>(null)
  const [error, setError] = useState<string | null>(null)

  const recordInterface = async (
    interfaceType: InterfaceType,
    state: InterfaceVerificationState,
  ) => {
    setPending(interfaceType)
    setError(null)
    try {
      await client.setInterfaceCapability(facility.organization_id, interfaceType, {
        verification_state: state,
      })
      onChanged()
    } catch (err) {
      const apiError = err as ExchangeApiError
      setError(
        apiError?.status === 403
          ? "Seule la plateforme DHealth peut enregistrer une vérification d'interface."
          : "L'interface n'a pas pu être enregistrée. Vous pouvez réessayer.",
      )
    } finally {
      setPending(null)
    }
  }

  return (
    <section aria-labelledby="connect-discovery-heading">
      <h2 id="connect-discovery-heading" className={styles.sectionTitle}>
        Système existant
      </h2>

      <dl className={styles.definitionList}>
        <div>
          <dt>Logiciel</dt>
          <dd>{field(facility.existing_system_name)}</dd>
        </div>
        <div>
          <dt>Éditeur</dt>
          <dd>{field(facility.existing_system_vendor)}</dd>
        </div>
        <div>
          <dt>Version</dt>
          <dd>{field(facility.existing_system_version)}</dd>
        </div>
        <div>
          <dt>Hébergement</dt>
          <dd>{hostingModelLabel(facility.hosting_model)}</dd>
        </div>
        <div>
          <dt>Contact technique</dt>
          <dd>{field(facility.technical_contact)}</dd>
        </div>
        <div>
          <dt>Contact éditeur</dt>
          <dd>{field(facility.vendor_contact)}</dd>
        </div>
      </dl>

      <h3 className={styles.subTitle}>Interfaces</h3>
      {error ? (
        <p role="alert" className={styles.errorText}>
          {error}
        </p>
      ) : null}
      <div className={styles.tableScroll}>
      <table className={styles.interfaceTable}>
        <caption className="visually-hidden">
          Interfaces signalées par le partenaire et niveau de vérification par DHealth
        </caption>
        <thead>
          <tr>
            <th scope="col">Interface</th>
            <th scope="col">Niveau de connaissance</th>
            <th scope="col">Enregistrer</th>
          </tr>
        </thead>
        <tbody>
          {INTERFACE_CHOICES.map((interfaceType) => {
            const recorded = facility.interfaces.find((i) => i.interface_type === interfaceType)
            return (
              <tr key={interfaceType}>
                <th scope="row">{interfaceTypeLabel(interfaceType)}</th>
                <td>
                  {/* Never "connecté". The strongest thing this column can say is
                      that DHealth checked the interface exists as documented. */}
                  {recorded ? verificationStateLabel(recorded.verification_state) : 'Non évaluée'}
                </td>
                <td>
                  <label className="visually-hidden" htmlFor={`iface-${interfaceType}`}>
                    Niveau de connaissance pour {interfaceTypeLabel(interfaceType)}
                  </label>
                  <select
                    id={`iface-${interfaceType}`}
                    className={styles.select}
                    value={recorded?.verification_state ?? ''}
                    disabled={pending === interfaceType}
                    onChange={(event) => {
                      if (!event.target.value) return
                      void recordInterface(
                        interfaceType,
                        event.target.value as InterfaceVerificationState,
                      )
                    }}
                  >
                    <option value="">Non évaluée</option>
                    {VERIFICATION_CHOICES.map((state) => (
                      <option key={state} value={state}>
                        {verificationStateLabel(state)}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      </div>

      <h3 className={styles.subTitle}>Progression vers la production</h3>
      <ol className={styles.progressList}>
        <li className={facility.existing_system_name ? styles.done : styles.owed}>
          Système identifié
        </li>
        <li className={facility.interfaces.length > 0 ? styles.done : styles.owed}>
          Interface signalée
        </li>
        <li
          className={
            facility.interfaces.some((i) =>
              ['DOCUMENTED', 'VERIFIED'].includes(i.verification_state),
            )
              ? styles.done
              : styles.owed
          }
        >
          Documentation obtenue
        </li>
        <li
          className={
            facility.interfaces.some((i) => i.verification_state === 'VERIFIED')
              ? styles.done
              : styles.owed
          }
        >
          Interface vérifiée
        </li>
      </ol>
      {/* The connector steps used to be hardcoded here as three permanently
          unreachable lines, which was truthful while no connector could exist.
          DH-CONNECT-CONTRACT-001 made them real state, so they moved to the
          section that reads that state -- a fixed "Connecteur — à construire"
          sitting above a registered, qualified connector would be a screen
          contradicting itself. What has not changed is that nothing on *this*
          screen can advance them: no interface verification makes a connector
          exist, and none of them unlocks routing. */}
      <p className={styles.nextStep}>
        <strong>Prochaine étape</strong> — construire et qualifier le connecteur DHealth pour ce
        système. Aucune interface vérifiée ne permet à elle seule d'activer le routage. L'état du
        connecteur est présenté ci-dessous.
      </p>
    </section>
  )
}
