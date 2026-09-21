/**
 * DH-MVP-ONBOARD-001 — every facility keeping its own software, and what each is
 * waiting for.
 *
 * A working list rather than a dashboard: the operator's question is "which
 * integrations need me this week", so the rows are the Connect facilities and the
 * column that matters is what is outstanding.
 *
 * Every row ends at the same place today, and that is the honest state of the
 * product: no DHealth connector exists for any external system yet.
 */

import type { FacilityEnrollment } from '../../api/exchangeTypes'
import { EmptyState } from '../../design-system'
import {
  feasibilityLabel,
  interfaceTypeLabel,
  onboardingStatusLabel,
  verificationStateLabel,
} from './onboardingVocabulary'
import styles from './Platform.module.css'

export interface IntegrationsOverviewProps {
  facilities: FacilityEnrollment[]
  onOpenFacility: (organizationId: string) => void
}

export function IntegrationsOverview({ facilities, onOpenFacility }: IntegrationsOverviewProps) {
  const connect = facilities.filter((f) => f.participation_mode === 'CONNECT')

  return (
    <section className={styles.page} aria-labelledby="integrations-heading">
      <h1 id="integrations-heading" className={styles.pageTitle}>
        Intégrations
      </h1>
      <p>
        Établissements conservant leur logiciel existant. Aucun connecteur DHealth n'est encore
        construit : ces intégrations sont en préparation, pas en production.
      </p>

      {connect.length === 0 ? (
        <EmptyState title="Aucun établissement en mode Connect">
          Les établissements utilisant DHealth directement n'ont pas besoin d'intégration.
        </EmptyState>
      ) : (
        <div className={styles.tableScroll}>
        <table className={styles.interfaceTable}>
          <caption className="visually-hidden">
            Établissements en mode Connect et état de leur découverte technique
          </caption>
          <thead>
            <tr>
              <th scope="col">Établissement</th>
              <th scope="col">Logiciel</th>
              <th scope="col">Interfaces</th>
              <th scope="col">État</th>
            </tr>
          </thead>
          <tbody>
            {connect.map((facility) => (
              <tr key={facility.organization_id}>
                <th scope="row">
                  <button
                    type="button"
                    className={styles.linkButton}
                    onClick={() => onOpenFacility(facility.organization_id)}
                  >
                    {facility.organization_name}
                  </button>
                </th>
                <td>{facility.existing_system_name ?? 'Non renseigné'}</td>
                <td>
                  {facility.interfaces.length === 0
                    ? 'Aucune signalée'
                    : facility.interfaces
                        .map(
                          (i) =>
                            `${interfaceTypeLabel(i.interface_type)} — ${verificationStateLabel(i.verification_state)}`,
                        )
                        .join(' · ')}
                </td>
                <td>
                  {onboardingStatusLabel(facility.onboarding_status)}
                  {facility.integration_feasibility ? (
                    <span className={styles.facilityMeta}>
                      {' '}
                      · {feasibilityLabel(facility.integration_feasibility)}
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </section>
  )
}
