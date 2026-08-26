import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAdmission } from './useAdmission'
import * as client from '../../api/client'
import { ApiError } from '../../api/types'
import {
  ADMISSION_ENCOUNTER_TYPE_UUID,
  DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
  MATERNITE_LOCATION_UUID,
} from '../../api/admissionConstants'
import type { ActiveVisit } from '../../api/types'

vi.mock('../../api/client', () => ({
  verifyAdmissionMetadata: vi.fn(),
  getSession: vi.fn(),
  getActiveVisits: vi.fn(),
  createMaternityVisit: vi.fn(),
  createAdmissionEncounter: vi.fn(),
}))

const PATIENT_UUID = 'patient-uuid'

const READY_SESSION = {
  authenticated: true,
  user: { uuid: 'clinician-uuid', roles: [{ name: 'Daher Clinician Admission' }] },
  currentProvider: { uuid: 'provider-uuid' },
}

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

function withAdmissionEncounter(visit: ActiveVisit): ActiveVisit {
  return {
    ...visit,
    encounters: [{ uuid: 'encounter-1', encounterType: { uuid: ADMISSION_ENCOUNTER_TYPE_UUID }, voided: false }],
  }
}

function mocked() {
  return {
    verifyMetadata: vi.mocked(client.verifyAdmissionMetadata),
    getSession: vi.mocked(client.getSession),
    getActiveVisits: vi.mocked(client.getActiveVisits),
    createVisit: vi.mocked(client.createMaternityVisit),
    createEncounter: vi.mocked(client.createAdmissionEncounter),
  }
}

async function renderReady(initialVisits: ActiveVisit[]) {
  const m = mocked()
  m.verifyMetadata.mockResolvedValue(undefined)
  m.getSession.mockResolvedValue(READY_SESSION)
  m.getActiveVisits.mockResolvedValueOnce(initialVisits)
  const hook = renderHook(() => useAdmission(PATIENT_UUID))
  await waitFor(() => expect(hook.result.current.read.kind).not.toBe('loading'))
  return { ...hook, m }
}

describe('useAdmission', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  it('no admission: resolves NOT_ADMITTED from an empty active-visit list', async () => {
    const { result } = await renderReady([])
    expect(result.current.read).toEqual({ kind: 'resolved', status: 'NOT_ADMITTED' })
  })

  it('one complete admission: resolves COMPLETE and performs zero mutation calls', async () => {
    const visit = withAdmissionEncounter(maternityVisit())
    const { result, m } = await renderReady([visit])
    expect(result.current.read).toEqual({ kind: 'resolved', status: 'COMPLETE', visit })
    expect(m.createVisit).not.toHaveBeenCalled()
    expect(m.createEncounter).not.toHaveBeenCalled()
  })

  it('Visit-only partial admission: resolves PARTIAL_VISIT', async () => {
    const visit = maternityVisit()
    const { result } = await renderReady([visit])
    expect(result.current.read).toEqual({ kind: 'resolved', status: 'PARTIAL_VISIT', visit })
  })

  it('duplicate matching Visits: resolves AMBIGUOUS on initial load, offering no mutation', async () => {
    const { result } = await renderReady([maternityVisit({ uuid: 'v1' }), maternityVisit({ uuid: 'v2' })])
    expect(result.current.read).toEqual({ kind: 'resolved', status: 'AMBIGUOUS', reason: 'MULTIPLE_VISITS' })
  })

  it('duplicate linked Admission Encounters: resolves AMBIGUOUS on initial load', async () => {
    const visit = maternityVisit({
      encounters: [
        { uuid: 'e1', encounterType: { uuid: ADMISSION_ENCOUNTER_TYPE_UUID }, voided: false },
        { uuid: 'e2', encounterType: { uuid: ADMISSION_ENCOUNTER_TYPE_UUID }, voided: false },
      ],
    })
    const { result } = await renderReady([visit])
    expect(result.current.read).toEqual({ kind: 'resolved', status: 'AMBIGUOUS', reason: 'MULTIPLE_ENCOUNTERS' })
  })

  it('fresh success: preflight -> create Visit -> reconcile -> create Encounter -> reconcile -> COMPLETE', async () => {
    const { result, m } = await renderReady([]) // mount load: NOT_ADMITTED

    m.getActiveVisits
      .mockResolvedValueOnce([]) // admit() preflight
      .mockResolvedValueOnce([maternityVisit()]) // reconcile after Visit create
      .mockResolvedValueOnce([withAdmissionEncounter(maternityVisit())]) // reconcile after Encounter create
    m.createVisit.mockResolvedValueOnce({ uuid: 'visit-1' })
    m.createEncounter.mockResolvedValueOnce({ uuid: 'encounter-1' })

    await act(async () => {
      await result.current.admit()
    })

    expect(result.current.read).toEqual({
      kind: 'resolved',
      status: 'COMPLETE',
      visit: withAdmissionEncounter(maternityVisit()),
    })
    expect(m.createVisit).toHaveBeenCalledTimes(1)
    expect(m.createEncounter).toHaveBeenCalledTimes(1)
    expect(m.createEncounter).toHaveBeenCalledWith(PATIENT_UUID, 'visit-1')
    // Visit before Encounter, never concurrent.
    expect(m.createVisit.mock.invocationCallOrder[0]).toBeLessThan(
      m.createEncounter.mock.invocationCallOrder[0],
    )
    expect(result.current.mutationError).toBeNull()
  })

  it('Visit ambiguous -> reconcile finds exactly one Visit -> continues using it (no second Visit POST)', async () => {
    const { result, m } = await renderReady([])

    m.getActiveVisits
      .mockResolvedValueOnce([]) // admit() preflight
      .mockResolvedValueOnce([maternityVisit()]) // reconcile finds the Visit that was actually created
      .mockResolvedValueOnce([withAdmissionEncounter(maternityVisit())]) // reconcile after Encounter create
    m.createVisit.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))
    m.createEncounter.mockResolvedValueOnce({ uuid: 'encounter-1' })

    await act(async () => {
      await result.current.admit()
    })

    expect(m.createVisit).toHaveBeenCalledTimes(1)
    expect(m.createEncounter).toHaveBeenCalledTimes(1)
    expect(result.current.read).toMatchObject({ status: 'COMPLETE' })
  })

  it('Visit ambiguous -> reconcile finds zero -> no automatic retry, unresolved', async () => {
    const { result, m } = await renderReady([])

    m.getActiveVisits
      .mockResolvedValueOnce([]) // admit() preflight
      .mockResolvedValueOnce([]) // reconcile: genuinely never committed
    m.createVisit.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))

    await act(async () => {
      await result.current.admit()
    })

    expect(m.createVisit).toHaveBeenCalledTimes(1)
    expect(m.createEncounter).not.toHaveBeenCalled()
    expect(result.current.read).toEqual({ kind: 'resolved', status: 'NOT_ADMITTED' })
    expect(result.current.mutationError).toBe('AMBIGUOUS_WRITE')
  })

  it('Visit ambiguous -> reconcile finds >1 -> AMBIGUOUS, stops (no Encounter attempt)', async () => {
    const { result, m } = await renderReady([])

    m.getActiveVisits
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([maternityVisit({ uuid: 'v1' }), maternityVisit({ uuid: 'v2' })])
    m.createVisit.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))

    await act(async () => {
      await result.current.admit()
    })

    expect(m.createEncounter).not.toHaveBeenCalled()
    expect(result.current.read).toEqual({ kind: 'resolved', status: 'AMBIGUOUS', reason: 'MULTIPLE_VISITS' })
  })

  it('Encounter 500/ambiguous -> reconcile finds the Encounter -> COMPLETE', async () => {
    const partial = maternityVisit()
    const { result, m } = await renderReady([partial])

    m.getActiveVisits
      .mockResolvedValueOnce([partial]) // resume() re-check
      .mockResolvedValueOnce([withAdmissionEncounter(partial)]) // reconcile after Encounter attempt
    m.createEncounter.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))

    await act(async () => {
      await result.current.resume()
    })

    expect(m.createVisit).not.toHaveBeenCalled()
    expect(m.createEncounter).toHaveBeenCalledTimes(1)
    expect(result.current.read).toMatchObject({ status: 'COMPLETE' })
    expect(result.current.mutationError).toBeNull()
  })

  it('Encounter 500/ambiguous -> reconcile finds zero -> PARTIAL_VISIT, no automatic retry', async () => {
    const partial = maternityVisit()
    const { result, m } = await renderReady([partial])

    m.getActiveVisits
      .mockResolvedValueOnce([partial])
      .mockResolvedValueOnce([partial]) // still zero linked Encounters
    m.createEncounter.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))

    await act(async () => {
      await result.current.resume()
    })

    expect(m.createEncounter).toHaveBeenCalledTimes(1)
    expect(result.current.read).toEqual({ kind: 'resolved', status: 'PARTIAL_VISIT', visit: partial })
    expect(result.current.mutationError).toBe('AMBIGUOUS_WRITE')
  })

  it('PARTIAL_VISIT resume: exactly one Encounter POST, zero Visit POSTs', async () => {
    const partial = maternityVisit()
    const { result, m } = await renderReady([partial])

    m.getActiveVisits.mockResolvedValueOnce([partial]).mockResolvedValueOnce([withAdmissionEncounter(partial)])
    m.createEncounter.mockResolvedValueOnce({ uuid: 'encounter-1' })

    await act(async () => {
      await result.current.resume()
    })

    expect(m.createVisit).not.toHaveBeenCalled()
    expect(m.createEncounter).toHaveBeenCalledTimes(1)
    expect(result.current.read).toMatchObject({ status: 'COMPLETE' })
  })

  it('resume never creates a second Visit even if state moved to COMPLETE since the last read', async () => {
    const partial = maternityVisit()
    const { result, m } = await renderReady([partial])

    // Someone else completed it between the last read and this click.
    m.getActiveVisits.mockResolvedValueOnce([withAdmissionEncounter(partial)])

    await act(async () => {
      await result.current.resume()
    })

    expect(m.createVisit).not.toHaveBeenCalled()
    expect(m.createEncounter).not.toHaveBeenCalled()
    expect(result.current.read).toMatchObject({ status: 'COMPLETE' })
  })

  it('COMPLETE on mount: admit() is a no-op producing zero POSTs (defense in depth beyond the UI hiding the button)', async () => {
    const visit = withAdmissionEncounter(maternityVisit())
    const { result, m } = await renderReady([visit])

    m.getActiveVisits.mockResolvedValueOnce([visit]) // admit()'s own preflight sees COMPLETE

    await act(async () => {
      await result.current.admit()
    })

    expect(m.createVisit).not.toHaveBeenCalled()
    expect(m.createEncounter).not.toHaveBeenCalled()
  })

  it('double submit: a second admit() call fired synchronously starts no second sequence', async () => {
    const { result, m } = await renderReady([])

    m.getActiveVisits
      .mockResolvedValueOnce([]) // preflight
      .mockResolvedValueOnce([maternityVisit()]) // reconcile after Visit
      .mockResolvedValueOnce([withAdmissionEncounter(maternityVisit())]) // reconcile after Encounter
    m.createVisit.mockResolvedValueOnce({ uuid: 'visit-1' })
    m.createEncounter.mockResolvedValueOnce({ uuid: 'encounter-1' })

    // §22: `inFlight.current = true` is the first statement admit() runs,
    // synchronously, before any await — so a second call in the same tick
    // (the double-click/double-Enter case) must see the guard already set
    // and return immediately, without a second read/write sequence.
    await act(async () => {
      const first = result.current.admit()
      const second = result.current.admit()
      await Promise.all([first, second])
    })

    expect(m.createVisit).toHaveBeenCalledTimes(1)
    expect(m.createEncounter).toHaveBeenCalledTimes(1)
  })

  it('reads NOT_AUTHORIZED as "unavailable" (e.g. Reception), not an alarming error', async () => {
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue({
      authenticated: true,
      user: { uuid: 'reception-uuid', roles: [{ name: 'Daher Reception' }] },
      currentProvider: null,
    })
    m.getActiveVisits.mockRejectedValueOnce(new ApiError('NOT_AUTHORIZED', 'denied'))

    const { result } = renderHook(() => useAdmission(PATIENT_UUID))
    await waitFor(() => expect(result.current.read.kind).not.toBe('loading'))

    expect(result.current.read).toEqual({ kind: 'unavailable' })
    expect(result.current.readiness).toEqual({ status: 'NOT_ELIGIBLE' })
  })

  it('fails closed to a read error, offering no mutation, when metadata drift is detected', async () => {
    const m = mocked()
    m.verifyMetadata.mockRejectedValueOnce(new ApiError('SERVER_ERROR', 'drift'))

    const { result } = renderHook(() => useAdmission(PATIENT_UUID))
    await waitFor(() => expect(result.current.read.kind).not.toBe('loading'))

    expect(result.current.read).toEqual({ kind: 'read_error', code: 'SERVER_ERROR' })
    expect(result.current.readiness).toBeNull()
    expect(m.getSession).not.toHaveBeenCalled()
    expect(m.getActiveVisits).not.toHaveBeenCalled()
  })

  it('PROVIDER_MISSING readiness fails closed even though the admission role is held', async () => {
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue({
      authenticated: true,
      user: { uuid: 'clinician-uuid', roles: [{ name: 'Daher Clinician Admission' }] },
      currentProvider: null,
    })
    m.getActiveVisits.mockResolvedValueOnce([])

    const { result } = renderHook(() => useAdmission(PATIENT_UUID))
    await waitFor(() => expect(result.current.read.kind).not.toBe('loading'))

    expect(result.current.readiness).toEqual({ status: 'PROVIDER_MISSING' })
  })
})
