import type { ActiveVisit } from '../../api/types'
import {
  ADMISSION_ENCOUNTER_TYPE_UUID,
  DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
  MATERNITE_LOCATION_UUID,
} from '../../api/admissionConstants'

export type AdmissionState =
  | { status: 'NOT_ADMITTED' }
  | { status: 'PARTIAL_VISIT'; visit: ActiveVisit }
  | { status: 'COMPLETE'; visit: ActiveVisit }
  | { status: 'AMBIGUOUS'; reason: 'MULTIPLE_VISITS' | 'MULTIPLE_ENCOUNTERS' }

// UI-013 §10 — a pure function over already-fetched server state, so the
// state matrix (§29) is testable without mocking fetch at all. Matches only
// the approved Visit type at the approved location (§10): an unrelated
// active OPD/Home/etc. visit for the same patient is never treated as a
// maternity admission, no matter how many of those exist.
export function deriveAdmissionState(visits: ActiveVisit[]): AdmissionState {
  const matchingVisits = visits.filter(
    (visit) =>
      visit.visitType.uuid === DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID &&
      visit.location.uuid === MATERNITE_LOCATION_UUID,
  )

  if (matchingVisits.length > 1) {
    return { status: 'AMBIGUOUS', reason: 'MULTIPLE_VISITS' }
  }
  if (matchingVisits.length === 0) {
    return { status: 'NOT_ADMITTED' }
  }

  const visit = matchingVisits[0]
  const linkedEncounters = visit.encounters.filter(
    (encounter) => encounter.encounterType.uuid === ADMISSION_ENCOUNTER_TYPE_UUID && !encounter.voided,
  )

  if (linkedEncounters.length > 1) {
    return { status: 'AMBIGUOUS', reason: 'MULTIPLE_ENCOUNTERS' }
  }
  if (linkedEncounters.length === 0) {
    return { status: 'PARTIAL_VISIT', visit }
  }
  return { status: 'COMPLETE', visit }
}
