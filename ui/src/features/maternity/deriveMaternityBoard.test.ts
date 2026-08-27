import { describe, expect, it } from 'vitest'
import { deriveMaternityBoard } from './deriveMaternityBoard'
import {
  DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
  MATERNITE_LOCATION_UUID,
} from '../../api/admissionConstants'
import type { MaternityBoardVisit } from '../../api/types'

const OTHER_VISIT_TYPE_UUID = 'facility-visit-uuid'
const OTHER_LOCATION_UUID = 'inpatient-ward-uuid'

function maternityVisit(overrides: Partial<MaternityBoardVisit> = {}): MaternityBoardVisit {
  return {
    uuid: 'visit-1',
    startDatetime: '2026-08-20T08:00:00.000+0000',
    stopDatetime: null,
    visitType: { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, display: 'Daher Maternity Admission' },
    location: { uuid: MATERNITE_LOCATION_UUID, display: 'Maternité' },
    patient: { uuid: 'patient-1', display: 'Patiente Un', identifiers: [{ identifier: '10001AA' }] },
    ...overrides,
  }
}

describe('deriveMaternityBoard', () => {
  it('no visits at all — empty board', () => {
    expect(deriveMaternityBoard([])).toEqual([])
  })

  it('a canonical active maternity visit appears', () => {
    const visit = maternityVisit()
    expect(deriveMaternityBoard([visit])).toEqual([
      { status: 'ADMITTED', patient: visit.patient, visit, admittedAt: visit.startDatetime },
    ])
  })

  it('an inactive (stopped) maternity visit is excluded', () => {
    const stopped = maternityVisit({ stopDatetime: '2026-08-21T09:00:00.000+0000' })
    expect(deriveMaternityBoard([stopped])).toEqual([])
  })

  it('a visit of the wrong Visit type is excluded, even at the right location', () => {
    const wrongType = maternityVisit({ visitType: { uuid: OTHER_VISIT_TYPE_UUID, display: 'Facility Visit' } })
    expect(deriveMaternityBoard([wrongType])).toEqual([])
  })

  it('a visit at the wrong location is excluded, even with the right Visit type', () => {
    const wrongLocation = maternityVisit({ location: { uuid: OTHER_LOCATION_UUID, display: 'Inpatient Ward' } })
    expect(deriveMaternityBoard([wrongLocation])).toEqual([])
  })

  it('an unrelated visit (wrong type AND location) is excluded', () => {
    const unrelated = maternityVisit({
      visitType: { uuid: OTHER_VISIT_TYPE_UUID, display: 'Home Visit' },
      location: { uuid: OTHER_LOCATION_UUID, display: 'Ward 2' },
    })
    expect(deriveMaternityBoard([unrelated])).toEqual([])
  })

  it('multiple valid patients all render, each as their own entry', () => {
    const first = maternityVisit({ uuid: 'v1', patient: { uuid: 'p1', display: 'Patiente Un' } })
    const second = maternityVisit({ uuid: 'v2', patient: { uuid: 'p2', display: 'Patiente Deux' } })
    const result = deriveMaternityBoard([first, second])
    expect(result).toHaveLength(2)
    expect(result.map((entry) => entry.patient.uuid).sort()).toEqual(['p1', 'p2'])
  })

  it('deterministic ordering — oldest admission first, regardless of input order', () => {
    const newer = maternityVisit({
      uuid: 'v-newer',
      startDatetime: '2026-08-25T10:00:00.000+0000',
      patient: { uuid: 'p-newer', display: 'Admise récemment' },
    })
    const older = maternityVisit({
      uuid: 'v-older',
      startDatetime: '2026-08-20T08:00:00.000+0000',
      patient: { uuid: 'p-older', display: 'Admise depuis plus longtemps' },
    })
    // Input order deliberately reversed vs. expected output order.
    const result = deriveMaternityBoard([newer, older])
    expect(result.map((entry) => entry.patient.uuid)).toEqual(['p-older', 'p-newer'])
  })

  it('excludes an unrelated active visit for the same patient, alongside their genuine maternity one', () => {
    const genuine = maternityVisit({ uuid: 'v1' })
    const unrelated = maternityVisit({
      uuid: 'v2',
      visitType: { uuid: OTHER_VISIT_TYPE_UUID, display: 'OPD Visit' },
      location: { uuid: OTHER_LOCATION_UUID },
    })
    expect(deriveMaternityBoard([genuine, unrelated])).toEqual([
      { status: 'ADMITTED', patient: genuine.patient, visit: genuine, admittedAt: genuine.startDatetime },
    ])
  })

  it('malformed duplicate-active-visit condition — one patient, two active matching visits, collapses to a single AMBIGUOUS entry, never two cards', () => {
    const patient = { uuid: 'p-dup', display: 'Patiente Dupliquée' }
    const first = maternityVisit({ uuid: 'v-dup-1', patient })
    const second = maternityVisit({ uuid: 'v-dup-2', patient, startDatetime: '2026-08-22T10:00:00.000+0000' })
    const result = deriveMaternityBoard([first, second])
    expect(result).toHaveLength(1)
    expect(result[0]).toEqual({
      status: 'AMBIGUOUS',
      patient,
      visits: [first, second],
      admittedAt: first.startDatetime,
    })
  })

  it('an AMBIGUOUS entry is admittedAt the earlier of its two colliding visits, regardless of input order', () => {
    const patient = { uuid: 'p-dup-2', display: 'Patiente Dupliquée 2' }
    const later = maternityVisit({ uuid: 'v-a', patient, startDatetime: '2026-08-22T10:00:00.000+0000' })
    const earlier = maternityVisit({ uuid: 'v-b', patient, startDatetime: '2026-08-15T00:00:00.000+0000' })
    const result = deriveMaternityBoard([later, earlier])
    expect(result).toHaveLength(1)
    expect(result[0].admittedAt).toBe(earlier.startDatetime)
  })

  it('a duplicate-visit patient is still ordered by their earliest admission time', () => {
    const dupPatient = { uuid: 'p-dup', display: 'Patiente Dupliquée' }
    const dupEarly = maternityVisit({ uuid: 'v-dup-1', patient: dupPatient, startDatetime: '2026-08-15T00:00:00.000+0000' })
    const dupLate = maternityVisit({ uuid: 'v-dup-2', patient: dupPatient, startDatetime: '2026-08-22T00:00:00.000+0000' })
    const single = maternityVisit({
      uuid: 'v-single',
      startDatetime: '2026-08-18T00:00:00.000+0000',
      patient: { uuid: 'p-single', display: 'Patiente Seule' },
    })
    const result = deriveMaternityBoard([single, dupLate, dupEarly])
    expect(result.map((entry) => entry.patient.uuid)).toEqual(['p-dup', 'p-single'])
  })
})
