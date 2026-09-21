/**
 * DH-MVP-ONBOARD-001 — every facility on the network, at a glance.
 *
 * The question this answers is "who is on the network and what is each one
 * waiting for", so each row carries the four facts that decide what an operator
 * does next: who, what kind, how they participate, and where they are stuck.
 *
 * No raw enum reaches this screen. `AWAITING_CONNECTOR` is a database value;
 * "En attente du connecteur" is what an operator reads.
 */

import type { FacilityEnrollment } from '../../api/exchangeTypes'
import { Button, EmptyState } from '../../design-system'
import {
  blockerLabel,
  onboardingStatusLabel,
  organizationTypeLabel,
  participationModeLabel,
} from './onboardingVocabulary'
import styles from './Platform.module.css'

export interface FacilityListProps {
  facilities: FacilityEnrollment[]
  onOpenFacility: (organizationId: string) => void
  onCreate: () => void
}

/**
 * The state word carries a tone, and the tone describes where a *process* is —
 * never a judgement about the facility. `brass` means something is owed, `clay`
 * means this stopped, `petrol` means a step is complete. A facility waiting on a
 * connector is not failing; it is waiting, and DHealth is the one holding it up.
 */
function statusTone(facility: FacilityEnrollment): 'done' | 'owed' | 'stopped' {
  if (facility.onboarding_status === 'BLOCKED') return 'stopped'
  if (facility.integration_feasibility === 'NOT_SAFELY_INTEGRABLE') return 'stopped'
  if (facility.onboarding_status === 'READY_FOR_ROUTING') return 'done'
  return 'owed'
}

export function FacilityList({ facilities, onOpenFacility, onCreate }: FacilityListProps) {
  return (
    <section className={styles.page} aria-labelledby="facility-list-heading">
      <header className={styles.pageHeader}>
        <h1 id="facility-list-heading" className={styles.pageTitle}>
          Établissements
        </h1>
        <Button onClick={onCreate}>Enregistrer un établissement</Button>
      </header>

      {facilities.length === 0 ? (
        <EmptyState title="Aucun établissement n'est encore enregistré">
          Enregistrez le premier établissement pour commencer à construire le réseau DHealth.
        </EmptyState>
      ) : (
        <ul className={styles.facilityList}>
          {facilities.map((facility) => {
            const blocker = facility.open_blockers[0]
            return (
              <li key={facility.organization_id}>
                <button
                  type="button"
                  className={styles.facilityRow}
                  onClick={() => onOpenFacility(facility.organization_id)}
                >
                  <span className={styles.facilityName}>{facility.organization_name}</span>
                  <span className={styles.facilityMeta}>
                    {organizationTypeLabel(facility.organization_type)} ·{' '}
                    {participationModeLabel(facility.participation_mode)}
                  </span>
                  {/* The state is a word first. The tint is secondary, so the row
                      still reads correctly in monochrome or to someone who cannot
                      distinguish the hues. */}
                  <span
                    className={[styles.facilityStatus, styles[statusTone(facility)]].join(' ')}
                  >
                    {onboardingStatusLabel(facility.onboarding_status)}
                  </span>
                  {blocker ? (
                    <span className={styles.facilityBlocker}>{blockerLabel(blocker.code)}</span>
                  ) : null}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
