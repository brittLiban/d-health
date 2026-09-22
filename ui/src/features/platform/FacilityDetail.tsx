/**
 * DH-MVP-ONBOARD-001 — one facility, and what is true about it.
 *
 * The overview has to answer five questions without being opened further: who is
 * this, how are they participating, are they ready, what is blocking them, and
 * what happens next. Everything else is a section the operator opens when they
 * need it.
 *
 * Readiness comes from the server on every view. This component formats findings
 * and never computes one — a React copy of the rule would drift from the backend
 * and the drift would only surface when somebody activated a route that should
 * have been refused.
 */

import { useCallback, useEffect, useState } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type { FacilityEnrollment, FacilityReadiness } from '../../api/exchangeTypes'
import { Button, LoadingSkeleton } from '../../design-system'
import { ConnectDiscovery } from './ConnectDiscovery'
import { RouteNetwork } from './RouteNetwork'
import {
  blockerLabel,
  feasibilityLabel,
  onboardingStatusLabel,
  organizationTypeLabel,
  participationModeLabel,
  readinessFindingLabel,
} from './onboardingVocabulary'
import styles from './Platform.module.css'

export interface FacilityDetailProps {
  facility: FacilityEnrollment
  /** Every facility on the network -- the routing section needs the laboratories. */
  facilities: FacilityEnrollment[]
  client: ExchangeClient
  onBack: () => void
  onChanged: () => void
}

export function FacilityDetail({
  facility,
  facilities,
  client,
  onBack,
  onChanged,
}: FacilityDetailProps) {
  const [readiness, setReadiness] = useState<FacilityReadiness | null>(null)
  const [readinessError, setReadinessError] = useState<ExchangeApiError | null>(null)

  const loadReadiness = useCallback(async () => {
    setReadinessError(null)
    try {
      setReadiness(await client.getFacilityReadiness(facility.organization_id))
    } catch (error) {
      setReadinessError(error as ExchangeApiError)
    }
  }, [client, facility.organization_id])

  useEffect(() => {
    void loadReadiness()
  }, [loadReadiness])

  const isConnect = facility.participation_mode === 'CONNECT'
  const notIntegrable = facility.integration_feasibility === 'NOT_SAFELY_INTEGRABLE'

  const blocking = (readiness?.findings ?? []).filter((f) => f.blocking && !f.satisfied)
  const outstanding = (readiness?.findings ?? []).filter((f) => !f.blocking && !f.satisfied)

  return (
    <section className={styles.page} aria-labelledby="facility-detail-heading">
      {/* Constrained rather than stretched: the design system's button fills its
          container, and a full-width "back" reads as a primary action. */}
      <div>
        <Button variant="secondary" onClick={onBack}>
          ← Retour aux établissements
        </Button>
      </div>

      <header className={styles.pageHeader}>
        <div>
          <h1 id="facility-detail-heading" className={styles.pageTitle}>
            {facility.organization_name}
          </h1>
          <p className={styles.facilityMeta}>
            {organizationTypeLabel(facility.organization_type)} ·{' '}
            {participationModeLabel(facility.participation_mode)} ·{' '}
            {onboardingStatusLabel(facility.onboarding_status)}
          </p>
        </div>
      </header>

      {notIntegrable ? (
        // §64: a truthful dead end, and the facility stays. Deleting the record
        // because integration is currently impossible would lose the reason and
        // the history of having tried.
        <div className={styles.stoppedPanel} role="note">
          <h2 className={styles.sectionTitle}>Intégration actuellement bloquée</h2>
          <p>
            Le logiciel actuel ne présente aucune interface prise en charge permettant un échange
            sûr avec DHealth.
          </p>
          <p>L'établissement reste enregistré dans DHealth.</p>
          <p className={styles.nextStep}>
            <strong>Prochaine étape</strong> — contacter l'éditeur ou réévaluer le mode de
            participation.
          </p>
        </div>
      ) : null}

      <section aria-labelledby="facility-readiness-heading">
        <h2 id="facility-readiness-heading" className={styles.sectionTitle}>
          Disponibilité opérationnelle
        </h2>

        {readinessError ? (
          <p role="alert">
            La disponibilité n'a pas pu être évaluée.{' '}
            <Button variant="secondary" onClick={() => void loadReadiness()}>
              Réessayer
            </Button>
          </p>
        ) : readiness === null ? (
          <LoadingSkeleton />
        ) : (
          <>
            <p className={styles.routingVerdict}>
              <span className={styles.routingLabel}>Routage de production</span>{' '}
              <strong className={readiness.production_routing_allowed ? styles.done : styles.owed}>
                {readiness.production_routing_allowed ? 'Autorisé' : 'Indisponible'}
              </strong>
            </p>

            {blocking.length > 0 ? (
              <>
                <h3 className={styles.subTitle}>Ce qui empêche la mise en service</h3>
                <ul className={styles.findingList}>
                  {blocking.map((finding) => (
                    <li key={finding.code} className={styles.findingBlocking}>
                      {readinessFindingLabel(finding.code, finding.satisfied, true)}
                      {finding.detail ? (
                        <span className={styles.findingDetail}> — {finding.detail}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p>Toutes les conditions de routage sont réunies.</p>
            )}

            {outstanding.length > 0 ? (
              <>
                <h3 className={styles.subTitle}>Configuration à compléter</h3>
                <ul className={styles.findingList}>
                  {outstanding.map((finding) => (
                    <li key={finding.code}>
                      {readinessFindingLabel(finding.code, finding.satisfied)}
                      {finding.detail ? (
                        <span className={styles.findingDetail}> — {finding.detail}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </>
        )}
      </section>

      {facility.open_blockers.length > 0 ? (
        <section aria-labelledby="facility-blockers-heading">
          <h2 id="facility-blockers-heading" className={styles.sectionTitle}>
            Blocages enregistrés
          </h2>
          <ul className={styles.findingList}>
            {facility.open_blockers.map((blocker) => (
              <li key={blocker.id} className={styles.findingBlocking}>
                {blockerLabel(blocker.code)}
                {blocker.note ? <span className={styles.findingDetail}> — {blocker.note}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {isConnect ? (
        <ConnectDiscovery
          facility={facility}
          client={client}
          onChanged={() => {
            onChanged()
            void loadReadiness()
          }}
        />
      ) : (
        <section aria-labelledby="facility-native-heading">
          <h2 id="facility-native-heading" className={styles.sectionTitle}>
            Participation
          </h2>
          <p>
            Cet établissement utilise DHealth directement pour son flux diagnostique. Sa
            disponibilité dépend de sa configuration et de son personnel, évaluées ci-dessus.
          </p>
        </section>
      )}

      {facility.organization_type === 'PROVIDER' ? (
        <RouteNetwork
          provider={facility}
          facilities={facilities}
          providerReadiness={readiness}
          client={client}
          onChanged={() => {
            onChanged()
            void loadReadiness()
          }}
        />
      ) : null}

      {facility.integration_feasibility && !notIntegrable ? (
        <p className={styles.facilityMeta}>
          Faisabilité d'intégration : {feasibilityLabel(facility.integration_feasibility)}
        </p>
      ) : null}
    </section>
  )
}
