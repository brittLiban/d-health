import { describe, expect, it } from 'vitest'
import { deriveAdmissionState } from './deriveAdmissionState'
import {
  ADMISSION_ENCOUNTER_TYPE_UUID,
  DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
  MATERNITE_LOCATION_UUID,
} from '../../api/admissionConstants'
import type { ActiveVisit } from '../../api/types'

const OTHER_VISIT_TYPE_UUID = 'facility-visit-uuid'
const OTHER_LOCATION_UUID = 'inpatient-ward-uuid'

function maternityVisit(overrides: Partial<ActiveVisit> = {}): ActiveVisit {
  return {
    uuid: 'visit-1',
    visitType: { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, display: 'Daher Maternity Admission' },
    location: { uuid: MATERNITE_LOCATION_UUID, display: 'Maternité' },
    stopDatetime: null,
    encounters: [],
    ...overrides,
  }
}

function admissionEncounter(overrides: { uuid?: string; voided?: boolean } = {}) {
  return {
    uuid: overrides.uuid ?? 'encounter-1',
    encounterType: { uuid: ADMISSION_ENCOUNTER_TYPE_UUID, display: 'Admission' },
    voided: overrides.voided ?? false,
  }
}

describe('deriveAdmissionState', () => {
  it('NOT_ADMITTED — no visits at all', () => {
    expect(deriveAdmissionState([])).toEqual({ status: 'NOT_ADMITTED' })
  })

  it('NOT_ADMITTED — an unrelated active visit (wrong type and location) is never treated as admission', () => {
    const unrelated = maternityVisit({
      uuid: 'opd-visit',
      visitType: { uuid: OTHER_VISIT_TYPE_UUID, display: 'OPD Visit' },
      location: { uuid: OTHER_LOCATION_UUID, display: 'Inpatient Ward' },
    })
    expect(deriveAdmissionState([unrelated])).toEqual({ status: 'NOT_ADMITTED' })
  })

  it('NOT_ADMITTED — right Visit type at the wrong location does not count', () => {
    const wrongLocation = maternityVisit({ location: { uuid: OTHER_LOCATION_UUID, display: 'Inpatient Ward' } })
    expect(deriveAdmissionState([wrongLocation])).toEqual({ status: 'NOT_ADMITTED' })
  })

  it('NOT_ADMITTED — right location but the wrong Visit type does not count', () => {
    const wrongType = maternityVisit({ visitType: { uuid: OTHER_VISIT_TYPE_UUID, display: 'Facility Visit' } })
    expect(deriveAdmissionState([wrongType])).toEqual({ status: 'NOT_ADMITTED' })
  })

  it('PARTIAL_VISIT — exactly one matching Visit, zero linked Admission Encounters', () => {
    const visit = maternityVisit()
    expect(deriveAdmissionState([visit])).toEqual({ status: 'PARTIAL_VISIT', visit })
  })

  it('PARTIAL_VISIT — the matching Visit has other, unrelated encounter types but no Admission one', () => {
    const visit = maternityVisit({
      encounters: [{ uuid: 'e1', encounterType: { uuid: 'vitals-uuid', display: 'Vitals' }, voided: false }],
    })
    expect(deriveAdmissionState([visit])).toEqual({ status: 'PARTIAL_VISIT', visit })
  })

  it('PARTIAL_VISIT — a voided Admission Encounter does not count as linked', () => {
    const visit = maternityVisit({ encounters: [admissionEncounter({ voided: true })] })
    expect(deriveAdmissionState([visit])).toEqual({ status: 'PARTIAL_VISIT', visit })
  })

  it('COMPLETE — exactly one matching Visit with exactly one linked, non-voided Admission Encounter', () => {
    const visit = maternityVisit({ encounters: [admissionEncounter()] })
    expect(deriveAdmissionState([visit])).toEqual({ status: 'COMPLETE', visit })
  })

  it('COMPLETE — ignores unrelated active visits for the same patient', () => {
    const unrelated = maternityVisit({
      uuid: 'group-session',
      visitType: { uuid: 'group-session-uuid', display: 'Group Session' },
      location: { uuid: OTHER_LOCATION_UUID },
    })
    const visit = maternityVisit({ encounters: [admissionEncounter()] })
    expect(deriveAdmissionState([unrelated, visit])).toEqual({ status: 'COMPLETE', visit })
  })

  it('AMBIGUOUS (MULTIPLE_VISITS) — more than one matching active maternity Visit', () => {
    const first = maternityVisit({ uuid: 'visit-1' })
    const second = maternityVisit({ uuid: 'visit-2' })
    expect(deriveAdmissionState([first, second])).toEqual({ status: 'AMBIGUOUS', reason: 'MULTIPLE_VISITS' })
  })

  it('AMBIGUOUS (MULTIPLE_ENCOUNTERS) — the one matching Visit has more than one linked Admission Encounter', () => {
    const visit = maternityVisit({
      encounters: [admissionEncounter({ uuid: 'e1' }), admissionEncounter({ uuid: 'e2' })],
    })
    expect(deriveAdmissionState([visit])).toEqual({ status: 'AMBIGUOUS', reason: 'MULTIPLE_ENCOUNTERS' })
  })

  it('AMBIGUOUS — a voided duplicate Encounter does not rescue the ambiguity check below it', () => {
    const visit = maternityVisit({
      encounters: [admissionEncounter({ uuid: 'e1' }), admissionEncounter({ uuid: 'e2', voided: true })],
    })
    // Exactly one non-voided linked Encounter — COMPLETE, not AMBIGUOUS.
    expect(deriveAdmissionState([visit])).toEqual({ status: 'COMPLETE', visit })
  })
})
