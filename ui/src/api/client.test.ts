import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createAdmissionEncounter,
  createMaternityVisit,
  createPatient,
  discoverIdentifierSource,
  endAdmission,
  findPatientByIdentifier,
  generateIdentifier,
  getActiveVisits,
  getMaternityBoardVisits,
  getSession,
  searchPatients,
  verifyAdmissionMetadata,
} from './client'
import {
  ADMISSION_ENCOUNTER_TYPE_UUID,
  DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
  MATERNITE_LOCATION_UUID,
} from './admissionConstants'
import { ApiError, type IdentifierSource, type PatientRegistration } from './types'

function mockFetchOnce(response: { ok: boolean; status: number; json: () => Promise<unknown> }) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response))
}

function textResponse(ok: boolean, status: number, body: unknown) {
  return { ok, status, text: async () => JSON.stringify(body) }
}

const SOURCE: IdentifierSource = {
  uuid: 'source-uuid',
  identifierTypeUuid: 'type-uuid',
  locationBehavior: 'NOT_USED',
}

const REGISTRATION: PatientRegistration = {
  givenName: 'Synthetique',
  familyName: 'Test',
  gender: 'F',
  birthdate: '1995-06-15',
}

describe('searchPatients', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns results on a successful search', async () => {
    mockFetchOnce({
      ok: true,
      status: 200,
      json: async () => ({ results: [{ uuid: '1', display: 'Test Patient' }] }),
    })
    const results = await searchPatients('Test')
    expect(results).toEqual([{ uuid: '1', display: 'Test Patient' }])
  })

  it('fetches the same-origin /openmrs/* path UI-004 wires through Caddy in production, not a /api/* dev-only path', async () => {
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ results: [] }) })
    vi.stubGlobal('fetch', fetchSpy)
    await searchPatients('Test')
    const [url] = fetchSpy.mock.calls[0] as [string]
    expect(url.startsWith('/openmrs/ws/rest/v1/patient?q=')).toBe(true)
  })

  it('returns an empty array for a nonexistent term rather than throwing', async () => {
    mockFetchOnce({ ok: true, status: 200, json: async () => ({ results: [] }) })
    const results = await searchPatients('nobody')
    expect(results).toEqual([])
  })

  it('maps a normalized NOT_AUTHORIZED body to an ApiError with that code', async () => {
    mockFetchOnce({
      ok: false,
      status: 401,
      json: async () => ({ error: { code: 'NOT_AUTHORIZED', message: 'nope' } }),
    })
    await expect(searchPatients('x')).rejects.toMatchObject({ code: 'NOT_AUTHORIZED' })
  })

  it('falls back to SERVER_ERROR for an unrecognized error code', async () => {
    mockFetchOnce({
      ok: false,
      status: 500,
      json: async () => ({ error: { code: 'SOMETHING_UNEXPECTED', message: 'boom' } }),
    })
    await expect(searchPatients('x')).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })

  it('throws NETWORK_ERROR when fetch itself rejects', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(searchPatients('x')).rejects.toBeInstanceOf(ApiError)
    await expect(searchPatients('x')).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
  })
})

describe('discoverIdentifierSource', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns the one identifier source this pinned stack has', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        textResponse(true, 200, {
          results: [
            { uuid: 'source-uuid', identifierType: { uuid: 'type-uuid' }, locationBehavior: 'NOT_USED' },
          ],
        }),
      ),
    )
    const source = await discoverIdentifierSource()
    expect(source).toEqual(SOURCE)
  })

  it('fails closed as SERVER_ERROR when zero sources are configured, rather than guessing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(true, 200, { results: [] })))
    await expect(discoverIdentifierSource()).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })

  it('fails closed as SERVER_ERROR when more than one source is configured, rather than picking one', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        textResponse(true, 200, {
          results: [
            { uuid: 'a', identifierType: { uuid: 't1' } },
            { uuid: 'b', identifierType: { uuid: 't2' } },
          ],
        }),
      ),
    )
    await expect(discoverIdentifierSource()).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })
})

describe('generateIdentifier', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns the server-generated identifier, never a client-invented one', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(true, 201, { identifier: '100001W' })))
    const identifier = await generateIdentifier('source-uuid')
    expect(identifier).toBe('100001W')
  })

  it('POSTs with the mutation-guard header, never a bare request', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(textResponse(true, 201, { identifier: '100001W' }))
    vi.stubGlobal('fetch', fetchSpy)
    await generateIdentifier('source-uuid')
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/openmrs/ws/rest/v1/idgen/identifiersource/source-uuid/identifier')
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>)['X-Daher-App-Request']).toBe('1')
  })
})

describe('findPatientByIdentifier', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('queries the exact-match identifier endpoint, not the fuzzy q= search', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(textResponse(true, 200, { results: [] }))
    vi.stubGlobal('fetch', fetchSpy)
    await findPatientByIdentifier('100001W')
    const [url] = fetchSpy.mock.calls[0] as [string]
    expect(url).toBe('/openmrs/ws/rest/v1/patient?identifier=100001W')
  })

  it('returns zero results for an identifier nobody has, without throwing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(true, 200, { results: [] })))
    expect(await findPatientByIdentifier('nobody')).toEqual([])
  })

  it('returns exactly one match for an existing identifier', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(textResponse(true, 200, { results: [{ uuid: 'p1', display: 'X' }] })),
    )
    expect(await findPatientByIdentifier('100001W')).toEqual([{ uuid: 'p1', display: 'X' }])
  })
})

describe('createPatient', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs the minimum verified payload with the mutation-guard header and no location field', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(textResponse(true, 201, { uuid: 'new-uuid', display: '100001W - Test' }))
    vi.stubGlobal('fetch', fetchSpy)
    const patient = await createPatient(REGISTRATION, '100001W', SOURCE)
    expect(patient).toEqual({ uuid: 'new-uuid', display: '100001W - Test' })

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/openmrs/ws/rest/v1/patient')
    expect((init.headers as Record<string, string>)['X-Daher-App-Request']).toBe('1')
    const body = JSON.parse(init.body as string)
    expect(body.identifiers).toEqual([{ identifier: '100001W', identifierType: 'type-uuid', preferred: true }])
    expect(body.identifiers[0]).not.toHaveProperty('location')
    expect(body.person).toEqual({
      names: [{ givenName: 'Synthetique', familyName: 'Test' }],
      gender: 'F',
      birthdate: '1995-06-15',
    })
  })

  it('refuses to guess a location when locationBehavior is not the one verified value', async () => {
    vi.stubGlobal('fetch', vi.fn())
    await expect(
      createPatient(REGISTRATION, '100001W', { ...SOURCE, locationBehavior: 'REQUIRED' }),
    ).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })

  it('treats a dropped connection as AMBIGUOUS_WRITE, never a definite failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(createPatient(REGISTRATION, '100001W', SOURCE)).rejects.toMatchObject({
      code: 'AMBIGUOUS_WRITE',
    })
  })

  it('treats an unparsable 2xx body as AMBIGUOUS_WRITE, not a silent success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 201, text: async () => 'not json' }))
    await expect(createPatient(REGISTRATION, '100001W', SOURCE)).rejects.toMatchObject({
      code: 'AMBIGUOUS_WRITE',
    })
  })

  it('normalizes a "Privileges required" 400 to NOT_AUTHORIZED, regardless of status (UI-001A)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        textResponse(false, 400, {
          error: { message: 'Invalid Submission', detail: 'Privileges required: Add Patients' },
        }),
      ),
    )
    await expect(createPatient(REGISTRATION, '100001W', SOURCE)).rejects.toMatchObject({
      code: 'NOT_AUTHORIZED',
    })
  })

  it('never leaks the raw Java stack trace into the thrown error message', async () => {
    const stackTrace = 'at org.openmrs.aop.AuthorizationAdvice.throwUnauthorized(AuthorizationAdvice.java:120)'
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        textResponse(false, 400, {
          error: { message: 'Invalid Submission', detail: `Privileges required: Add Patients\n\t${stackTrace}` },
        }),
      ),
    )
    try {
      await createPatient(REGISTRATION, '100001W', SOURCE)
      expect.unreachable()
    } catch (error) {
      expect((error as ApiError).message).not.toContain('AuthorizationAdvice')
      expect((error as ApiError).message).not.toContain('org.openmrs')
    }
  })

  it('maps a duplicate identifier to CONFLICT', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        textResponse(false, 400, {
          error: { globalErrors: [{ message: 'Identifier 100001W already in use by another patient' }] },
        }),
      ),
    )
    await expect(createPatient(REGISTRATION, '100001W', SOURCE)).rejects.toMatchObject({ code: 'CONFLICT' })
  })

  it('maps a generic 400 to VALIDATION_ERROR', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(false, 400, { error: { message: 'bad' } })))
    await expect(createPatient(REGISTRATION, '100001W', SOURCE)).rejects.toMatchObject({
      code: 'VALIDATION_ERROR',
    })
  })
})

// UI-013 — clinician maternity admission.

describe('verifyAdmissionMetadata', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function metadataResponses(overrides: {
    visitType?: unknown[]
    encounterType?: unknown[]
    location?: unknown[]
  } = {}) {
    const visitType = overrides.visitType ?? [
      { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, retired: false },
    ]
    const encounterType = overrides.encounterType ?? [
      { uuid: ADMISSION_ENCOUNTER_TYPE_UUID, retired: false },
    ]
    const location = overrides.location ?? [{ uuid: MATERNITE_LOCATION_UUID, retired: false }]
    const fetchSpy = vi.fn((url: string) => {
      if (url.includes('/visittype')) return Promise.resolve(textResponse(true, 200, { results: visitType }))
      if (url.includes('/encountertype')) return Promise.resolve(textResponse(true, 200, { results: encounterType }))
      if (url.includes('/location')) return Promise.resolve(textResponse(true, 200, { results: location }))
      throw new Error(`unexpected url ${url}`)
    })
    vi.stubGlobal('fetch', fetchSpy)
    return fetchSpy
  }

  it('resolves when exactly one non-retired, UUID-matching result exists for each artifact', async () => {
    metadataResponses()
    await expect(verifyAdmissionMetadata()).resolves.toBeUndefined()
  })

  it('fails closed when the Visit type is missing (0 results)', async () => {
    metadataResponses({ visitType: [] })
    await expect(verifyAdmissionMetadata()).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })

  it('fails closed when the Visit type is duplicated (>1 result)', async () => {
    metadataResponses({
      visitType: [
        { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, retired: false },
        { uuid: 'some-other-uuid', retired: false },
      ],
    })
    await expect(verifyAdmissionMetadata()).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })

  it('fails closed when the only match is retired', async () => {
    metadataResponses({ visitType: [{ uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, retired: true }] })
    await expect(verifyAdmissionMetadata()).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })

  it('fails closed on a UUID mismatch for the pinned Daher-owned Visit type (never silently adopts it)', async () => {
    metadataResponses({ visitType: [{ uuid: 'drifted-uuid', retired: false }] })
    await expect(verifyAdmissionMetadata()).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })

  it('fails closed when the Maternité location is missing', async () => {
    metadataResponses({ location: [] })
    await expect(verifyAdmissionMetadata()).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })

  it('fails closed when the Admission encounter type is missing', async () => {
    metadataResponses({ encounterType: [] })
    await expect(verifyAdmissionMetadata()).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })
})

describe('getSession', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns the authenticated session shape, including a null currentProvider', async () => {
    mockFetchOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        textResponse(true, 200, {
          authenticated: true,
          user: { uuid: 'user-uuid', roles: [{ name: 'Daher Clinician Admission' }] },
          currentProvider: null,
        }),
      ),
    )
    const session = await getSession()
    expect(session).toEqual({
      authenticated: true,
      user: { uuid: 'user-uuid', roles: [{ name: 'Daher Clinician Admission' }] },
      currentProvider: null,
    })
  })

  it('treats a dropped connection as NETWORK_ERROR', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(getSession()).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
  })
})

describe('getActiveVisits', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('fetches with includeInactive=false and a custom representation carrying embedded encounters', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(textResponse(true, 200, { results: [] }))
    vi.stubGlobal('fetch', fetchSpy)
    await getActiveVisits('patient-uuid')
    const [url] = fetchSpy.mock.calls[0] as [string]
    expect(url).toContain('/openmrs/ws/rest/v1/visit?patient=patient-uuid')
    expect(url).toContain('includeInactive=false')
    expect(url).toContain('encounters')
    expect(url).toContain('encounterType')
  })

  it('returns the parsed results array', async () => {
    const visit = {
      uuid: 'visit-uuid',
      visitType: { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, display: 'Daher Maternity Admission' },
      location: { uuid: MATERNITE_LOCATION_UUID, display: 'Maternité' },
      stopDatetime: null,
      encounters: [],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(true, 200, { results: [visit] })))
    await expect(getActiveVisits('patient-uuid')).resolves.toEqual([visit])
  })

  it('propagates NOT_AUTHORIZED on a Reception-shaped 403 rather than an empty list', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(textResponse(false, 403, { error: { detail: 'Privileges required: Get Visits' } })),
    )
    await expect(getActiveVisits('patient-uuid')).rejects.toMatchObject({ code: 'NOT_AUTHORIZED' })
  })
})

describe('createMaternityVisit', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs exactly the proven payload shape with the pinned Daher visit type and Maternité location', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(textResponse(true, 201, { uuid: 'new-visit-uuid' }))
    vi.stubGlobal('fetch', fetchSpy)
    const visit = await createMaternityVisit('patient-uuid')
    expect(visit).toEqual({ uuid: 'new-visit-uuid' })

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/openmrs/ws/rest/v1/visit')
    expect((init.headers as Record<string, string>)['X-Daher-App-Request']).toBe('1')
    const body = JSON.parse(init.body as string)
    expect(body).toEqual({
      patient: 'patient-uuid',
      visitType: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
      location: MATERNITE_LOCATION_UUID,
    })
  })

  it('never sends the upstream Facility Visit UUID', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(textResponse(true, 201, { uuid: 'new-visit-uuid' }))
    vi.stubGlobal('fetch', fetchSpy)
    await createMaternityVisit('patient-uuid')
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(init.body as string).not.toContain('7b0f5697-27e3-40c4-8bae-f4049abfb4ed')
  })

  it('treats a dropped connection as AMBIGUOUS_WRITE', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(createMaternityVisit('patient-uuid')).rejects.toMatchObject({ code: 'AMBIGUOUS_WRITE' })
  })

  it('treats an unparsable 2xx body as AMBIGUOUS_WRITE', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 201, text: async () => 'not json' }))
    await expect(createMaternityVisit('patient-uuid')).rejects.toMatchObject({ code: 'AMBIGUOUS_WRITE' })
  })

  it('treats a 5xx as AMBIGUOUS_WRITE, never a definite failure (ADR-017C write-succeeds/response-lies)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(false, 500, { error: { message: 'boom' } })))
    await expect(createMaternityVisit('patient-uuid')).rejects.toMatchObject({ code: 'AMBIGUOUS_WRITE' })
  })

  it('normalizes a "Privileges required" rejection to NOT_AUTHORIZED, a definite pre-save rejection', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(textResponse(false, 403, { error: { detail: 'Privileges required: Add Visits' } })),
    )
    await expect(createMaternityVisit('patient-uuid')).rejects.toMatchObject({ code: 'NOT_AUTHORIZED' })
  })

  it('maps a generic 400 to VALIDATION_ERROR, a definite pre-save rejection', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(false, 400, { error: { message: 'bad' } })))
    await expect(createMaternityVisit('patient-uuid')).rejects.toMatchObject({ code: 'VALIDATION_ERROR' })
  })

  it('never leaks a raw backend stack trace into the thrown error message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        textResponse(false, 500, {
          error: { message: 'boom', detail: 'at org.openmrs.SomeClass.method(SomeClass.java:1)' },
        }),
      ),
    )
    try {
      await createMaternityVisit('patient-uuid')
      expect.unreachable()
    } catch (error) {
      expect((error as ApiError).message).not.toContain('org.openmrs')
    }
  })
})

describe('createAdmissionEncounter', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs exactly the proven payload shape, with the confirmed Visit uuid and no encounterProviders key', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(textResponse(true, 201, { uuid: 'new-encounter-uuid' }))
    vi.stubGlobal('fetch', fetchSpy)
    const encounter = await createAdmissionEncounter('patient-uuid', 'visit-uuid')
    expect(encounter).toEqual({ uuid: 'new-encounter-uuid' })

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/openmrs/ws/rest/v1/encounter')
    expect((init.headers as Record<string, string>)['X-Daher-App-Request']).toBe('1')
    const body = JSON.parse(init.body as string)
    expect(body).toEqual({
      patient: 'patient-uuid',
      encounterType: ADMISSION_ENCOUNTER_TYPE_UUID,
      location: MATERNITE_LOCATION_UUID,
      visit: 'visit-uuid',
    })
    // §6 — the exact permanent safety property: never populate
    // encounterProviders, not even as an empty array.
    expect(body).not.toHaveProperty('encounterProviders')
    expect(Object.keys(body)).not.toContain('encounterProviders')
  })

  it('never sends bed assignment, observations, diagnoses, or orders fields', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(textResponse(true, 201, { uuid: 'new-encounter-uuid' }))
    vi.stubGlobal('fetch', fetchSpy)
    await createAdmissionEncounter('patient-uuid', 'visit-uuid')
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(init.body as string)
    for (const forbidden of ['bed', 'beds', 'obs', 'observations', 'diagnoses', 'orders', 'encounterProviders']) {
      expect(body).not.toHaveProperty(forbidden)
    }
  })

  it('treats a dropped connection as AMBIGUOUS_WRITE', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(createAdmissionEncounter('patient-uuid', 'visit-uuid')).rejects.toMatchObject({
      code: 'AMBIGUOUS_WRITE',
    })
  })

  it('treats an unparsable 2xx body as AMBIGUOUS_WRITE', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 201, text: async () => 'not json' }))
    await expect(createAdmissionEncounter('patient-uuid', 'visit-uuid')).rejects.toMatchObject({
      code: 'AMBIGUOUS_WRITE',
    })
  })

  it('routes a 5xx to AMBIGUOUS_WRITE/reconciliation semantics, never a definite failure (ADR-017C)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(false, 500, { error: { message: 'boom' } })))
    await expect(createAdmissionEncounter('patient-uuid', 'visit-uuid')).rejects.toMatchObject({
      code: 'AMBIGUOUS_WRITE',
    })
  })

  it('normalizes a "Privileges required" rejection to NOT_AUTHORIZED', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(textResponse(false, 403, { error: { detail: 'Privileges required: Add Encounters' } })),
    )
    await expect(createAdmissionEncounter('patient-uuid', 'visit-uuid')).rejects.toMatchObject({
      code: 'NOT_AUTHORIZED',
    })
  })

  it('never leaks a raw backend stack trace into the thrown error message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        textResponse(false, 500, {
          error: { message: 'boom', detail: 'at org.openmrs.SomeClass.method(SomeClass.java:1)' },
        }),
      ),
    )
    try {
      await createAdmissionEncounter('patient-uuid', 'visit-uuid')
      expect.unreachable()
    } catch (error) {
      expect((error as ApiError).message).not.toContain('org.openmrs')
    }
  })
})

describe('getMaternityBoardVisits', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('fetches the facility-wide query — location + visitType + includeInactive=false, no patient filter', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(textResponse(true, 200, { results: [] }))
    vi.stubGlobal('fetch', fetchSpy)
    await getMaternityBoardVisits()
    const [url] = fetchSpy.mock.calls[0] as [string]
    expect(url).toContain('/openmrs/ws/rest/v1/visit?location=' + MATERNITE_LOCATION_UUID)
    expect(url).toContain('visitType=' + DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID)
    expect(url).toContain('includeInactive=false')
    expect(url).not.toContain('patient=')
    expect(url).toContain('identifiers')
  })

  it('returns the parsed visits array and truncated=false when there is no next link', async () => {
    const visit = {
      uuid: 'visit-1',
      startDatetime: '2026-08-20T08:00:00.000+0000',
      stopDatetime: null,
      visitType: { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, display: 'Daher Maternity Admission' },
      location: { uuid: MATERNITE_LOCATION_UUID, display: 'Maternité' },
      patient: { uuid: 'p1', display: 'Patiente Un', identifiers: [{ identifier: '10001AA' }] },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(true, 200, { results: [visit] })))
    await expect(getMaternityBoardVisits()).resolves.toEqual({ visits: [visit], truncated: false })
  })

  it('truncated=true when the response carries a "next" pagination link — the ward exceeds the page size', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(textResponse(true, 200, { results: [], links: [{ rel: 'next' }] })),
    )
    await expect(getMaternityBoardVisits()).resolves.toEqual({ visits: [], truncated: true })
  })

  it('propagates NOT_AUTHORIZED on a Reception-shaped 403, never an empty board', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(textResponse(false, 403, { error: { detail: 'Privileges required: Get Visit Types' } })),
    )
    await expect(getMaternityBoardVisits()).rejects.toMatchObject({ code: 'NOT_AUTHORIZED' })
  })

  it('a missing/retired location 404 (with a full Java stack trace in `detail`) maps to SERVER_ERROR without leaking the trace', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        textResponse(false, 404, {
          error: {
            message: "Object with given uuid doesn't exist [null]",
            detail: 'org.openmrs.module.webservices.rest.web.response.ObjectNotFoundException\n\tat org.openmrs...',
          },
        }),
      ),
    )
    try {
      await getMaternityBoardVisits()
      expect.unreachable()
    } catch (error) {
      expect((error as ApiError).code).toBe('SERVER_ERROR')
      expect((error as ApiError).message).not.toContain('org.openmrs')
    }
  })

  it('NETWORK_ERROR on a dropped connection', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('fetch failed')),
    )
    await expect(getMaternityBoardVisits()).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
  })
})

// UI-015F §13 — fault injection at the actual fetch boundary, not just against
// an already-classified ApiError. These are the cases a live container cannot
// cleanly reproduce (a commit whose response is lost in transit) — this is
// the honest way to cover them, per the ticket's own instruction not to burn
// time trying to induce real mid-flight network loss against a live backend.
describe('endAdmission', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs the narrow path with the visit uuid, no request body, and the CSRF header', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      textResponse(true, 200, { status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' }),
    )
    vi.stubGlobal('fetch', fetchSpy)
    await endAdmission('visit-uuid-1')

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(
      '/openmrs/moduleServlet/daherendadmission/daherEndMaternityAdmission/visit-uuid-1/end',
    )
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>)['X-Daher-App-Request']).toBe('1')
    expect(init.body).toBeUndefined()
  })

  it('a definite server failure before commit (400) maps to VALIDATION_ERROR', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(false, 400, { status: 'WRONG_VISIT_TYPE' })))
    await expect(endAdmission('v1')).rejects.toMatchObject({ code: 'VALIDATION_ERROR' })
  })

  it('a definite server failure before commit (404) maps to NOT_FOUND', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(false, 404, { status: 'VISIT_NOT_FOUND' })))
    await expect(endAdmission('v1')).rejects.toMatchObject({ code: 'NOT_FOUND' })
  })

  it('a definite authorization failure (401 or 403) maps to NOT_AUTHORIZED', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(false, 403, { status: 'FORBIDDEN' })))
    await expect(endAdmission('v1')).rejects.toMatchObject({ code: 'NOT_AUTHORIZED' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(false, 401, { status: 'UNAUTHENTICATED' })))
    await expect(endAdmission('v1')).rejects.toMatchObject({ code: 'NOT_AUTHORIZED' })
  })

  it('a 500 maps to SERVER_ERROR, deliberately NOT AMBIGUOUS_WRITE — ADR-030 proves the whole ' +
    'operation is transactional, so a 500 here means nothing committed, unlike core’s own /visit ' +
    'endpoint (createMaternityVisit above, which treats 5xx as ambiguous)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(false, 500, { status: 'OPERATION_FAILED' })))
    await expect(endAdmission('v1')).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })

  it('a timeout/dropped connection after the request was sent is AMBIGUOUS_WRITE — the request may ' +
    'have already reached the server and committed before the response was lost', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(endAdmission('v1')).rejects.toMatchObject({ code: 'AMBIGUOUS_WRITE' })
  })

  it('an unparsable "successful" 200 response body is AMBIGUOUS_WRITE, never trusted as ENDED', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => 'not json' }))
    await expect(endAdmission('v1')).rejects.toMatchObject({ code: 'AMBIGUOUS_WRITE' })
  })

  it('a well-formed 200 body missing the documented status/stopDatetime fields is AMBIGUOUS_WRITE, not silently accepted', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(true, 200, { status: 'SOMETHING_ELSE' })))
    await expect(endAdmission('v1')).rejects.toMatchObject({ code: 'AMBIGUOUS_WRITE' })

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(true, 200, { stopDatetime: '2026-08-28T00:00:00.000Z' })))
    await expect(endAdmission('v1')).rejects.toMatchObject({ code: 'AMBIGUOUS_WRITE' })
  })

  it('a genuine 200 ENDED response resolves with the exact status and stopDatetime, nothing else', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(textResponse(true, 200, { status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })),
    )
    await expect(endAdmission('v1')).resolves.toEqual({ status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
  })

  it('a genuine 200 ALREADY_ENDED response resolves distinctly from ENDED', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        textResponse(true, 200, { status: 'ALREADY_ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' }),
      ),
    )
    await expect(endAdmission('v1')).resolves.toEqual({
      status: 'ALREADY_ENDED',
      stopDatetime: '2026-08-28T00:00:00.000Z',
    })
  })

  it('never leaks a raw backend detail into the thrown error, regardless of status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(textResponse(false, 500, { status: 'OPERATION_FAILED', detail: 'org.openmrs.SomeException' })),
    )
    try {
      await endAdmission('v1')
      expect.unreachable()
    } catch (error) {
      expect((error as ApiError).message).not.toContain('org.openmrs')
    }
  })
})
