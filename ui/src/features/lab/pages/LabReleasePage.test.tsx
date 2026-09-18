import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../../api/exchangeClient'
import type {
  ExchangeMeResponse,
  LabResultInspection,
  LabWorkflowPatientIdentity,
  ReleaseQueue,
} from '../../../api/exchangeTypes'
import { LabApp } from '../LabApp'

const meDualRole: ExchangeMeResponse = {
  identity_id: 'identity-1',
  issuer: 'issuer',
  subject: 'subject',
  display_name: 'Agent Synthétique',
  email: null,
  platform_admin: false,
  memberships: [
    {
      organization_id: 'lab-a',
      organization_name: 'Laboratoire A',
      organization_type: 'LAB',
      organization_status: 'ACTIVE',
      membership_status: 'ACTIVE',
      roles: ['LAB_TECH', 'LAB_RESULT_RELEASER'],
    },
  ],
}

const meReleaserOnly: ExchangeMeResponse = {
  ...meDualRole,
  memberships: [{ ...meDualRole.memberships[0], roles: ['LAB_RESULT_RELEASER'] }],
}

const meTechOnly: ExchangeMeResponse = {
  ...meDualRole,
  memberships: [{ ...meDualRole.memberships[0], roles: ['LAB_TECH'] }],
}

function makePatient(overrides: Partial<LabWorkflowPatientIdentity> = {}): LabWorkflowPatientIdentity {
  return {
    patient_id: '11111111-1111-4111-8111-111111111111',
    display_name: 'Fatouma Robleh',
    date_of_birth: '1988-06-02',
    ...overrides,
  }
}

function makeQueue(overrides: Partial<ReleaseQueue> = {}): ReleaseQueue {
  return {
    lab_organization_id: 'lab-a',
    orders: [
      {
        order_id: 'order-a',
        patient: makePatient(),
        // Deliberately distinct from `makeInspection()`'s own `ordering_provider_name` below --
        // the queue row and the inspection panel render simultaneously once a panel is open, so
        // sharing a string would make `findByText` ambiguous across the two.
        ordering_provider_name: 'Queue Row Provider',
        requested_tests: [
          {
            catalog_code_snapshot: 'GLU',
            catalog_display_name_snapshot: 'Glycémie à jeun',
            selected_price_amount: '1000',
            selected_price_currency: 'DJF',
          },
        ],
        updated_at: '2026-09-18T10:00:00Z',
      },
    ],
    ...overrides,
  }
}

function makeInspection(overrides: Partial<LabResultInspection> = {}): LabResultInspection {
  return {
    order_id: 'order-a',
    order_status: 'IN_PROGRESS',
    all_items_entry_complete: true,
    released: false,
    released_at: null,
    patient: makePatient(),
    ordering_provider_name: 'Clinique de la Paix',
    result_fingerprint: 'fingerprint-1',
    results: [
      {
        item_id: 'item-a',
        catalog_code_snapshot: 'GLU',
        catalog_display_name_snapshot: 'Glycémie à jeun',
        entry_completed: true,
        entry_completed_at: '2026-09-18T10:40:00Z',
        has_correction_history: false,
        observations: [
          {
            observation_id: 'obs-1',
            source_code: 'GLU',
            source_system: 'dhealth-exchange',
            display_label_snapshot: 'Glycémie',
            result_type: 'NUMERIC',
            numeric_value: '5.4000',
            numeric_unit: 'mmol/L',
            reference_range_low: null,
            reference_range_high: null,
            text_value: null,
            recorded_at: '2026-09-18T10:40:00Z',
          },
        ],
      },
    ],
    ...overrides,
  }
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(meDualRole),
    listLabOrders: vi.fn().mockResolvedValue([]),
    getReleaseQueue: vi.fn().mockResolvedValue(makeQueue()),
    getLabOrderResults: vi.fn().mockResolvedValue(makeInspection()),
    releaseLabOrderResults: vi.fn().mockResolvedValue({
      order_id: 'order-a',
      released_by_identity_id: 'identity-1',
      released_at: '2026-09-18T11:00:00Z',
    }),
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

describe('LabReleasePage', () => {
  it('RQ1: a dual-role user sees the Libération tab and the release queue', async () => {
    const client = makeClient()
    render(<LabApp client={client} initialTab="release" />)
    expect(await screen.findByText('Libération des résultats')).toBeInTheDocument()
    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
  })

  it('RQ5: a releaser-only user reaches the release queue with no LAB_TECH queue access', async () => {
    const client = makeClient({ getMe: vi.fn().mockResolvedValue(meReleaserOnly) })
    render(<LabApp client={client} initialTab="release" />)
    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ordres' })).not.toBeInTheDocument()
  })

  it('a LAB_TECH-only user sees no Libération tab and the page itself refuses access', async () => {
    const client = makeClient({ getMe: vi.fn().mockResolvedValue(meTechOnly) })
    render(<LabApp client={client} initialTab="release" />)
    expect(screen.queryByRole('button', { name: 'Libération' })).not.toBeInTheDocument()
    expect(
      await screen.findByText("Vous n'êtes pas autorisé(e) à libérer des résultats pour ce laboratoire."),
    ).toBeInTheDocument()
  })

  it('REL1: inspecting an order shows patient, provider, and every effective observation', async () => {
    const client = makeClient()
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="release" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText('Clinique de la Paix')).toBeInTheDocument()
    expect(await screen.findByText(/Glycémie : 5.4000 mmol\/L/)).toBeInTheDocument()
  })

  it('UI-R1: the release confirmation dialog carries the exact required wording', async () => {
    const client = makeClient()
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="release" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Libérer les résultats' }))
    expect(
      await screen.findByText(
        "Libérer ces résultats ? Ils deviendront disponibles pour l'établissement prescripteur.",
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirmer la libération' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Annuler' })).toBeInTheDocument()
  })

  it('REL2: confirming release calls the API with the exact inspected fingerprint and shows success', async () => {
    const releaseLabOrderResults = vi.fn().mockResolvedValue({
      order_id: 'order-a',
      released_by_identity_id: 'identity-1',
      released_at: '2026-09-18T11:00:00Z',
    })
    const client = makeClient({ releaseLabOrderResults })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="release" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Libérer les résultats' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la libération' }))

    await waitFor(() => expect(releaseLabOrderResults).toHaveBeenCalledTimes(1))
    expect(releaseLabOrderResults).toHaveBeenCalledWith(
      'lab-a',
      'order-a',
      { expected_result_fingerprint: 'fingerprint-1' },
      expect.anything(),
    )
    expect(await screen.findByText(/Résultats libérés/)).toBeInTheDocument()
  })

  it('REL3: a stale-fingerprint conflict forces re-inspection, never a silent retry', async () => {
    const releaseLabOrderResults = vi
      .fn()
      .mockRejectedValue(new ExchangeApiError(409, 'CONFLICT', 'stale fingerprint'))
    const getLabOrderResults = vi
      .fn()
      .mockResolvedValueOnce(makeInspection({ result_fingerprint: 'fingerprint-1' }))
      .mockResolvedValueOnce(makeInspection({ result_fingerprint: 'fingerprint-2' }))
    const client = makeClient({ releaseLabOrderResults, getLabOrderResults })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="release" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Libérer les résultats' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la libération' }))

    expect(
      await screen.findByText(
        'Les résultats ont changé depuis votre vérification. Veuillez les examiner à nouveau avant de les libérer.',
      ),
    ).toBeInTheDocument()
    // A fresh inspection was actually re-fetched (never merely a static message) -- the release
    // button is offered again from the new inspection, never auto-resubmitted.
    await waitFor(() => expect(getLabOrderResults).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('button', { name: 'Libérer les résultats' })).toBeInTheDocument()
    expect(releaseLabOrderResults).toHaveBeenCalledTimes(1)
  })

  it('RQ3: a released order no longer appears after the queue refresh', async () => {
    const queue = deferred<ReleaseQueue>()
    let callCount = 0
    const client = makeClient({
      getReleaseQueue: vi.fn(() => {
        callCount += 1
        return callCount === 1 ? Promise.resolve(makeQueue()) : queue.promise
      }),
    })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="release" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Libérer les résultats' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la libération' }))
    await screen.findByText(/Résultats libérés/)

    await act(async () => {
      queue.resolve(makeQueue({ orders: [] }))
    })
    expect(await screen.findByText('Aucun ordre prêt')).toBeInTheDocument()
  })

  it('RQ4: cross-lab queue isolation -- switching labs clears the previous lab panel', async () => {
    const meTwoLabs: ExchangeMeResponse = {
      ...meDualRole,
      memberships: [
        meDualRole.memberships[0],
        { ...meDualRole.memberships[0], organization_id: 'lab-b', organization_name: 'Laboratoire B' },
      ],
    }
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue(meTwoLabs),
      getReleaseQueue: vi.fn((labId: string) =>
        Promise.resolve(
          makeQueue({
            lab_organization_id: labId,
            orders:
              labId === 'lab-a'
                ? [
                    {
                      order_id: 'order-a',
                      patient: makePatient({ display_name: 'Patient A' }),
                      ordering_provider_name: 'Queue Row Provider',
                      requested_tests: [],
                      updated_at: '2026-09-18T10:00:00Z',
                    },
                  ]
                : [],
          }),
        ),
      ),
    })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="release" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    // The panel's own inspection read uses `makeClient()`'s default `getLabOrderResults`
    // (`makeInspection()`, ordering_provider_name "Clinique de la Paix") -- unambiguous against
    // this queue row's own distinct "Queue Row Provider" text.
    await screen.findByText('Clinique de la Paix')

    const select = await screen.findByLabelText('Sélectionner le laboratoire')
    await user.selectOptions(select, 'lab-b')

    expect(screen.queryByText('Clinique de la Paix')).not.toBeInTheDocument()
    expect(await screen.findByText('Aucun ordre prêt')).toBeInTheDocument()
  })

  it('a late inspection response for order A never mutates the now-visible order B panel', async () => {
    const inspectionA = deferred<LabResultInspection>()
    const client = makeClient({
      getReleaseQueue: vi.fn().mockResolvedValue(
        makeQueue({
          orders: [
            {
              order_id: 'order-a',
              patient: makePatient({ display_name: 'Patient A' }),
              ordering_provider_name: 'Queue Row A',
              requested_tests: [],
              updated_at: '2026-09-18T10:00:00Z',
            },
            {
              order_id: 'order-b',
              patient: makePatient({ display_name: 'Patient B' }),
              ordering_provider_name: 'Queue Row B',
              requested_tests: [],
              updated_at: '2026-09-18T10:00:00Z',
            },
          ],
        }),
      ),
      getLabOrderResults: vi.fn((_labId: string, orderId: string) =>
        orderId === 'order-a'
          ? inspectionA.promise
          : Promise.resolve(makeInspection({ order_id: 'order-b', ordering_provider_name: 'Clinic B' })),
      ),
    })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="release" />)

    await user.click(
      await screen.findByRole('button', { name: 'Examiner les résultats de Patient A' }),
    )
    await user.click(
      await screen.findByRole('button', { name: 'Examiner les résultats de Patient B' }),
    )
    await screen.findByText('Clinic B')

    await act(async () => {
      inspectionA.resolve(makeInspection({ order_id: 'order-a', ordering_provider_name: 'Clinic A' }))
    })

    expect(screen.queryByText('Clinic A')).not.toBeInTheDocument()
    expect(screen.getByText('Clinic B')).toBeInTheDocument()
  })

  it('an inspection response whose order_id disagrees with the request fails closed', async () => {
    const client = makeClient({
      getLabOrderResults: vi.fn().mockResolvedValue(makeInspection({ order_id: 'order-WRONG' })),
    })
    render(<LabApp client={client} initialTab="release" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText('Impossible de vérifier de manière sûre cet ordre.'),
    ).toBeInTheDocument()
  })

  it('incomplete entry shows a truthful blocking message, never a release button', async () => {
    const client = makeClient({
      getLabOrderResults: vi.fn().mockResolvedValue(makeInspection({ all_items_entry_complete: false })),
    })
    render(<LabApp client={client} initialTab="release" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText(
        "Au moins une analyse n'a pas encore de saisie terminée -- la libération n'est pas encore possible.",
      ),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Libérer les résultats' })).not.toBeInTheDocument()
  })
})
