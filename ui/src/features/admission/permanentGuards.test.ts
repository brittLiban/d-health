import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAdmission } from './useAdmission'
import * as client from '../../api/client'
import { deriveAdmissionState } from './deriveAdmissionState'
import {
  ADMISSION_ENCOUNTER_TYPE_UUID,
  DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
  MATERNITE_LOCATION_UUID,
} from '../../api/admissionConstants'
import type { ActiveVisit } from '../../api/types'

// §31 — these must fail if a future change reintroduces any of these
// behaviours, regardless of how the implementation is refactored.

vi.mock('../../api/client', () => ({
  verifyAdmissionMetadata: vi.fn(),
  getSession: vi.fn(),
  getActiveVisits: vi.fn(),
  createMaternityVisit: vi.fn(),
  createAdmissionEncounter: vi.fn(),
  endAdmission: vi.fn(),
}))

// Raw source text via Vite's own glob import (no Node fs/path — this
// project's browser tsconfig deliberately carries no @types/node), read at
// test time so a future edit to any of these files is what these checks
// actually see, not a stale snapshot.
const featureModules = import.meta.glob('./*.{ts,tsx}', {
  eager: true,
  query: '?raw',
  import: 'default',
}) as Record<string, string>
const clientModule = import.meta.glob('../../api/client.ts', {
  eager: true,
  query: '?raw',
  import: 'default',
}) as Record<string, string>

function featureSourceText(): string {
  return Object.entries(featureModules)
    .filter(([path]) => !path.includes('.test.'))
    .map(([, content]) => content)
    .join('\n')
}

function clientSourceText(): string {
  return Object.values(clientModule)[0] ?? ''
}

describe('permanent safety guards — static source checks', () => {
  it('never references bed assignment (Assign Beds, /beds) anywhere in the admission feature or client', () => {
    const text = featureSourceText() + clientSourceText()
    expect(text).not.toMatch(/Assign Beds/i)
    expect(text).not.toMatch(/\/beds\b/)
  })

  it('never references the upstream Facility Visit / Inpatient Ward fixtures by name', () => {
    expect(featureSourceText()).not.toContain('Facility Visit')
    expect(featureSourceText()).not.toContain('Inpatient Ward')
  })

  it('never references browser persistence (localStorage/sessionStorage/indexedDB) in the admission feature', () => {
    expect(featureSourceText()).not.toMatch(/localStorage|sessionStorage|indexedDB/i)
  })

  // UI-015/ADR-030 — "Terminer l'admission" is deliberately narrower than
  // core's /visit endpoint: no stopDatetime, discharge, transfer, diagnosis,
  // or outcome field is ever part of this feature's own request shape. These
  // guards must fail if a future change reintroduces any of them.
  it('never sends a request body on the end-admission call — the Visit UUID in the URL path is the only input', () => {
    expect(clientSourceText()).not.toMatch(/endAdmission[\s\S]{0,400}?body:/)
  })

  it('never references discharge, transfer, or diagnosis anywhere in the admission feature or client', () => {
    const text = featureSourceText() + clientSourceText()
    expect(text).not.toMatch(/discharge|transfer|diagnosis/i)
  })
})

function mocked() {
  return {
    verifyMetadata: vi.mocked(client.verifyAdmissionMetadata),
    getSession: vi.mocked(client.getSession),
    getActiveVisits: vi.mocked(client.getActiveVisits),
    createVisit: vi.mocked(client.createMaternityVisit),
    createEncounter: vi.mocked(client.createAdmissionEncounter),
  }
}

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

describe('permanent safety guards — runtime behavior', () => {
  afterEach(() => {
    vi.resetAllMocks()
    vi.restoreAllMocks()
  })

  it('never calls localStorage/sessionStorage during a full fresh-admission flow', async () => {
    const setLocal = vi.spyOn(Storage.prototype, 'setItem')
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(READY_SESSION)
    m.getActiveVisits
      .mockResolvedValueOnce([]) // mount
      .mockResolvedValueOnce([]) // admit preflight
      .mockResolvedValueOnce([maternityVisit()]) // after Visit
      .mockResolvedValueOnce([{ ...maternityVisit(), encounters: [{ uuid: 'e1', encounterType: { uuid: ADMISSION_ENCOUNTER_TYPE_UUID }, voided: false }] }]) // after Encounter
    m.createVisit.mockResolvedValueOnce({ uuid: 'visit-1' })
    m.createEncounter.mockResolvedValueOnce({ uuid: 'encounter-1' })

    const { result } = renderHook(() => useAdmission('patient-uuid'))
    await waitFor(() => expect(result.current.read.kind).not.toBe('loading'))
    await act(async () => {
      await result.current.admit()
    })

    expect(setLocal).not.toHaveBeenCalled()
  })

  it('never fires a second Visit POST once one has been confirmed, across a full fresh-admission flow', async () => {
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(READY_SESSION)
    m.getActiveVisits
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([maternityVisit()])
      .mockResolvedValueOnce([{ ...maternityVisit(), encounters: [{ uuid: 'e1', encounterType: { uuid: ADMISSION_ENCOUNTER_TYPE_UUID }, voided: false }] }])
    m.createVisit.mockResolvedValueOnce({ uuid: 'visit-1' })
    m.createEncounter.mockResolvedValueOnce({ uuid: 'encounter-1' })

    const { result } = renderHook(() => useAdmission('patient-uuid'))
    await waitFor(() => expect(result.current.read.kind).not.toBe('loading'))
    await act(async () => {
      await result.current.admit()
    })

    expect(m.createVisit).toHaveBeenCalledTimes(1)
  })

  it('a Visit with zero linked, non-voided Admission Encounters is never derived as COMPLETE', () => {
    const visit = maternityVisit({
      encounters: [{ uuid: 'e1', encounterType: { uuid: ADMISSION_ENCOUNTER_TYPE_UUID }, voided: true }],
    })
    expect(deriveAdmissionState([visit]).status).not.toBe('COMPLETE')
  })

  it('the Visit and Encounter mutation requests both carry X-Daher-App-Request', async () => {
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, status: 201, text: async () => JSON.stringify({ uuid: 'x' }) })
    vi.stubGlobal('fetch', fetchSpy)
    const { createMaternityVisit, createAdmissionEncounter } = await vi.importActual<typeof client>('../../api/client')
    await createMaternityVisit('patient-uuid')
    await createAdmissionEncounter('patient-uuid', 'visit-uuid')
    vi.unstubAllGlobals()

    for (const call of fetchSpy.mock.calls) {
      const [, init] = call as [string, RequestInit]
      expect((init.headers as Record<string, string>)['X-Daher-App-Request']).toBe('1')
    }
  })
})
