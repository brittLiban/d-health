import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../../api/exchangeClient'
import type {
  ExchangeMeResponse,
  LabOrderSummary,
  PatientIdentitySummary,
  ProviderOrderResultsResponse,
  ProviderReleaseVersionHistoryResponse,
} from '../../../api/exchangeTypes'
import { ClinicApp } from '../ClinicApp'

const me: ExchangeMeResponse = {
  identity_id: 'identity-1',
  issuer: 'issuer',
  subject: 'subject',
  display_name: 'Clinicien Synthétique',
  email: null,
  platform_admin: false,
  memberships: [
    {
      organization_id: 'provider-a',
      organization_name: 'Clinique A',
      organization_type: 'PROVIDER',
      organization_status: 'ACTIVE',
      membership_status: 'ACTIVE',
      roles: ['CLINICIAN'],
    },
  ],
}

const meWithTwoProviders: ExchangeMeResponse = {
  ...me,
  memberships: [
    me.memberships[0],
    { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
  ],
}

function makePatient(overrides: Partial<PatientIdentitySummary> = {}): PatientIdentitySummary {
  return {
    patient_id: '11111111-1111-4111-8111-111111111111',
    display_name: 'Fatouma Robleh',
    date_of_birth: '1988-06-02',
    contact_channel_type: 'PHONE',
    contact_value: '+25377100001',
    ...overrides,
  }
}

function makeOrder(overrides: Partial<LabOrderSummary> = {}): LabOrderSummary {
  return {
    id: 'order-a',
    provider_organization_id: 'provider-a',
    patient_reference_id: 'ref-a',
    practitioner_reference_id: 'prac-1',
    patient: makePatient(),
    status: 'COMPLETED',
    created_at: '2026-09-17T08:00:00Z',
    item_count: 1,
    lab_assignment: null,
    ...overrides,
  }
}

function makeResults(overrides: Partial<ProviderOrderResultsResponse> = {}): ProviderOrderResultsResponse {
  return {
    order_id: 'order-a',
    order_status: 'COMPLETED',
    patient: makePatient(),
    release_version_id: 'rel-v1',
    release_version_number: 1,
    is_amendment: false,
    results: [
      {
        item_id: 'item-1',
        catalog_code_snapshot: 'GLU',
        catalog_display_name_snapshot: 'Glycémie',
        entry_completed_at: '2026-09-17T09:00:00Z',
        observations: [
          {
            observation_id: 'obs-1',
            source_code: 'GLU',
            source_system: 'dhealth-exchange',
            display_label_snapshot: 'Glycémie',
            result_type: 'NUMERIC',
            numeric_value: '0.9',
            numeric_unit: 'g/L',
            reference_range_low: '0.7',
            reference_range_high: '1.1',
            text_value: null,
            recorded_at: '2026-09-17T09:00:00Z',
          },
        ],
      },
    ],
    ...overrides,
  }
}

function makeHistory(
  orderId: string,
  overrides: Partial<ProviderReleaseVersionHistoryResponse> = {},
): ProviderReleaseVersionHistoryResponse {
  return {
    order_id: orderId,
    versions: [
      {
        release_version_id: 'rel-v1',
        release_version_number: 1,
        is_amendment: false,
        released_at: '2026-09-17T09:30:00Z',
        review_status: 'PENDING',
        reviewed_at: null,
        reviewed_by_practitioner_reference_id: null,
      },
    ],
    ...overrides,
  }
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(me),
    listOrders: vi.fn().mockResolvedValue([]),
    getOrderResults: vi.fn(),
    getReleaseVersions: vi.fn(),
    ...overrides,
  } as unknown as ExchangeClient
}

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('ClinicResultsPage', () => {
  it('RS1/RS2/RS3: list shows patient name and DOB, never a patient-reference fragment as identity', async () => {
    const client = makeClient({ listOrders: vi.fn().mockResolvedValue([makeOrder()]) })
    render(<ClinicApp client={client} initialTab="results" />)

    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(screen.getByText('Né(e) le 2 juin 1988')).toBeInTheDocument()
    expect(screen.queryByText('Réf. Patient')).not.toBeInTheDocument()
    expect(screen.queryByText(/^ref-a/)).not.toBeInTheDocument()
  })

  it('RS6: the order technical reference remains available as a secondary column', async () => {
    const client = makeClient({ listOrders: vi.fn().mockResolvedValue([makeOrder()]) })
    render(<ClinicApp client={client} initialTab="results" />)

    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(screen.getByText("Référence de l'ordre")).toBeInTheDocument()
    expect(screen.getByText('order-a…')).toBeInTheDocument()
  })

  it('RS4/RS5: opening a result shows the patient banner before any observation value', async () => {
    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([makeOrder()]),
      getOrderResults: vi.fn().mockResolvedValue(makeResults()),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="results" />)

    await user.click(await screen.findByRole('button', { name: /^Consulter/ }))

    // Both the list row and the detail banner render the same patient's DOB -- the banner is the
    // one that appears later in DOM order (the detail panel renders below the list).
    const banners = await screen.findAllByText('Né(e) le 2 juin 1988')
    const banner = banners[banners.length - 1]
    const value = await screen.findByText('0.9')
    expect(
      banner.compareDocumentPosition(value) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('RR1: selecting B before A resolves shows only B once both eventually resolve', async () => {
    const a = deferred<ProviderOrderResultsResponse>()
    const b = deferred<ProviderOrderResultsResponse>()
    const historyA = deferred<ProviderReleaseVersionHistoryResponse>()
    const historyB = deferred<ProviderReleaseVersionHistoryResponse>()

    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([
        makeOrder({ id: 'order-a', patient: makePatient({ display_name: 'Patient A' }) }),
        makeOrder({ id: 'order-b', patient: makePatient({ display_name: 'Patient B' }) }),
      ]),
      getOrderResults: vi.fn((_providerId: string, orderId: string) =>
        orderId === 'order-a' ? a.promise : b.promise,
      ),
      getReleaseVersions: vi.fn((_providerId: string, orderId: string) =>
        orderId === 'order-a' ? historyA.promise : historyB.promise,
      ),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="results" />)

    const buttons = await screen.findAllByRole('button', { name: /^Consulter/ })
    await user.click(buttons[0]) // select A
    await user.click(buttons[1]) // select B, before A resolved

    // Resolve B first, then A late -- the classic out-of-order race.
    b.resolve(makeResults({ order_id: 'order-b', patient: makePatient({ display_name: 'Patient B' }) }))
    historyB.resolve(makeHistory('order-b'))
    await screen.findByRole('heading', { name: /order-b/ })

    await act(async () => {
      a.resolve(makeResults({ order_id: 'order-a', patient: makePatient({ display_name: 'Patient A' }) }))
      historyA.resolve(makeHistory('order-a'))
      await Promise.resolve()
    })

    // B's own patient identity is the one shown in the detail banner; A never overwrote it.
    expect(screen.getByRole('heading', { name: /order-b/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /order-a$/ })).not.toBeInTheDocument()
  })

  it('RR2: a late failure from an earlier selection must not overwrite the current success', async () => {
    const a = deferred<ProviderOrderResultsResponse>()
    const historyA = deferred<ProviderReleaseVersionHistoryResponse>()

    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([
        makeOrder({ id: 'order-a' }),
        makeOrder({ id: 'order-b' }),
      ]),
      getOrderResults: vi.fn((_providerId: string, orderId: string) =>
        orderId === 'order-a' ? a.promise : Promise.resolve(makeResults({ order_id: 'order-b' })),
      ),
      getReleaseVersions: vi.fn((_providerId: string, orderId: string) =>
        orderId === 'order-a' ? historyA.promise : Promise.resolve(makeHistory('order-b')),
      ),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="results" />)

    const buttons = await screen.findAllByRole('button', { name: /^Consulter/ })
    await user.click(buttons[0]) // select A (stalls)
    await user.click(buttons[1]) // select B (resolves immediately)

    await screen.findByRole('heading', { name: /order-b/ })

    await act(async () => {
      a.reject(new Error('stale network failure'))
      try {
        await a.promise
      } catch {
        // expected
      }
    })

    // B's successful content must remain on screen -- a stale A failure never replaces it.
    expect(screen.getByRole('heading', { name: /order-b/ })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('RR3: a late success from an earlier selection must not overwrite the current failure', async () => {
    const a = deferred<ProviderOrderResultsResponse>()
    const historyA = deferred<ProviderReleaseVersionHistoryResponse>()

    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([
        makeOrder({ id: 'order-a' }),
        makeOrder({ id: 'order-b' }),
      ]),
      getOrderResults: vi.fn((_providerId: string, orderId: string) =>
        orderId === 'order-a' ? a.promise : Promise.reject(new Error('order-b failed')),
      ),
      getReleaseVersions: vi.fn((_providerId: string, orderId: string) =>
        orderId === 'order-a' ? historyA.promise : Promise.reject(new Error('order-b failed')),
      ),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="results" />)

    const buttons = await screen.findAllByRole('button', { name: /^Consulter/ })
    await user.click(buttons[0]) // select A (stalls)
    await user.click(buttons[1]) // select B (fails immediately)

    await screen.findByRole('alert')

    await act(async () => {
      a.resolve(makeResults({ order_id: 'order-a' }))
      historyA.resolve(makeHistory('order-a'))
      await Promise.resolve()
    })

    // The truthful current error state must survive -- a late A success never replaces it.
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Ordre/ })).not.toBeInTheDocument()
  })

  it('RR4: a stale response from the previous provider must never render after switching providers', async () => {
    const staleResults = deferred<ProviderOrderResultsResponse>()
    const staleHistory = deferred<ProviderReleaseVersionHistoryResponse>()

    const client = makeClient({
      getMe: vi.fn().mockResolvedValue(meWithTwoProviders),
      listOrders: vi.fn((providerId: string) =>
        Promise.resolve([
          makeOrder({
            id: providerId === 'provider-a' ? 'order-a' : 'order-b',
            provider_organization_id: providerId,
            patient: makePatient({
              display_name: providerId === 'provider-a' ? 'Patient Provider A' : 'Patient Provider B',
            }),
          }),
        ]),
      ),
      getOrderResults: vi.fn((providerId: string) =>
        providerId === 'provider-a' ? staleResults.promise : Promise.resolve(makeResults({ order_id: 'order-b' })),
      ),
      getReleaseVersions: vi.fn((providerId: string) =>
        providerId === 'provider-a' ? staleHistory.promise : Promise.resolve(makeHistory('order-b')),
      ),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="results" />)

    await user.click(await screen.findByRole('button', { name: /^Consulter/ }))

    const providerSelect = screen.getByLabelText("Sélectionner l'établissement")
    await user.selectOptions(providerSelect, 'provider-b')
    await waitFor(() => expect(providerSelect).toHaveValue('provider-b'))

    await act(async () => {
      staleResults.resolve(makeResults({ order_id: 'order-a' }))
      staleHistory.resolve(makeHistory('order-a'))
      await Promise.resolve()
    })

    // Provider A's stale response must never render under Provider B's active scope.
    expect(screen.queryByText(/order-a$/)).not.toBeInTheDocument()
  })

  it('RR5: a response whose order_id disagrees with the request fails closed with no leaked identity', async () => {
    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([makeOrder({ id: 'order-a' })]),
      getOrderResults: vi.fn().mockResolvedValue(makeResults({ order_id: 'order-mismatched' })),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="results" />)

    await user.click(await screen.findByRole('button', { name: /^Consulter/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Impossible de vérifier de manière sûre le résultat demandé.')
    expect(alert.textContent).not.toMatch(/order-a|order-mismatched/)
  })

  it('RR6: a release-history response whose order_id disagrees fails closed', async () => {
    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([makeOrder({ id: 'order-a' })]),
      getOrderResults: vi.fn().mockResolvedValue(makeResults({ order_id: 'order-a' })),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-mismatched')),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="results" />)

    await user.click(await screen.findByRole('button', { name: /^Consulter/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Impossible de vérifier de manière sûre le résultat demandé.')
  })

  it('a results response whose patient disagrees with the list row fails closed', async () => {
    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([
        makeOrder({ id: 'order-a', patient: makePatient({ patient_id: 'list-row-patient' }) }),
      ]),
      getOrderResults: vi.fn().mockResolvedValue(
        makeResults({ order_id: 'order-a', patient: makePatient({ patient_id: 'different-patient' }) }),
      ),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="results" />)

    await user.click(await screen.findByRole('button', { name: /^Consulter/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Impossible de vérifier de manière sûre le résultat demandé.',
    )
  })

  it('RR7: a response arriving after unmount produces no error and no state update', async () => {
    const results = deferred<ProviderOrderResultsResponse>()
    const history = deferred<ProviderReleaseVersionHistoryResponse>()
    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([makeOrder()]),
      getOrderResults: vi.fn().mockResolvedValue(results.promise),
      getReleaseVersions: vi.fn().mockResolvedValue(history.promise),
    })
    const user = userEvent.setup()
    const { unmount } = render(<ClinicApp client={client} initialTab="results" />)

    await user.click(await screen.findByRole('button', { name: /^Consulter/ }))
    unmount()

    await act(async () => {
      results.resolve(makeResults())
      history.resolve(makeHistory('order-a'))
      await Promise.resolve()
    })
    // No assertion beyond "this did not throw" -- React would warn/throw on a state update
    // against an unmounted component if the generation/mounted guard were missing.
  })
})
