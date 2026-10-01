/**
 * DHEALTH-LAB-REPORT-001/ADR-072 §8: the two entry points, and the one rule that matters about them.
 *
 * **A report action prints the version the user is looking at.** Not the latest one. The whole value
 * of a version-bound report evaporates if a clinician inspecting Version 1 presses a button and
 * receives Version 2 — the document would then be honest about its own content and wrong about what
 * the user asked for.
 *
 * Both halves of that are asserted here against a stubbed client, so the assertion is about which
 * `release_version_id` the UI sends, not about whether the server would have answered correctly:
 *
 *  - the laboratory's version history, where every version in the list has its own button;
 *  - the clinician's result panel, where one button belongs to whichever version the panel has
 *    actually fetched and rendered.
 *
 * In both cases Version 2 is the current release and Version 1 is the one being opened, so a "resolve
 * the latest" regression cannot pass.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../api/exchangeClient'
import type {
  ExchangeMeResponse,
  LabReleaseVersionHistory,
  ProviderReleaseVersionContentResponse,
  ProviderReleaseVersionHistoryResponse,
  ProviderResultReviewQueueEntry,
  ProviderResultReviewQueueResponse,
  ReleasedReport,
} from '../../api/exchangeTypes'
import { ReleaseVersionHistory } from '../lab/components/ReleaseVersionHistory'
import { StaffApp } from '../staff/StaffApp'

const V1 = 'rel-v1'
const V2 = 'rel-v2'

function makeReport(overrides: Partial<ReleasedReport> = {}): ReleasedReport {
  return {
    order_id: 'order-a',
    release_version_id: V1,
    release_version_number: 1,
    is_amendment: false,
    released_at: '2026-09-17T09:30:00Z',
    // Version 1 is historical: Version 2 is the current release.
    is_current_version: false,
    current_release_version_number: 2,
    laboratory: { organization_id: 'lab-1', name: 'Laboratoire Central' },
    ordering_organization: { organization_id: 'provider-a', name: 'Clinique A' },
    patient: {
      patient_id: 'patient-1',
      display_name: 'Fatouma Robleh',
      date_of_birth: '1988-06-02',
    },
    results: [
      {
        item_id: 'item-1',
        catalog_code_snapshot: 'GLU',
        catalog_display_name_snapshot: 'Glycémie',
        observations: [
          {
            observation_id: 'obs-1',
            display_label_snapshot: 'Glycémie',
            result_type: 'NUMERIC',
            // The value Version 1 actually released. Version 2's is 9.9, and it must not appear.
            numeric_value: '0.9',
            numeric_unit: 'g/L',
            reference_range_low: '0.7',
            reference_range_high: '1.1',
            text_value: null,
          },
        ],
      },
    ],
    ...overrides,
  }
}

// ── Laboratory side ────────────────────────────────────────────────────────────────────────────

const LAB_HISTORY: LabReleaseVersionHistory = {
  order_id: 'order-a',
  versions: [
    {
      release_version_id: V1,
      version_number: 1,
      lab_result_release_id: 'release-1',
      amendment_id: null,
      created_at: '2026-09-17T09:30:00Z',
      is_amendment: false,
    },
    {
      release_version_id: V2,
      version_number: 2,
      lab_result_release_id: null,
      amendment_id: 'amendment-1',
      created_at: '2026-09-18T11:00:00Z',
      is_amendment: true,
    },
  ],
}

describe('laboratory entry point — one report button per version', () => {
  function labClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
    return {
      getLabOrderReleaseVersions: vi.fn().mockResolvedValue(LAB_HISTORY),
      getLabOrderReleaseVersionContent: vi.fn(),
      getLabOrderReleaseVersionReport: vi.fn().mockResolvedValue(makeReport()),
      ...overrides,
    } as unknown as ExchangeClient
  }

  it('asks for Version 1 when Version 1 is the version whose button was pressed', async () => {
    const client = labClient()
    render(<ReleaseVersionHistory client={client} labId="lab-1" orderId="order-a" />)

    await userEvent.click(screen.getByRole('button', { name: "Voir l'historique des versions" }))
    const versions = await screen.findAllByRole('listitem')
    expect(versions).toHaveLength(2)

    // The first list item is Version 1 — and Version 2 is the current release, per the history.
    const versionOneRow = versions[0]
    expect(within(versionOneRow).getByText(/Version 1/)).toBeInTheDocument()
    await userEvent.click(
      within(versionOneRow).getByRole('button', { name: 'Voir le compte rendu' }),
    )

    expect(client.getLabOrderReleaseVersionReport).toHaveBeenCalledTimes(1)
    expect(client.getLabOrderReleaseVersionReport).toHaveBeenCalledWith('lab-1', 'order-a', V1)
    // Never the current version, which is exactly the regression this asserts against.
    expect(client.getLabOrderReleaseVersionReport).not.toHaveBeenCalledWith(
      'lab-1',
      'order-a',
      V2,
    )
  })

  it('opens Version 1’s document, marked historical, with Version 1’s own values', async () => {
    render(<ReleaseVersionHistory client={labClient()} labId="lab-1" orderId="order-a" />)

    await userEvent.click(screen.getByRole('button', { name: "Voir l'historique des versions" }))
    const versions = await screen.findAllByRole('listitem')
    await userEvent.click(
      within(versions[0]).getByRole('button', { name: 'Voir le compte rendu' }),
    )

    // The document, not the history row behind it: the history list also says "Version 1".
    const document_ = await screen.findByText('Compte rendu d’analyses')
    const report = document_.closest('[data-released-report]') as HTMLElement
    expect(within(report).getByText('Version 1')).toBeInTheDocument()
    expect(
      within(report).getByText('Version remplacée — résultat actuel : version 2'),
    ).toBeInTheDocument()
    expect(within(report).getByText('0.9')).toBeInTheDocument()
    expect(within(report).queryByText('9.9')).not.toBeInTheDocument()
  })

  it('asks for Version 2 when Version 2’s own button is pressed', async () => {
    const client = labClient({
      getLabOrderReleaseVersionReport: vi.fn().mockResolvedValue(
        makeReport({
          release_version_id: V2,
          release_version_number: 2,
          is_amendment: true,
          is_current_version: true,
          current_release_version_number: 2,
        }),
      ),
    })
    render(<ReleaseVersionHistory client={client} labId="lab-1" orderId="order-a" />)

    await userEvent.click(screen.getByRole('button', { name: "Voir l'historique des versions" }))
    const versions = await screen.findAllByRole('listitem')
    await userEvent.click(
      within(versions[1]).getByRole('button', { name: 'Voir le compte rendu' }),
    )

    expect(client.getLabOrderReleaseVersionReport).toHaveBeenCalledWith('lab-1', 'order-a', V2)
    expect(await screen.findAllByText('Version 2 — rectificatif')).not.toHaveLength(0)
    expect(screen.getByText('Résultat actuel')).toBeInTheDocument()
  })

  it('shows a report action for no version before any release exists', async () => {
    const client = labClient({
      getLabOrderReleaseVersions: vi
        .fn()
        .mockResolvedValue({ order_id: 'order-a', versions: [] }),
    })
    render(<ReleaseVersionHistory client={client} labId="lab-1" orderId="order-a" />)

    await userEvent.click(screen.getByRole('button', { name: "Voir l'historique des versions" }))
    await waitFor(() => expect(client.getLabOrderReleaseVersions).toHaveBeenCalled())
    // No versions, so no per-version report button — the action does not exist to be pressed.
    expect(screen.queryByRole('button', { name: 'Voir le compte rendu' })).not.toBeInTheDocument()
    expect(client.getLabOrderReleaseVersionReport).not.toHaveBeenCalled()
  })

  it('reports a failed report load without claiming the version is gone', async () => {
    const client = labClient({
      getLabOrderReleaseVersionReport: vi.fn().mockRejectedValue(new Error('network')),
    })
    render(<ReleaseVersionHistory client={client} labId="lab-1" orderId="order-a" />)

    await userEvent.click(screen.getByRole('button', { name: "Voir l'historique des versions" }))
    const versions = await screen.findAllByRole('listitem')
    await userEvent.click(
      within(versions[0]).getByRole('button', { name: 'Voir le compte rendu' }),
    )

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Impossible de charger le compte rendu de cette version.',
    )
  })
})

// ── Clinic side ────────────────────────────────────────────────────────────────────────────────

const clinicMe: ExchangeMeResponse = {
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
      capabilities: [
        'order.manage',
        'patient.directory.read',
        'patient.register',
        'result.review',
        'visit.document',
        'visit.read',
      ],
    },
  ],
}

const clinicPatient = {
  patient_id: 'patient-1',
  display_name: 'Fatouma Robleh',
  date_of_birth: '1988-06-02',
  contact_channel_type: 'PHONE' as const,
  contact_value: '+25377100001',
}

function clinicQueueEntry(
  overrides: Partial<ProviderResultReviewQueueEntry> = {},
): ProviderResultReviewQueueEntry {
  return {
    review_id: 'review-1',
    order_id: 'order-a',
    release_version_id: V1,
    release_version_number: 1,
    is_amendment: false,
    released_at: '2026-09-17T09:30:00Z',
    legacy_unversioned: false,
    review_status: 'PENDING',
    reviewed_at: null,
    patient: clinicPatient,
    order_created_at: '2026-09-17T08:00:00Z',
    ...overrides,
  }
}

const clinicHistory: ProviderReleaseVersionHistoryResponse = {
  order_id: 'order-a',
  versions: [
    {
      release_version_id: V1,
      release_version_number: 1,
      is_amendment: false,
      released_at: '2026-09-17T09:30:00Z',
      review_status: 'PENDING',
      reviewed_at: null,
      reviewed_by_practitioner_reference_id: null,
    },
    {
      release_version_id: V2,
      release_version_number: 2,
      is_amendment: true,
      released_at: '2026-09-18T11:00:00Z',
      review_status: 'PENDING',
      reviewed_at: null,
      reviewed_by_practitioner_reference_id: null,
    },
  ],
}

function clinicVersionContent(
  releaseVersionId: string,
  releaseVersionNumber: number,
  numericValue: string,
): ProviderReleaseVersionContentResponse {
  return {
    order_id: 'order-a',
    release_version_id: releaseVersionId,
    release_version_number: releaseVersionNumber,
    is_amendment: releaseVersionNumber > 1,
    patient: clinicPatient,
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
            numeric_value: numericValue,
            numeric_unit: 'g/L',
            reference_range_low: '0.7',
            reference_range_high: '1.1',
            text_value: null,
            recorded_at: '2026-09-17T09:00:00Z',
          },
        ],
      },
    ],
  }
}

function clinicQueueResponse(
  entries: ProviderResultReviewQueueEntry[],
): ProviderResultReviewQueueResponse {
  return { provider_organization_id: 'provider-a', entries }
}

describe('clinic entry point — the button belongs to the version on screen', () => {
  function clinicClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
    return {
      getMe: vi.fn().mockResolvedValue(clinicMe),
      listResultReviews: vi
        .fn()
        .mockResolvedValue(clinicQueueResponse([clinicQueueEntry()])),
      getReleaseVersions: vi.fn().mockResolvedValue(clinicHistory),
      getReleaseVersion: vi
        .fn()
        .mockImplementation((_p: string, _o: string, versionId: string) =>
          Promise.resolve(
            versionId === V2
              ? clinicVersionContent(V2, 2, '9.9')
              : clinicVersionContent(V1, 1, '0.9'),
          ),
        ),
      getReleaseVersionReport: vi.fn().mockResolvedValue(makeReport()),
      getOrderResults: vi.fn(),
      reviewReleaseVersionNoFollowUp: vi.fn(),
      reviewReleaseVersionFollowUp: vi.fn(),
      reviewLegacyOrderNoFollowUp: vi.fn(),
      reviewLegacyOrderFollowUp: vi.fn(),
      ...overrides,
    } as unknown as ExchangeClient
  }

  it('prints Version 1 while Version 2 is the current release', async () => {
    const client = clinicClient()
    render(<StaffApp client={client} initialSection="results" />)

    // The queue entry is the Version 1 review obligation, so the panel opens on Version 1.
    await userEvent.click(await screen.findByRole('button', { name: /^Examiner le résultat de/ }))
    await waitFor(() => expect(client.getReleaseVersion).toHaveBeenCalled())

    await userEvent.click(await screen.findByRole('button', { name: 'Voir le compte rendu' }))

    expect(client.getReleaseVersionReport).toHaveBeenCalledTimes(1)
    expect(client.getReleaseVersionReport).toHaveBeenCalledWith('provider-a', 'order-a', V1)
    expect(client.getReleaseVersionReport).not.toHaveBeenCalledWith('provider-a', 'order-a', V2)

    expect(await screen.findByText('Compte rendu d’analyses')).toBeInTheDocument()
    expect(
      screen.getByText('Version remplacée — résultat actuel : version 2'),
    ).toBeInTheDocument()
  })
})
