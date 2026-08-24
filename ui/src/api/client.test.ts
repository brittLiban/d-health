import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createPatient,
  discoverIdentifierSource,
  findPatientByIdentifier,
  generateIdentifier,
  searchPatients,
} from './client'
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
