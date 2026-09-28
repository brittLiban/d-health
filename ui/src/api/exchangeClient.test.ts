import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ExchangeApiError,
  ExchangeClient,
  isAmbiguousExchangeError,
  isDefinitiveExchangeError,
} from './exchangeClient'
import type { ProviderOrderResultsResponse } from './exchangeTypes'

describe('ExchangeClient', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    globalThis.fetch = vi.fn()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('configures default base path to /exchange/v1 and normalizes trailing slashes', () => {
    const clientDefault = new ExchangeClient()
    expect(clientDefault.getBaseUrl()).toBe('/exchange/v1')

    const clientCustom = new ExchangeClient({ baseUrl: 'https://api.dhealth.org/exchange/v1/' })
    expect(clientCustom.getBaseUrl()).toBe('https://api.dhealth.org/exchange/v1')
  })

  it('getMe performs GET /exchange/v1/me with Authorization Bearer header', async () => {
    const mockMe = {
      identity_id: '11111111-1111-1111-1111-111111111111',
      issuer: 'https://id.dhealth.org/realms/dhealth-exchange',
      subject: 'sub-123',
      display_name: 'Dr. Farah',
      email: 'farah@clinic.dj',
      platform_admin: false,
      memberships: [
        {
          organization_id: 'org-1',
          organization_name: 'Clinique de la Paix',
          organization_type: 'PROVIDER',
          organization_status: 'ACTIVE',
          membership_status: 'ACTIVE',
          roles: ['CLINICIAN'],
          capabilities: ['order.manage', 'patient.directory.read', 'patient.register', 'result.review', 'visit.document', 'visit.read'],
        },
      ],
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockMe), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const client = new ExchangeClient({ baseUrl: '/exchange/v1' })
    const result = await client.getMe('test-token-jwt')

    expect(result).toEqual(mockMe)
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/me')
    expect(init?.headers).toMatchObject({
      Accept: 'application/json',
      Authorization: 'Bearer test-token-jwt',
    })
  })

  it('supports an injected getAuthToken callback', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ identity_id: 'id-1', memberships: [] }), { status: 200 }),
    )

    const client = new ExchangeClient({
      getAuthToken: () => 'dynamic-token-456',
    })
    await client.getMe()

    const [, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(init?.headers).toMatchObject({
      Authorization: 'Bearer dynamic-token-456',
    })
  })

  it('listOrders sends GET /organizations/{id}/orders with query limit', async () => {
    const mockOrders = [
      {
        id: 'ord-1',
        provider_organization_id: 'prov-1',
        patient_reference_id: 'pat-1',
        practitioner_reference_id: 'prac-1',
        status: 'AWAITING_LAB_SELECTION',
        created_at: '2026-09-15T06:00:00Z',
        item_count: 2,
      },
    ]

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockOrders), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.listOrders('prov-1', { limit: 25 })

    expect(result).toEqual(mockOrders)
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/orders?limit=25')
  })

  it('getOrder sends GET /organizations/{id}/orders/{order_id}', async () => {
    const mockOrder = {
      id: 'ord-1',
      provider_organization_id: 'prov-1',
      patient_reference_id: 'pat-1',
      practitioner_reference_id: 'prac-1',
      status: 'IN_PROGRESS',
      created_at: '2026-09-15T06:00:00Z',
      items: [
        {
          catalog_entry_id: 'cat-1',
          catalog_code_snapshot: 'CBC',
          catalog_display_name_snapshot: 'Numération Formule Sanguine',
        },
      ],
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockOrder), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.getOrder('prov-1', 'ord-1')

    expect(result).toEqual(mockOrder)
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/orders/ord-1')
  })

  it('getOrderResults sends GET /organizations/{id}/orders/{order_id}/results', async () => {
    const mockResults: ProviderOrderResultsResponse = {
      order_id: 'ord-1',
      order_status: 'COMPLETED',
      patient: {
        patient_id: 'patient-1',
        display_name: 'Patient Synthétique',
        date_of_birth: null,
        contact_channel_type: null,
        contact_value: null,
      },
      release_version_id: 'rel-v1',
      release_version_number: 1,
      is_amendment: false,
      results: [
        {
          item_id: 'item-1',
          catalog_code_snapshot: 'GLU',
          catalog_display_name_snapshot: 'Glycémie',
          entry_completed_at: '2026-09-15T09:00:00Z',
          observations: [
            {
              observation_id: 'obs-1',
              source_code: 'GLU',
              source_system: 'LOINC',
              display_label_snapshot: 'Glycémie',
              result_type: 'NUMERIC',
              numeric_value: '5.2',
              numeric_unit: 'mmol/L',
              reference_range_low: '3.9',
              reference_range_high: '6.1',
              text_value: null,
              recorded_at: '2026-09-15T09:00:00Z',
            },
          ],
        },
      ],
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockResults), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.getOrderResults('prov-1', 'ord-1')

    expect(result).toEqual(mockResults)
    const obs = result.results[0].observations[0]
    expect(typeof obs.numeric_value).toBe('string')
    expect(obs.numeric_value).toBe('5.2')
    expect(typeof obs.reference_range_low).toBe('string')
    expect(typeof obs.reference_range_high).toBe('string')
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/orders/ord-1/results')
  })

  it('getReleaseVersions sends GET /organizations/{id}/orders/{order_id}/release-versions', async () => {
    const mockVersions = {
      order_id: 'ord-1',
      versions: [
        {
          release_version_id: 'rel-v1',
          release_version_number: 1,
          is_amendment: false,
          released_at: '2026-09-15T10:00:00Z',
          review_status: 'PENDING',
          reviewed_at: null,
          reviewed_by_practitioner_reference_id: null,
        },
      ],
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockVersions), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.getReleaseVersions('prov-1', 'ord-1')

    expect(result).toEqual(mockVersions)
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/orders/ord-1/release-versions')
  })

  it('getReleaseVersion sends GET .../release-versions/{release_version_id}', async () => {
    const mockContent = {
      order_id: 'ord-1',
      release_version_id: 'rel-v1',
      release_version_number: 1,
      is_amendment: false,
      patient: {
        patient_id: 'patient-1',
        display_name: 'Patient Synthétique',
        date_of_birth: null,
        contact_channel_type: null,
        contact_value: null,
      },
      review_status: 'PENDING',
      reviewed_at: null,
      reviewed_by_practitioner_reference_id: null,
      results: [],
    }
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockContent), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.getReleaseVersion('prov-1', 'ord-1', 'rel-v1')

    expect(result).toEqual(mockContent)
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/orders/ord-1/release-versions/rel-v1')
  })

  it('listResultReviews sends GET .../result-reviews with status and limit query params', async () => {
    const mockQueue = { provider_organization_id: 'prov-1', entries: [] }
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockQueue), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.listResultReviews('prov-1', { status: 'ALL', limit: 25 })

    expect(result).toEqual(mockQueue)
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/result-reviews?status=ALL&limit=25')
  })

  it('listResultReviews defaults to no query params when none are given', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ provider_organization_id: 'prov-1', entries: [] }), { status: 200 }),
    )

    const client = new ExchangeClient()
    await client.listResultReviews('prov-1')

    const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/result-reviews')
  })

  it('reviewReleaseVersionNoFollowUp/FollowUp POST the version-specific review routes', async () => {
    const mockReview = {
      order_id: 'ord-1',
      release_version_id: 'rel-v1',
      status: 'NO_FOLLOW_UP_REQUIRED',
      reviewed_at: '2026-09-17T10:00:00Z',
      reviewed_by_practitioner_reference_id: 'prac-1',
    }
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockReview), { status: 200 }),
    )
    const client = new ExchangeClient()
    const result = await client.reviewReleaseVersionNoFollowUp('prov-1', 'ord-1', 'rel-v1')

    expect(result).toEqual(mockReview)
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe(
      '/exchange/v1/organizations/prov-1/orders/ord-1/release-versions/rel-v1/review/no-follow-up-required',
    )
    expect(init?.method).toBe('POST')

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ ...mockReview, status: 'FOLLOW_UP_REQUIRED' }), { status: 200 }),
    )
    await client.reviewReleaseVersionFollowUp('prov-1', 'ord-1', 'rel-v1')
    const [url2] = vi.mocked(globalThis.fetch).mock.calls[1]
    expect(url2).toBe(
      '/exchange/v1/organizations/prov-1/orders/ord-1/release-versions/rel-v1/review/follow-up-required',
    )
  })

  it('reviewLegacyOrderNoFollowUp/FollowUp POST the legacy order-only review routes', async () => {
    const mockReview = {
      order_id: 'ord-legacy',
      release_version_id: null,
      status: 'NO_FOLLOW_UP_REQUIRED',
      reviewed_at: '2026-09-17T10:00:00Z',
      reviewed_by_practitioner_reference_id: 'prac-1',
    }
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockReview), { status: 200 }),
    )
    const client = new ExchangeClient()
    await client.reviewLegacyOrderNoFollowUp('prov-1', 'ord-legacy')
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/orders/ord-legacy/review/no-follow-up-required')

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ ...mockReview, status: 'FOLLOW_UP_REQUIRED' }), { status: 200 }),
    )
    await client.reviewLegacyOrderFollowUp('prov-1', 'ord-legacy')
    const [url2] = vi.mocked(globalThis.fetch).mock.calls[1]
    expect(url2).toBe('/exchange/v1/organizations/prov-1/orders/ord-legacy/review/follow-up-required')
  })

  it('bindPatientReference sends POST /organizations/{id}/patient-references', async () => {
    const mockRef = {
      id: 'pat-ref-1',
      source_organization_id: 'prov-1',
      source_system: 'CLINIC_LOCAL',
      source_identity_system: 'LOCAL_MRN',
      source_identity_value: 'MRN-9021',
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockRef), { status: 201 }),
    )

    const client = new ExchangeClient()
    const result = await client.bindPatientReference('prov-1', {
      source_system: 'CLINIC_LOCAL',
      source_identity_system: 'LOCAL_MRN',
      source_identity_value: 'MRN-9021',
      display_name: 'Patient A',
    })

    expect(result).toEqual(mockRef)
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/patient-references')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({
      source_system: 'CLINIC_LOCAL',
      source_identity_system: 'LOCAL_MRN',
      source_identity_value: 'MRN-9021',
      display_name: 'Patient A',
    })
  })

  it('reports create versus exact-identity replay from the binding status', async () => {
    const mockRef = {
      id: 'pat-ref-1',
      source_organization_id: 'prov-1',
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: 'opaque-1',
    }
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify(mockRef), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(mockRef), { status: 200 }))
    const client = new ExchangeClient()
    const request = {
      source_system: mockRef.source_system,
      source_identity_system: mockRef.source_identity_system,
      source_identity_value: mockRef.source_identity_value,
    }

    await expect(client.bindPatientReferenceWithOutcome('prov-1', request)).resolves.toEqual({
      reference: mockRef,
      created: true,
    })
    await expect(client.bindPatientReferenceWithOutcome('prov-1', request)).resolves.toEqual({
      reference: mockRef,
      created: false,
    })
  })

  it('lists patients with GET and sends patient search text only in a POST body', async () => {
    const patients = [
      {
        patient_reference_id: 'pat-ref-1',
        display_name: 'Patient Synthétique',
        contact_channel_type: null,
        contact_value: null,
        local_reference: 'opaque-1',
      },
    ]
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify(patients), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(patients), { status: 200 }))
    const client = new ExchangeClient()

    await expect(client.listPatientReferences('prov-1')).resolves.toEqual(patients)
    await expect(
      client.listPatientReferences('prov-1', { query: 'Patient Synthétique', limit: 25 }),
    ).resolves.toEqual(patients)

    const [listUrl, listInit] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(listUrl).toBe('/exchange/v1/organizations/prov-1/patient-references?limit=50')
    expect(listInit?.method).toBe('GET')

    const [searchUrl, searchInit] = vi.mocked(globalThis.fetch).mock.calls[1]
    expect(searchUrl).toBe('/exchange/v1/organizations/prov-1/patient-references/search')
    expect(String(searchUrl)).not.toContain('Patient')
    expect(searchInit?.method).toBe('POST')
    expect(JSON.parse(searchInit?.body as string)).toEqual({
      query: 'Patient Synthétique',
      limit: 25,
    })
  })

  it('gets one provider-scoped patient reference by id', async () => {
    const patient = {
      patient_reference_id: 'pat-ref-1',
      display_name: null,
      contact_channel_type: null,
      contact_value: null,
      local_reference: 'opaque-1',
    }
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(patient), { status: 200 }),
    )
    const client = new ExchangeClient()

    await expect(client.getPatientReference('prov-1', 'pat-ref-1')).resolves.toEqual(patient)
    expect(vi.mocked(globalThis.fetch).mock.calls[0][0]).toBe(
      '/exchange/v1/organizations/prov-1/patient-references/pat-ref-1',
    )
  })

  it('createPatientWithOutcome sends POST /organizations/{id}/patients with Idempotency-Key', async () => {
    const entry = {
      patient_id: 'patient-1',
      provider_organization_id: 'prov-1',
      display_name: 'Amina Hassan',
      date_of_birth: null,
      contact_channel_type: null,
      contact_value: null,
      primary_patient_reference_id: 'pat-ref-1',
      created_at: '2026-09-17T00:00:00Z',
    }
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify(entry), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(entry), { status: 200 }))
    const client = new ExchangeClient()
    const body = { display_name: 'Amina Hassan' }

    await expect(
      client.createPatientWithOutcome('prov-1', 'idem-1', body),
    ).resolves.toEqual({ patient: entry, created: true })
    await expect(
      client.createPatientWithOutcome('prov-1', 'idem-1', body),
    ).resolves.toEqual({ patient: entry, created: false })

    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/patients')
    expect(init?.method).toBe('POST')
    expect((init as RequestInit).headers as Record<string, string>).toMatchObject({
      'Idempotency-Key': 'idem-1',
    })
    expect(JSON.parse(init?.body as string)).toEqual(body)
  })

  it('getPatientRegistration returns null on 404 and the entry otherwise', async () => {
    const entry = {
      patient_id: 'patient-1',
      provider_organization_id: 'prov-1',
      display_name: 'Amina Hassan',
      date_of_birth: null,
      contact_channel_type: null,
      contact_value: null,
      primary_patient_reference_id: 'pat-ref-1',
      created_at: '2026-09-17T00:00:00Z',
    }
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify(entry), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'patient registration not found' }), {
          status: 404,
        }),
      )
    const client = new ExchangeClient()

    await expect(client.getPatientRegistration('prov-1', 'idem-1')).resolves.toEqual(entry)
    await expect(client.getPatientRegistration('prov-1', 'idem-2')).resolves.toBeNull()

    expect(vi.mocked(globalThis.fetch).mock.calls[0][0]).toBe(
      '/exchange/v1/organizations/prov-1/patient-registrations/idem-1',
    )
  })

  it('lists and searches the native Patient directory with GET/POST respectively', async () => {
    const entries = [
      {
        patient_id: 'patient-1',
        provider_organization_id: 'prov-1',
        display_name: 'Patient Synthétique',
        date_of_birth: null,
        contact_channel_type: null,
        contact_value: null,
        primary_patient_reference_id: 'pat-ref-1',
        created_at: '2026-09-17T00:00:00Z',
      },
    ]
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify(entries), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(entries), { status: 200 }))
    const client = new ExchangeClient()

    await expect(client.listPatients('prov-1')).resolves.toEqual(entries)
    await expect(client.searchPatients('prov-1', 'Synthétique', { limit: 25 })).resolves.toEqual(
      entries,
    )

    const [listUrl, listInit] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(listUrl).toBe('/exchange/v1/organizations/prov-1/patients?limit=50')
    expect(listInit?.method).toBe('GET')

    const [searchUrl, searchInit] = vi.mocked(globalThis.fetch).mock.calls[1]
    expect(searchUrl).toBe('/exchange/v1/organizations/prov-1/patients/search')
    expect(String(searchUrl)).not.toContain('Synth')
    expect(searchInit?.method).toBe('POST')
    expect(JSON.parse(searchInit?.body as string)).toEqual({
      query: 'Synthétique',
      limit: 25,
    })
  })

  it('gets one provider-scoped Patient by id', async () => {
    const entry = {
      patient_id: 'patient-1',
      provider_organization_id: 'prov-1',
      display_name: null,
      date_of_birth: null,
      contact_channel_type: null,
      contact_value: null,
      primary_patient_reference_id: 'pat-ref-1',
      created_at: '2026-09-17T00:00:00Z',
    }
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(entry), { status: 200 }),
    )
    const client = new ExchangeClient()

    await expect(client.getPatient('prov-1', 'patient-1')).resolves.toEqual(entry)
    expect(vi.mocked(globalThis.fetch).mock.calls[0][0]).toBe(
      '/exchange/v1/organizations/prov-1/patients/patient-1',
    )
  })

  it('normalizes 401 Unauthorized with isAuthError = true', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'invalid or missing credentials' }), {
        status: 401,
        statusText: 'Unauthorized',
      }),
    )

    const client = new ExchangeClient()
    await expect(client.getMe()).rejects.toMatchObject({
      status: 401,
      code: 'UNAUTHORIZED',
      detail: 'invalid or missing credentials',
      isAuthError: true,
      isNetworkError: false,
    })
  })

  it('normalizes 403 Forbidden with isAuthError = true', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'clinician role required' }), {
        status: 403,
        statusText: 'Forbidden',
      }),
    )

    const client = new ExchangeClient()
    await expect(client.listOrders('prov-1')).rejects.toMatchObject({
      status: 403,
      code: 'FORBIDDEN',
      detail: 'clinician role required',
      isAuthError: true,
      isNetworkError: false,
    })
  })

  it('normalizes 404 Not Found', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'order not found' }), {
        status: 404,
        statusText: 'Not Found',
      }),
    )

    const client = new ExchangeClient()
    await expect(client.getOrder('prov-1', 'nonexistent')).rejects.toMatchObject({
      status: 404,
      code: 'NOT_FOUND',
      detail: 'order not found',
      isAuthError: false,
    })
  })

  it('normalizes 409 Conflict', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'order results are not yet available' }), {
        status: 409,
        statusText: 'Conflict',
      }),
    )

    const client = new ExchangeClient()
    await expect(client.getOrderResults('prov-1', 'ord-in-progress')).rejects.toMatchObject({
      status: 409,
      code: 'CONFLICT',
      detail: 'order results are not yet available',
    })
  })

  it('normalizes 422 Validation Error with Pydantic array detail', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: [
            { loc: ['body', 'source_system'], msg: 'Field required', type: 'missing' },
            { loc: ['body', 'source_identity_value'], msg: 'Field required', type: 'missing' },
          ],
        }),
        { status: 422, statusText: 'Unprocessable Entity' },
      ),
    )

    const client = new ExchangeClient()
    await expect(
      client.bindPatientReference('prov-1', {
        source_system: '',
        source_identity_system: '',
        source_identity_value: '',
      }),
    ).rejects.toMatchObject({
      status: 422,
      code: 'VALIDATION_ERROR',
      detail: 'Field required; Field required',
    })
  })

  it('normalizes network failures with isNetworkError = true', async () => {
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new TypeError('Failed to fetch'))

    const client = new ExchangeClient()
    await expect(client.getMe()).rejects.toMatchObject({
      status: 0,
      code: 'NETWORK_ERROR',
      detail: 'Failed to fetch',
      isNetworkError: true,
      isAuthError: false,
    })
  })

  it('never calls /openmrs/*', async () => {
    vi.mocked(globalThis.fetch).mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    )

    const client = new ExchangeClient()
    await client.getMe()
    await client.listOrders('prov-1')
    await client.getOrder('prov-1', 'ord-1')
    await client.listPatientReferences('prov-1', { query: 'synthetic' })
    await client.getPatientReference('prov-1', 'pat-ref-1')

    for (const call of vi.mocked(globalThis.fetch).mock.calls) {
      const url = String(call[0])
      expect(url).not.toContain('/openmrs')
      expect(url).toContain('/exchange/v1')
    }
  })

  it('maps HTTP 400 to VALIDATION_ERROR', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Requête invalide' }), { status: 400 }),
    )

    const client = new ExchangeClient()
    await expect(client.getMe()).rejects.toMatchObject({
      status: 400,
      code: 'VALIDATION_ERROR',
      detail: 'Requête invalide',
    })
  })

  it('correctly classifies definitive vs ambiguous exchange errors', () => {
    const err400 = new ExchangeApiError(400, 'VALIDATION_ERROR', 'Bad Request')
    const err401 = new ExchangeApiError(401, 'UNAUTHORIZED', 'Unauthorized')
    const err403 = new ExchangeApiError(403, 'FORBIDDEN', 'Forbidden')
    const err404 = new ExchangeApiError(404, 'NOT_FOUND', 'Not Found')
    const err409 = new ExchangeApiError(409, 'CONFLICT', 'Conflict')
    const err422 = new ExchangeApiError(422, 'VALIDATION_ERROR', 'Validation Error')

    expect(isDefinitiveExchangeError(err400)).toBe(true)
    expect(isDefinitiveExchangeError(err401)).toBe(true)
    expect(isDefinitiveExchangeError(err403)).toBe(true)
    expect(isDefinitiveExchangeError(err404)).toBe(true)
    expect(isDefinitiveExchangeError(err409)).toBe(true)
    expect(isDefinitiveExchangeError(err422)).toBe(true)

    const errNetwork = new ExchangeApiError(0, 'NETWORK_ERROR', 'Network failure')
    const err500 = new ExchangeApiError(500, 'SERVER_ERROR', 'Internal Server Error')
    const err503 = new ExchangeApiError(503, 'SERVER_ERROR', 'Service Unavailable')
    const errTypeError = new TypeError('Failed to fetch')

    expect(isDefinitiveExchangeError(errNetwork)).toBe(false)
    expect(isDefinitiveExchangeError(err500)).toBe(false)
    expect(isDefinitiveExchangeError(errTypeError)).toBe(false)

    expect(isAmbiguousExchangeError(errNetwork)).toBe(true)
    expect(isAmbiguousExchangeError(err500)).toBe(true)
    expect(isAmbiguousExchangeError(err503)).toBe(true)
    expect(isAmbiguousExchangeError(errTypeError)).toBe(true)
    expect(isAmbiguousExchangeError(err422)).toBe(false)
    expect(isAmbiguousExchangeError(err401)).toBe(false)
  })

  it('listCatalogTests performs GET /exchange/v1/catalog/tests without openmrs calls', async () => {
    const mockCatalog = [
      {
        id: 'cat-1',
        code: 'SYNTH-CBC',
        display_name: 'Numération Formule Sanguine (NFS)',
        version: 1,
        active: true,
        is_synthetic: true,
      },
    ]

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockCatalog), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.listCatalogTests({ token: 'tok-1' })

    expect(result).toEqual(mockCatalog)
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/catalog/tests')
    expect(url).not.toContain('/openmrs')
    expect(init?.headers).toMatchObject({
      Authorization: 'Bearer tok-1',
    })
  })

  it('createOrderWithOutcome performs POST /organizations/{id}/orders with Idempotency-Key and distinguishes created 201 vs 200', async () => {
    const mockOrder = {
      id: 'ord-123',
      provider_organization_id: 'prov-1',
      patient_reference_id: 'pat-ref-1',
      practitioner_reference_id: 'prac-1',
      status: 'AWAITING_LAB_SELECTION',
      created_at: '2026-09-16T08:00:00Z',
      items: [
        {
          catalog_entry_id: 'cat-1',
          catalog_code_snapshot: 'SYNTH-CBC',
          catalog_display_name_snapshot: 'NFS',
        },
      ],
    }

    // 201 Created
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockOrder), { status: 201 }),
    )

    const client = new ExchangeClient()
    const outcome1 = await client.createOrderWithOutcome(
      'prov-1',
      'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
      {
        patient_reference_id: 'pat-ref-1',
        catalog_entry_ids: ['cat-1'],
      },
      { token: 'tok-1' },
    )

    expect(outcome1.created).toBe(true)
    expect(outcome1.order).toEqual(mockOrder)

    const [url1, init1] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url1).toBe('/exchange/v1/organizations/prov-1/orders')
    expect(url1).not.toContain('/openmrs')
    expect(init1?.headers).toMatchObject({
      'Content-Type': 'application/json',
      'Idempotency-Key': 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
      Authorization: 'Bearer tok-1',
    })
    expect(JSON.parse(String(init1?.body))).toEqual({
      patient_reference_id: 'pat-ref-1',
      catalog_entry_ids: ['cat-1'],
    })

    // 200 Replay
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockOrder), { status: 200 }),
    )

    const outcome2 = await client.createOrderWithOutcome(
      'prov-1',
      'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
      {
        patient_reference_id: 'pat-ref-1',
        catalog_entry_ids: ['cat-1'],
      },
      { token: 'tok-1' },
    )
    expect(outcome2.created).toBe(false)
  })

  it('listPatientOrders sends GET /organizations/{id}/patient-references/{id}/orders with query limit', async () => {
    const mockOrders = [
      {
        id: 'ord-1',
        provider_organization_id: 'prov-1',
        patient_reference_id: 'pat-ref-1',
        practitioner_reference_id: 'prac-1',
        status: 'AWAITING_LAB_SELECTION',
        created_at: '2026-09-16T08:00:00Z',
        items: [],
      },
    ]

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockOrders), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.listPatientOrders('prov-1', 'pat-ref-1', {
      limit: 25,
      token: 'tok-2',
    })

    expect(result).toEqual(mockOrders)
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe(
      '/exchange/v1/organizations/prov-1/patient-references/pat-ref-1/orders?limit=25',
    )
    expect(url).not.toContain('/openmrs')
    expect(init?.headers).toMatchObject({
      Authorization: 'Bearer tok-2',
    })
  })

  it('getOrderByIdempotencyKey sends GET /organizations/{id}/orders/by-idempotency-key/{key}', async () => {
    const mockOrder = {
      id: 'ord-1',
      provider_organization_id: 'prov-1',
      patient_reference_id: 'pat-ref-1',
      practitioner_reference_id: 'prac-1',
      status: 'AWAITING_LAB_SELECTION',
      created_at: '2026-09-16T08:00:00Z',
      items: [],
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockOrder), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.getOrderByIdempotencyKey('prov-1', 'key-123', {
      token: 'tok-3',
    })

    expect(result).toEqual(mockOrder)
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/orders/by-idempotency-key/key-123')
    expect(url).not.toContain('/openmrs')
    expect(init?.headers).toMatchObject({
      Authorization: 'Bearer tok-3',
    })
  })

  it('getOrderByIdempotencyKey returns null on 404', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'order not found' }), {
        status: 404,
        statusText: 'Not Found',
      }),
    )

    const client = new ExchangeClient()
    const result = await client.getOrderByIdempotencyKey('prov-1', 'missing-key')
    expect(result).toBeNull()
  })

  it('getOrderByIdempotencyKey throws on non-404 error', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'server error' }), {
        status: 500,
        statusText: 'Server Error',
      }),
    )

    const client = new ExchangeClient()
    await expect(client.getOrderByIdempotencyKey('prov-1', 'err-key')).rejects.toThrow()
  })

  it('createOrderIntentWithOutcome sends POST /exchange/v1/organizations/{provider_id}/order-intents with Idempotency-Key', async () => {
    const mockIntent = {
      id: 'intent-123',
      idempotency_key: 'key-abc',
      provider_organization_id: 'prov-1',
      patient_reference_id: 'pat-1',
      practitioner_reference_id: 'prac-1',
      committed_lab_order_id: null,
      created_at: '2026-09-16T08:00:00Z',
      items: [
        {
          id: 'item-1',
          catalog_entry_id: 'cat-1',
          catalog_code_snapshot: 'SYNTH-CBC',
          catalog_display_name_snapshot: 'Numération Formule Sanguine',
        },
      ],
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockIntent), { status: 201 }),
    )

    const client = new ExchangeClient()
    const outcome = await client.createOrderIntentWithOutcome(
      'prov-1',
      'key-abc',
      {
        patient_reference_id: 'pat-1',
        catalog_entry_ids: ['cat-1'],
      },
      { token: 'tok-intent' },
    )

    expect(outcome.created).toBe(true)
    expect(outcome.intent).toEqual(mockIntent)
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/order-intents')
    expect(init?.headers).toMatchObject({
      Authorization: 'Bearer tok-intent',
      'Idempotency-Key': 'key-abc',
      'Content-Type': 'application/json',
    })
  })

  it('getOrderIntentByIdempotencyKey retrieves intent by key and returns null on 404', async () => {
    const mockIntent = {
      id: 'intent-456',
      idempotency_key: 'key-xyz',
      provider_organization_id: 'prov-1',
      patient_reference_id: 'pat-1',
      practitioner_reference_id: 'prac-1',
      committed_lab_order_id: null,
      created_at: '2026-09-16T08:00:00Z',
      items: [],
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockIntent), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.getOrderIntentByIdempotencyKey('prov-1', 'key-xyz')
    expect(result).toEqual(mockIntent)

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'not found' }), { status: 404 }),
    )
    const notFound = await client.getOrderIntentByIdempotencyKey('prov-1', 'missing-key')
    expect(notFound).toBeNull()
  })

  it('getOrderIntent retrieves intent by ID and returns null on 404', async () => {
    const mockIntent = {
      id: 'intent-789',
      idempotency_key: 'key-789',
      provider_organization_id: 'prov-1',
      patient_reference_id: 'pat-1',
      practitioner_reference_id: 'prac-1',
      committed_lab_order_id: null,
      created_at: '2026-09-16T08:00:00Z',
      items: [],
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockIntent), { status: 200 }),
    )

    const client = new ExchangeClient()
    const result = await client.getOrderIntent('prov-1', 'intent-789')
    expect(result).toEqual(mockIntent)

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'not found' }), { status: 404 }),
    )
    const notFound = await client.getOrderIntent('prov-1', 'missing-id')
    expect(notFound).toBeNull()
  })

  it('commitOrderIntentWithOutcome sends POST /exchange/v1/organizations/{provider_id}/order-intents/{intent_id}/commit', async () => {
    const mockOrder = {
      id: 'order-commit-1',
      provider_organization_id: 'prov-1',
      patient_reference_id: 'pat-1',
      practitioner_reference_id: 'prac-1',
      status: 'AWAITING_LAB_SELECTION',
      created_at: '2026-09-16T08:00:00Z',
      items: [],
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockOrder), { status: 201 }),
    )

    const client = new ExchangeClient()
    const outcome = await client.commitOrderIntentWithOutcome('prov-1', 'intent-123', {
      token: 'tok-commit',
    })

    expect(outcome.created).toBe(true)
    expect(outcome.order).toEqual(mockOrder)
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/prov-1/order-intents/intent-123/commit')
    expect(init?.headers).toMatchObject({
      Authorization: 'Bearer tok-commit',
      'Content-Type': 'application/json',
    })
  })

  // DH-DEMO-001 D16: proves the REAL ExchangeClient -- not a test double implementing its
  // interface, as every LabOrdersPage.test.tsx case above does -- correctly builds the request
  // and parses the response at the actual `fetch` network boundary, using a response shaped
  // exactly like what `adapter/src/daher_adapter/exchange/demo_seed.py`'s `seed_baseline` creates
  // (PROVIDER_ORG_NAME/LAB_ORG_NAME/CATALOG_DISPLAY_NAME there, mirrored as literal strings here).
  // A mismatch between those two files -- the seed script's own naming drifting from what a
  // native UI screen actually renders -- would fail this test without ever booting a container.
  it('DH-DEMO-001 D16: listLabOrders renders demo-seed-shaped data through the real fetch boundary', async () => {
    const demoSeedOrder = {
      order_id: 'demo-order-1',
      status: 'SELECTED',
      selected_at: '2026-09-18T08:00:00Z',
      updated_at: '2026-09-18T08:00:00Z',
      patient_display_name: 'Amina Warsame',
      patient: {
        patient_id: '22222222-2222-4222-8222-222222222222',
        display_name: 'Amina Warsame',
        date_of_birth: '1990-01-01',
      },
      ordering_provider_name: 'Clinique Démo DHealth',
      requested_tests: [
        {
          catalog_code_snapshot: 'DEMO-A',
          catalog_display_name_snapshot: 'Analyse de démonstration A',
          selected_price_amount: null,
          selected_price_currency: null,
        },
      ],
      collection_recorded_at: null,
    }

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify([demoSeedOrder]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const client = new ExchangeClient()
    const orders = await client.listLabOrders('lab-demo', { token: 'demo-token' })

    expect(orders).toHaveLength(1)
    expect(orders[0].ordering_provider_name).toBe('Clinique Démo DHealth')
    expect(orders[0].requested_tests[0].catalog_display_name_snapshot).toBe(
      'Analyse de démonstration A',
    )
    expect(orders[0].patient.display_name).toBe('Amina Warsame')

    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/exchange/v1/organizations/lab-demo/lab-orders')
    expect(init?.headers).toMatchObject({ Authorization: 'Bearer demo-token' })
  })

  // -- DH-PATIENT-001: provider-side patient-access surface -------------------------------------

  it("getPatientAccessMetadata sends GET .../orders/{id}/patient-access and returns metadata only", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          active: true,
          expires_at: "2026-09-27T14:00:00Z",
          access_mode: "ORDER_READ",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )

    const client = new ExchangeClient()
    const metadata = await client.getPatientAccessMetadata("prov-1", "ord-1", { token: "tok-1" })

    expect(metadata).toEqual({
      active: true,
      expires_at: "2026-09-27T14:00:00Z",
      access_mode: "ORDER_READ",
    })
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe("/exchange/v1/organizations/prov-1/orders/ord-1/patient-access")
    expect(init?.method).toBe("GET")
    expect(init?.headers).toMatchObject({ Authorization: "Bearer tok-1" })
  })

  it("issuePatientAccess POSTs to the same path and returns the one-time raw token", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          access_token: "raw-token-value",
          expires_at: "2026-09-27T14:00:00Z",
          access_mode: "ORDER_READ",
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    )

    const client = new ExchangeClient()
    const issued = await client.issuePatientAccess("prov-1", "ord-1", { token: "tok-1" })

    expect(issued.access_token).toBe("raw-token-value")
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe("/exchange/v1/organizations/prov-1/orders/ord-1/patient-access")
    expect(init?.method).toBe("POST")
    // The credential must never be carried in the request URL, only in the response body.
    expect(String(url)).not.toContain("raw-token-value")
  })

  it("revokePatientAccess resolves on a real 204 with an empty body", async () => {
    // The bug this guards: `requestWithResponse` calls `response.json()` on every ok response, so
    // a genuinely empty 204 threw a raw SyntaxError that `isAmbiguousExchangeError` classified as
    // ambiguous -- making every *successful* revoke look like a lost response to the caller. This
    // asserts against a real empty-bodied 204 Response, not a mock that returns {}.
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(null, { status: 204 }))

    const client = new ExchangeClient()
    await expect(
      client.revokePatientAccess("prov-1", "ord-1", { token: "tok-1" }),
    ).resolves.toBeUndefined()

    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe("/exchange/v1/organizations/prov-1/orders/ord-1/patient-access")
    expect(init?.method).toBe("DELETE")
  })

  it("revokePatientAccess maps an error status to a typed ExchangeApiError, not a parse error", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "order not found" }), { status: 404 }),
    )

    const client = new ExchangeClient()
    const error = await client.revokePatientAccess("prov-1", "ord-1").catch((caught) => caught)

    expect(error).toBeInstanceOf(ExchangeApiError)
    expect((error as ExchangeApiError).code).toBe("NOT_FOUND")
    expect((error as ExchangeApiError).detail).toBe("order not found")
    expect(isDefinitiveExchangeError(error)).toBe(true)
  })

  it("a network failure during revoke is ambiguous, which is what drives the metadata re-read", async () => {
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new TypeError("Failed to fetch"))

    const client = new ExchangeClient()
    const error = await client.revokePatientAccess("prov-1", "ord-1").catch((caught) => caught)

    expect(error).toBeInstanceOf(ExchangeApiError)
    expect((error as ExchangeApiError).code).toBe("NETWORK_ERROR")
    expect(isAmbiguousExchangeError(error)).toBe(true)
  })
})
