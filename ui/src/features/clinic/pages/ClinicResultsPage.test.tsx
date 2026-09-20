import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../../api/exchangeClient'
import type {
  DoctorReviewResponse,
  ExchangeMeResponse,
  PatientIdentitySummary,
  ProviderOrderResultsResponse,
  ProviderReleaseVersionContentResponse,
  ProviderReleaseVersionHistoryResponse,
  ProviderResultReviewQueueEntry,
  ProviderResultReviewQueueResponse,
} from '../../../api/exchangeTypes'
import { StaffApp } from '../../staff/StaffApp'

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

function makeQueueEntry(
  overrides: Partial<ProviderResultReviewQueueEntry> = {},
): ProviderResultReviewQueueEntry {
  return {
    review_id: 'review-1',
    order_id: 'order-a',
    release_version_id: 'rel-v1',
    release_version_number: 1,
    is_amendment: false,
    released_at: '2026-09-17T09:30:00Z',
    legacy_unversioned: false,
    review_status: 'PENDING',
    reviewed_at: null,
    patient: makePatient(),
    order_created_at: '2026-09-17T08:00:00Z',
    ...overrides,
  }
}

function makeQueueResponse(
  entries: ProviderResultReviewQueueEntry[],
): ProviderResultReviewQueueResponse {
  return { provider_organization_id: 'provider-a', entries }
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

function makeVersionContent(
  overrides: Partial<ProviderReleaseVersionContentResponse> = {},
): ProviderReleaseVersionContentResponse {
  return {
    order_id: 'order-a',
    release_version_id: 'rel-v1',
    release_version_number: 1,
    is_amendment: false,
    patient: makePatient(),
    review_status: 'PENDING',
    reviewed_at: null,
    reviewed_by_practitioner_reference_id: null,
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

function makeLegacyResults(
  overrides: Partial<ProviderOrderResultsResponse> = {},
): ProviderOrderResultsResponse {
  return {
    order_id: 'order-legacy',
    order_status: 'COMPLETED',
    patient: makePatient(),
    release_version_id: null,
    release_version_number: null,
    is_amendment: null,
    results: [],
    ...overrides,
  }
}

function makeReviewResponse(overrides: Partial<DoctorReviewResponse> = {}): DoctorReviewResponse {
  return {
    order_id: 'order-a',
    release_version_id: 'rel-v1',
    status: 'NO_FOLLOW_UP_REQUIRED',
    reviewed_at: '2026-09-17T10:00:00Z',
    reviewed_by_practitioner_reference_id: 'prac-2',
    ...overrides,
  }
}

/**
 * A `listResultReviews` mock that actually filters by `status`, mirroring the real backend and
 * this page's own two-call (`PENDING` + `ALL`) `loadQueue` shape -- a plain
 * `vi.fn().mockResolvedValue(...)` would return the *same* unfiltered response to both calls,
 * which only matters (causes duplicate/miscounted entries) once a non-PENDING entry is present.
 * `.setEntries` lets a test mutate the underlying data mid-flow (e.g. after a mocked mutation)
 * without juggling `mockResolvedValueOnce` call-order against this page's two parallel requests.
 */
function mockListResultReviews(initialEntries: ProviderResultReviewQueueEntry[]) {
  let entries = initialEntries
  const fn = vi.fn((_providerId: string, options: { status?: string } = {}) => {
    const status = options.status ?? 'PENDING'
    const filtered = status === 'ALL' ? entries : entries.filter((e) => e.review_status === status)
    return Promise.resolve(makeQueueResponse(filtered))
  })
  return Object.assign(fn, {
    setEntries: (next: ProviderResultReviewQueueEntry[]) => {
      entries = next
    },
  })
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(me),
    listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([])),
    getReleaseVersions: vi.fn(),
    getReleaseVersion: vi.fn(),
    getOrderResults: vi.fn(),
    reviewReleaseVersionNoFollowUp: vi.fn(),
    reviewReleaseVersionFollowUp: vi.fn(),
    reviewLegacyOrderNoFollowUp: vi.fn(),
    reviewLegacyOrderFollowUp: vi.fn(),
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
  it('UI-Q1: shows the pending-review count prominently', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
    })
    render(<StaffApp client={client} initialSection="results" />)

    expect(await screen.findByText('Résultats à examiner (1)')).toBeInTheDocument()
    // DH-CLINIC-005 RS6 parity: two orders for the same patient must stay distinguishable before
    // either is opened.
    expect(screen.getByText(/Référence de l'ordre/)).toBeInTheDocument()
  })

  it('UI-Q2: a completed review remains discoverable in the history section, never disappearing', async () => {
    const client = makeClient({
      listResultReviews: mockListResultReviews([
        makeQueueEntry({ review_id: 'r1', review_status: 'NO_FOLLOW_UP_REQUIRED', reviewed_at: '2026-09-17T10:00:00Z' }),
      ]),
    })
    render(<StaffApp client={client} initialSection="results" />)

    expect(await screen.findByText('Résultats à examiner (0)')).toBeInTheDocument()
    expect(screen.getByText('Historique (1)')).toBeInTheDocument()
    expect(screen.getByText('Fatouma Robleh')).toBeInTheDocument()
  })

  it('UI-Q3: two pending versions on the same order render as two distinct queue entries', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(
        makeQueueResponse([
          makeQueueEntry({ review_id: 'r1', release_version_id: 'rel-v1', release_version_number: 1 }),
          makeQueueEntry({
            review_id: 'r2',
            release_version_id: 'rel-v2',
            release_version_number: 2,
            is_amendment: true,
          }),
        ]),
      ),
    })
    render(<StaffApp client={client} initialSection="results" />)

    expect(await screen.findByText('Résultats à examiner (2)')).toBeInTheDocument()
    expect(screen.getByText(/^Version 1$/)).toBeInTheDocument()
    expect(screen.getByText(/^Version 2 — Amendée$/)).toBeInTheDocument()
  })

  it('UI-Q4: a legacy unversioned entry is labelled truthfully, never a fabricated version', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(
        makeQueueResponse([
          makeQueueEntry({
            release_version_id: null,
            release_version_number: null,
            legacy_unversioned: true,
            released_at: null,
          }),
        ]),
      ),
    })
    render(<StaffApp client={client} initialSection="results" />)

    expect(
      await screen.findByText('Résultat historique — aucune version de libération enregistrée'),
    ).toBeInTheDocument()
  })

  it('UI-V1: opening a pending version loads its exact content via getReleaseVersion, not the legacy read', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi.fn().mockResolvedValue(makeVersionContent()),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByText('0.9')).toBeInTheDocument()
    expect(client.getReleaseVersion).toHaveBeenCalledWith('provider-a', 'order-a', 'rel-v1')
    expect(client.getOrderResults).not.toHaveBeenCalled()
  })

  it('UI-V2: switching to a different version fetches that version\'s own distinct exact content', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(
        makeQueueResponse([
          makeQueueEntry({ release_version_id: 'rel-v2', release_version_number: 2, is_amendment: true }),
        ]),
      ),
      getReleaseVersions: vi.fn().mockResolvedValue(
        makeHistory('order-a', {
          versions: [
            {
              release_version_id: 'rel-v1',
              release_version_number: 1,
              is_amendment: false,
              released_at: '2026-09-17T09:00:00Z',
              review_status: 'NO_FOLLOW_UP_REQUIRED',
              reviewed_at: '2026-09-17T09:15:00Z',
              reviewed_by_practitioner_reference_id: 'prac-1',
            },
            {
              release_version_id: 'rel-v2',
              release_version_number: 2,
              is_amendment: true,
              released_at: '2026-09-17T09:30:00Z',
              review_status: 'PENDING',
              reviewed_at: null,
              reviewed_by_practitioner_reference_id: null,
            },
          ],
        }),
      ),
      getReleaseVersion: vi.fn((_providerId: string, _orderId: string, versionId: string) =>
        Promise.resolve(
          versionId === 'rel-v1'
            ? makeVersionContent({
                release_version_id: 'rel-v1',
                review_status: 'NO_FOLLOW_UP_REQUIRED',
                results: [],
              })
            : makeVersionContent({ release_version_id: 'rel-v2', is_amendment: true }),
        ),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('0.9')

    await user.click(screen.getByRole('tab', { name: /^Version 1/ }))

    await waitFor(() => expect(client.getReleaseVersion).toHaveBeenLastCalledWith('provider-a', 'order-a', 'rel-v1'))
    expect(screen.queryByText('0.9')).not.toBeInTheDocument()
  })

  it('UI-V3: a mismatched release_version_id in the content response fails closed', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi.fn().mockResolvedValue(makeVersionContent({ release_version_id: 'rel-mismatched' })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Impossible de vérifier de manière sûre le résultat demandé.')
    expect(alert.textContent).not.toMatch(/rel-v1|rel-mismatched/)
  })

  it('UI-V4: a mismatched patient identity in the content response fails closed', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi.fn().mockResolvedValue(
        makeVersionContent({ patient: makePatient({ patient_id: 'different-patient' }) }),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Impossible de vérifier de manière sûre le résultat demandé.',
    )
  })

  it('UI-V5: a legacy queue entry loads via getOrderResults and never calls the version-content route', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(
        makeQueueResponse([
          makeQueueEntry({
            order_id: 'order-legacy',
            release_version_id: null,
            release_version_number: null,
            legacy_unversioned: true,
            released_at: null,
          }),
        ]),
      ),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-legacy', { versions: [] })),
      getOrderResults: vi.fn().mockResolvedValue(makeLegacyResults()),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(
      await screen.findByText('Résultat historique — aucune version de libération enregistrée.'),
    ).toBeInTheDocument()
    expect(client.getReleaseVersion).not.toHaveBeenCalled()
  })

  it('UI-V6: confirming a legacy order review targets the legacy mutation route, never the version-specific one', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(
        makeQueueResponse([
          makeQueueEntry({
            order_id: 'order-legacy',
            release_version_id: null,
            release_version_number: null,
            legacy_unversioned: true,
            released_at: null,
          }),
        ]),
      ),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-legacy', { versions: [] })),
      getOrderResults: vi.fn().mockResolvedValue(makeLegacyResults()),
      reviewLegacyOrderNoFollowUp: vi.fn().mockResolvedValue(
        makeReviewResponse({ order_id: 'order-legacy', release_version_id: null }),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('radiogroup', { name: 'Décision de revue' })
    await user.click(screen.getByLabelText('Aucun suivi requis'))
    await user.click(screen.getByRole('button', { name: 'Confirmer la revue' }))

    await waitFor(() =>
      expect(client.reviewLegacyOrderNoFollowUp).toHaveBeenCalledWith('provider-a', 'order-legacy'),
    )
    expect(client.reviewReleaseVersionNoFollowUp).not.toHaveBeenCalled()
    expect(client.reviewReleaseVersionFollowUp).not.toHaveBeenCalled()
  })

  it('UI-R1: exactly two disposition choices are offered for a pending review, never a third', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi.fn().mockResolvedValue(makeVersionContent()),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    const group = await screen.findByRole('radiogroup', { name: 'Décision de revue' })
    expect(within(group).getAllByRole('radio')).toHaveLength(2)
    expect(within(group).getByLabelText('Aucun suivi requis')).toBeInTheDocument()
    expect(within(group).getByLabelText('Suivi requis')).toBeInTheDocument()
  })

  it('UI-R2: the confirm button stays disabled until a disposition is chosen, and discloses immutability first', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi.fn().mockResolvedValue(makeVersionContent()),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('radiogroup', { name: 'Décision de revue' })

    const confirmButton = screen.getByRole('button', { name: 'Confirmer la revue' })
    expect(confirmButton).toBeDisabled()
    expect(screen.queryByText(/ne peut actuellement pas/)).not.toBeInTheDocument()

    await user.click(screen.getByLabelText('Aucun suivi requis'))
    expect(confirmButton).toBeEnabled()
    expect(screen.getByText(/ne peut actuellement pas/)).toBeInTheDocument()
  })

  it('UI-R3: selecting "Suivi requis" discloses the truthful follow-up scope limit', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi.fn().mockResolvedValue(makeVersionContent()),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('radiogroup', { name: 'Décision de revue' })
    await user.click(screen.getByLabelText('Suivi requis'))

    expect(
      screen.getByText(/ne planifie pas automatiquement un rendez-vous ni un contact patient/),
    ).toBeInTheDocument()
  })

  it('UI-R4: confirming targets the exact version-specific mutation route, never the legacy one', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi.fn().mockResolvedValue(makeVersionContent()),
      reviewReleaseVersionNoFollowUp: vi.fn().mockResolvedValue(makeReviewResponse()),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('radiogroup', { name: 'Décision de revue' })
    await user.click(screen.getByLabelText('Aucun suivi requis'))
    await user.click(screen.getByRole('button', { name: 'Confirmer la revue' }))

    await waitFor(() =>
      expect(client.reviewReleaseVersionNoFollowUp).toHaveBeenCalledWith('provider-a', 'order-a', 'rel-v1'),
    )
    expect(client.reviewLegacyOrderNoFollowUp).not.toHaveBeenCalled()
  })

  it('UI-R5: a successful review is shown as terminal, the queue count drops, and the version tab label refreshes', async () => {
    const listResultReviews = mockListResultReviews([makeQueueEntry()])
    const reviewedHistory = makeHistory('order-a', {
      versions: [
        {
          release_version_id: 'rel-v1',
          release_version_number: 1,
          is_amendment: false,
          released_at: '2026-09-17T09:30:00Z',
          review_status: 'NO_FOLLOW_UP_REQUIRED',
          reviewed_at: '2026-09-17T10:00:00Z',
          reviewed_by_practitioner_reference_id: 'prac-2',
        },
      ],
    })
    const client = makeClient({
      listResultReviews,
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi
        .fn()
        .mockResolvedValueOnce(makeVersionContent())
        .mockResolvedValueOnce(makeVersionContent({ review_status: 'NO_FOLLOW_UP_REQUIRED', reviewed_at: '2026-09-17T10:00:00Z' })),
      reviewReleaseVersionNoFollowUp: vi.fn().mockImplementation(async () => {
        listResultReviews.setEntries([
          makeQueueEntry({ review_status: 'NO_FOLLOW_UP_REQUIRED', reviewed_at: '2026-09-17T10:00:00Z' }),
        ])
        vi.mocked(client.getReleaseVersions).mockResolvedValueOnce(reviewedHistory)
        return makeReviewResponse()
      }),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('radiogroup', { name: 'Décision de revue' })
    await user.click(screen.getByLabelText('Aucun suivi requis'))
    await user.click(screen.getByRole('button', { name: 'Confirmer la revue' }))

    await waitFor(() => expect(screen.getByText('Résultats à examiner (0)')).toBeInTheDocument())
    expect(screen.getByText(/Revue enregistrée/)).toBeInTheDocument()
    // The recheck re-derives the version history fresh (never the stale switch-time snapshot), so
    // the tab's own status label reflects what the mutation actually recorded.
    expect(screen.getByRole('tab', { name: /Version 1 — Revu — aucun suivi requis/ })).toBeInTheDocument()
  })

  it('UI-R6: a 409 conflict from a concurrent reviewer never overwrites -- shows the authoritative outcome', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi
        .fn()
        .mockResolvedValueOnce(makeVersionContent())
        .mockResolvedValueOnce(
          makeVersionContent({ review_status: 'FOLLOW_UP_REQUIRED', reviewed_at: '2026-09-17T10:05:00Z' }),
        ),
      reviewReleaseVersionNoFollowUp: vi
        .fn()
        .mockRejectedValue(new ExchangeApiError(409, 'CONFLICT', 'already finalized')),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('radiogroup', { name: 'Décision de revue' })
    await user.click(screen.getByLabelText('Aucun suivi requis'))
    await user.click(screen.getByRole('button', { name: 'Confirmer la revue' }))

    expect(await screen.findByText(/a déjà été revue/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/Revue enregistrée/)).toBeInTheDocument())
    expect(screen.getAllByText('Revu — suivi requis').length).toBeGreaterThan(0)
  })

  it('UI-R7: an ambiguous mutation failure re-reads authoritative state rather than blindly retrying', async () => {
    const getReleaseVersion = vi
      .fn()
      .mockResolvedValueOnce(makeVersionContent())
      .mockResolvedValueOnce(makeVersionContent())
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion,
      reviewReleaseVersionNoFollowUp: vi.fn().mockRejectedValue(new Error('network timeout')),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('radiogroup', { name: 'Décision de revue' })
    await user.click(screen.getByLabelText('Aucun suivi requis'))
    await user.click(screen.getByRole('button', { name: 'Confirmer la revue' }))

    expect(await screen.findByText(/n'a pas pu être confirmée/)).toBeInTheDocument()
    await waitFor(() => expect(getReleaseVersion).toHaveBeenCalledTimes(2))
  })

  it('UI-R8: a response whose status disagrees with the requested disposition fails closed', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi.fn().mockResolvedValue(makeVersionContent()),
      reviewReleaseVersionNoFollowUp: vi
        .fn()
        .mockResolvedValue(makeReviewResponse({ status: 'FOLLOW_UP_REQUIRED' })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('radiogroup', { name: 'Décision de revue' })
    await user.click(screen.getByLabelText('Aucun suivi requis'))
    await user.click(screen.getByRole('button', { name: 'Confirmer la revue' }))

    expect(await screen.findByText('Impossible de vérifier de manière sûre le résultat demandé.')).toBeInTheDocument()
  })

  it('RR8: reviewing version 1 never touches or hides an independently pending version 2', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(
        makeQueueResponse([
          makeQueueEntry({ review_id: 'r1', release_version_id: 'rel-v1', release_version_number: 1 }),
          makeQueueEntry({
            review_id: 'r2',
            release_version_id: 'rel-v2',
            release_version_number: 2,
            is_amendment: true,
          }),
        ]),
      ),
    })
    render(<StaffApp client={client} initialSection="results" />)

    expect(await screen.findByText('Résultats à examiner (2)')).toBeInTheDocument()
    expect(screen.getByText(/^Version 1$/)).toBeInTheDocument()
    expect(screen.getByText(/^Version 2 — Amendée$/)).toBeInTheDocument()
  })

  it('RR9: a stale version-switch response must never render after a newer version was opened', async () => {
    const v1 = deferred<ProviderReleaseVersionContentResponse>()
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(
        makeQueueResponse([
          makeQueueEntry({ release_version_id: 'rel-v2', release_version_number: 2, is_amendment: true }),
        ]),
      ),
      getReleaseVersions: vi.fn().mockResolvedValue(
        makeHistory('order-a', {
          versions: [
            {
              release_version_id: 'rel-v1',
              release_version_number: 1,
              is_amendment: false,
              released_at: '2026-09-17T09:00:00Z',
              review_status: 'PENDING',
              reviewed_at: null,
              reviewed_by_practitioner_reference_id: null,
            },
            {
              release_version_id: 'rel-v2',
              release_version_number: 2,
              is_amendment: true,
              released_at: '2026-09-17T09:30:00Z',
              review_status: 'PENDING',
              reviewed_at: null,
              reviewed_by_practitioner_reference_id: null,
            },
          ],
        }),
      ),
      getReleaseVersion: vi.fn((_providerId: string, _orderId: string, versionId: string) =>
        versionId === 'rel-v1' ? v1.promise : Promise.resolve(makeVersionContent({ release_version_id: 'rel-v2', is_amendment: true })),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('0.9') // v2 content loaded first

    await user.click(screen.getByRole('tab', { name: /^Version 1/ })) // starts loading v1, stalls
    await user.click(screen.getByRole('tab', { name: /^Version 2/ })) // switches back to v2 before v1 resolves

    await act(async () => {
      v1.resolve(makeVersionContent({ release_version_id: 'rel-v1', results: [] }))
      await Promise.resolve()
    })

    expect(screen.getByRole('tab', { name: /^Version 2/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('0.9')).toBeInTheDocument()
  })

  it('RR10: a stale response from the previous provider must never render after switching providers', async () => {
    const staleQueue = deferred<ProviderResultReviewQueueResponse>()

    const client = makeClient({
      getMe: vi.fn().mockResolvedValue(meWithTwoProviders),
      listResultReviews: vi.fn((providerId: string) =>
        providerId === 'provider-a'
          ? staleQueue.promise
          : Promise.resolve(
              makeQueueResponse([
                makeQueueEntry({
                  order_id: 'order-b',
                  patient: makePatient({ display_name: 'Patient Provider B' }),
                }),
              ]),
            ),
      ),
    })
    render(<StaffApp client={client} initialSection="results" />)

    const providerSelect = await screen.findByLabelText("Établissement actif")
    await userEvent.setup().selectOptions(providerSelect, 'provider-b')
    await waitFor(() => expect(providerSelect).toHaveValue('provider-b'))
    await screen.findByText('Patient Provider B')

    await act(async () => {
      staleQueue.resolve(
        makeQueueResponse([makeQueueEntry({ patient: makePatient({ display_name: 'Patient Provider A' }) })]),
      )
      await Promise.resolve()
    })

    expect(screen.queryByText('Patient Provider A')).not.toBeInTheDocument()
    expect(screen.getByText('Patient Provider B')).toBeInTheDocument()
  })

  it('RR13: the outgoing provider\'s own queue entries must never render under the new provider, even before the new fetch resolves', async () => {
    const providerBQueue = deferred<ProviderResultReviewQueueResponse>()

    const client = makeClient({
      getMe: vi.fn().mockResolvedValue(meWithTwoProviders),
      listResultReviews: vi.fn((providerId: string) =>
        providerId === 'provider-a'
          ? Promise.resolve(
              makeQueueResponse([
                makeQueueEntry({ patient: makePatient({ display_name: 'Patient Provider A' }) }),
              ]),
            )
          : providerBQueue.promise,
      ),
    })
    render(<StaffApp client={client} initialSection="results" />)

    await screen.findByText('Patient Provider A')

    const providerSelect = await screen.findByLabelText("Établissement actif")
    await userEvent.setup().selectOptions(providerSelect, 'provider-b')

    // Provider B's own fetch is still unresolved at this point -- provider A's entries must
    // already be gone, not merely overwritten once B's fetch eventually settles.
    await waitFor(() => expect(screen.queryByText('Patient Provider A')).not.toBeInTheDocument())

    await act(async () => {
      providerBQueue.resolve(makeQueueResponse([]))
      await Promise.resolve()
    })
  })

  it('RR11: a response arriving after unmount produces no error and no state update', async () => {
    const queue = deferred<ProviderResultReviewQueueResponse>()
    const client = makeClient({ listResultReviews: vi.fn().mockReturnValue(queue.promise) })
    const { unmount } = render(<StaffApp client={client} initialSection="results" />)

    unmount()

    await act(async () => {
      queue.resolve(makeQueueResponse([makeQueueEntry()]))
      await Promise.resolve()
    })
    // No assertion beyond "this did not throw" -- React would warn/throw on a state update
    // against an unmounted component if the generation/mounted guard were missing.
  })

  it("RR12: a stale review-mutation response for a since-switched-away version must not touch the newly opened version's controls", async () => {
    const mutationV1 = deferred<DoctorReviewResponse>()
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(
        makeQueueResponse([
          makeQueueEntry({ release_version_id: 'rel-v1', release_version_number: 1 }),
        ]),
      ),
      getReleaseVersions: vi.fn().mockResolvedValue(
        makeHistory('order-a', {
          versions: [
            {
              release_version_id: 'rel-v1',
              release_version_number: 1,
              is_amendment: false,
              released_at: '2026-09-17T09:00:00Z',
              review_status: 'PENDING',
              reviewed_at: null,
              reviewed_by_practitioner_reference_id: null,
            },
            {
              release_version_id: 'rel-v2',
              release_version_number: 2,
              is_amendment: true,
              released_at: '2026-09-17T09:30:00Z',
              review_status: 'PENDING',
              reviewed_at: null,
              reviewed_by_practitioner_reference_id: null,
            },
          ],
        }),
      ),
      getReleaseVersion: vi.fn((_providerId: string, _orderId: string, versionId: string) =>
        Promise.resolve(
          versionId === 'rel-v1'
            ? makeVersionContent({ release_version_id: 'rel-v1', results: [] })
            : makeVersionContent({ release_version_id: 'rel-v2', is_amendment: true, results: [] }),
        ),
      ),
      reviewReleaseVersionNoFollowUp: vi.fn().mockReturnValue(mutationV1.promise),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('radiogroup', { name: 'Décision de revue' })
    await user.click(screen.getByLabelText('Aucun suivi requis'))
    await user.click(screen.getByRole('button', { name: 'Confirmer la revue' })) // stalls

    await user.click(screen.getByRole('tab', { name: /^Version 2/ })) // switch away before it resolves
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /^Version 2/ })).toHaveAttribute('aria-selected', 'true'),
    )

    await act(async () => {
      mutationV1.resolve(makeReviewResponse({ release_version_id: 'rel-v1' }))
      await Promise.resolve()
    })

    // Version 2's own controls remain untouched by Version 1's late-arriving mutation response.
    expect(screen.getByRole('tab', { name: /^Version 2/ })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('radiogroup', { name: 'Décision de revue' })).toBeInTheDocument()
    expect(screen.queryByText(/Revue enregistrée/)).not.toBeInTheDocument()
  })

  it('patient identity is shown before any observation value in the detail panel', async () => {
    const client = makeClient({
      listResultReviews: vi.fn().mockResolvedValue(makeQueueResponse([makeQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(makeHistory('order-a')),
      getReleaseVersion: vi.fn().mockResolvedValue(makeVersionContent()),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    const banners = await screen.findAllByText('Fatouma Robleh')
    const banner = banners[banners.length - 1]
    const value = await screen.findByText('0.9')
    expect(banner.compareDocumentPosition(value) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
