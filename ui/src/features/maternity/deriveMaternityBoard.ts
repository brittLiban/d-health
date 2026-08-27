import type { MaternityBoardVisit, OpenMrsPatient } from '../../api/types'
import {
  DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
  MATERNITE_LOCATION_UUID,
} from '../../api/admissionConstants'

// `admittedAt` is the single source of truth for "when was this patient
// admitted" — computed once here, not recomputed by whatever renders the
// entry (MaternityPatientCard reads it directly). For an AMBIGUOUS entry
// it is the earliest of the colliding visits' start times.
export type MaternityBoardEntry =
  | { status: 'ADMITTED'; patient: OpenMrsPatient; visit: MaternityBoardVisit; admittedAt: string }
  // UI-013 already owns admission-completeness reconciliation
  // (deriveAdmissionState) — this is a different, narrower property: the
  // same patient somehow has more than one active Daher Maternity Admission
  // visit at once. Live-confirmed the normal write path refuses this
  // (`Visit.visitCannotOverlapAnotherVisitOfTheSamePatient`), so it can only
  // arise from historical/migrated/direct-database data — the board must
  // still never render it as two separate people.
  | { status: 'AMBIGUOUS'; patient: OpenMrsPatient; visits: MaternityBoardVisit[]; admittedAt: string }

// §7 — a UX ordering decision only; it carries no clinical meaning.
// Oldest admission first: a new admission appends to the bottom of the
// list rather than jumping to the top, the lowest-surprise convention for
// a small, slowly-changing ward list. The server's own unfiltered order was
// observed newest-first during investigation, but §1 says never assume
// server-side ordering — this always re-sorts client-side regardless.
function earliestStartDatetime(visits: MaternityBoardVisit[]): string {
  return visits.reduce((earliest, visit) => (visit.startDatetime < earliest ? visit.startDatetime : earliest), visits[0].startDatetime)
}

// §2 — matches only the pinned canonical Visit type at the pinned canonical
// location, by UUID, never display text; membership is the Visit alone, no
// linked-Encounter requirement (that completeness question belongs to
// UI-013's own AdmissionPanel when a clinician opens the patient, not to a
// read-only board). Re-checks UUIDs client-side even though the server
// query already filters on them — the same defense-in-depth the per-patient
// deriveAdmissionState already applies, in case a future server
// misconfiguration ever makes the query filter looser than intended.
export function deriveMaternityBoard(visits: MaternityBoardVisit[]): MaternityBoardEntry[] {
  const matching = visits.filter(
    (visit) =>
      visit.visitType.uuid === DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID &&
      visit.location.uuid === MATERNITE_LOCATION_UUID &&
      !visit.stopDatetime,
  )

  const byPatient = new Map<string, MaternityBoardVisit[]>()
  for (const visit of matching) {
    const existing = byPatient.get(visit.patient.uuid)
    if (existing) {
      existing.push(visit)
    } else {
      byPatient.set(visit.patient.uuid, [visit])
    }
  }

  const entries: MaternityBoardEntry[] = []
  for (const patientVisits of byPatient.values()) {
    const admittedAt = earliestStartDatetime(patientVisits)
    if (patientVisits.length > 1) {
      entries.push({ status: 'AMBIGUOUS', patient: patientVisits[0].patient, visits: patientVisits, admittedAt })
    } else {
      entries.push({ status: 'ADMITTED', patient: patientVisits[0].patient, visit: patientVisits[0], admittedAt })
    }
  }

  entries.sort((a, b) => (a.admittedAt < b.admittedAt ? -1 : a.admittedAt > b.admittedAt ? 1 : 0))
  return entries
}
