import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../../api/exchangeClient'
import type {
  AmendmentInspection,
  AmendmentQueue,
  ExchangeMeResponse,
  LabResultInspection,
  LabWorkflowPatientIdentity,
  ReleaseQueue,
} from '../../../api/exchangeTypes'
import { StaffApp } from '../../staff/StaffApp'
import { renderWithWorkspace } from '../../../test/renderWithWorkspace'
import { LabProvider } from '../LabContext'
import { LabReleasePage } from './LabReleasePage'

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

function makeAmendmentQueue(overrides: Partial<AmendmentQueue> = {}): AmendmentQueue {
  return {
    lab_organization_id: 'lab-a',
    amendments: [
      {
        order_id: 'order-a',
        amendment_id: 'amendment-a',
        amendment_number: 1,
        patient: makePatient(),
        ordering_provider_name: 'Amendment Queue Provider',
        requested_tests: [
          {
            catalog_code_snapshot: 'GLU',
            catalog_display_name_snapshot: 'Glycémie à jeun',
            selected_price_amount: null,
            selected_price_currency: null,
          },
        ],
        started_at: '2026-09-18T12:00:00Z',
      },
    ],
    ...overrides,
  }
}

function makeAmendmentInspection(overrides: Partial<AmendmentInspection> = {}): AmendmentInspection {
  return {
    amendment: {
      amendment_id: 'amendment-a',
      lab_order_id: 'order-a',
      amendment_number: 1,
      base_release_version_id: 'version-1',
      status: 'PREPARING',
      started_by_identity_id: 'identity-1',
      started_at: '2026-09-18T12:00:00Z',
      released_by_identity_id: null,
      released_at: null,
    },
    ready_to_release: true,
    result_fingerprint: 'amend-fingerprint-1',
    items: [
      {
        item_id: 'item-a',
        catalog_code_snapshot: 'GLU',
        catalog_display_name_snapshot: 'Glycémie à jeun',
        entry_completed_at: '2026-09-18T10:40:00Z',
        changed_in_this_amendment: true,
        observations: [
          {
            observation_id: 'obs-2',
            source_code: 'GLU',
            source_system: 'dhealth-exchange',
            display_label_snapshot: 'Glycémie',
            result_type: 'NUMERIC',
            numeric_value: '6.0000',
            numeric_unit: 'mmol/L',
            reference_range_low: null,
            reference_range_high: null,
            text_value: null,
            recorded_at: '2026-09-18T11:50:00Z',
            amendment_id: 'amendment-a',
            template_observation_definition_id: 'def-a',
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
    getAmendmentReleaseQueue: vi.fn().mockResolvedValue(makeAmendmentQueue()),
    getLabOrderAmendment: vi.fn().mockResolvedValue(makeAmendmentInspection()),
    getLabOrderReleaseVersions: vi.fn().mockResolvedValue({
      order_id: 'order-a',
      versions: [
        {
          release_version_id: 'version-1',
          version_number: 1,
          lab_result_release_id: 'release-1',
          amendment_id: null,
          created_at: '2026-09-18T11:00:00Z',
          is_amendment: false,
        },
      ],
    }),
    getLabOrderReleaseVersionContent: vi.fn().mockResolvedValue({
      order_id: 'order-a',
      release_version_id: 'version-1',
      release_version_number: 1,
      is_amendment: false,
      patient: makePatient(),
      ordering_provider_name: 'Clinique de la Paix',
      results: [
        {
          item_id: 'item-a',
          catalog_code_snapshot: 'GLU',
          catalog_display_name_snapshot: 'Glycémie à jeun',
          entry_completed_at: '2026-09-18T10:40:00Z',
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
    }),
    releaseLabOrderAmendment: vi.fn().mockResolvedValue({
      release_version_id: 'version-2',
      version_number: 2,
      lab_result_release_id: null,
      amendment_id: 'amendment-a',
      created_at: '2026-09-18T12:05:00Z',
      is_amendment: true,
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
    render(<StaffApp client={client} initialSection="results" />)
    expect(await screen.findByText('Libération des résultats')).toBeInTheDocument()
    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
  })

  it('RQ5: a releaser-only user reaches the release queue with no LAB_TECH queue access', async () => {
    const client = makeClient({ getMe: vi.fn().mockResolvedValue(meReleaserOnly) })
    render(<StaffApp client={client} initialSection="results" />)
    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ordres' })).not.toBeInTheDocument()
  })

  // The original single test asserted both halves at once: no section in the
  // shell, and a refusal from the page. Those are now two separate facts, because
  // the shell no longer renders a section a membership cannot use — so the page
  // cannot be reached through it to be observed refusing. Both halves are still
  // covered, deliberately split rather than dropped: the second is the one that
  // would matter if the shell ever stopped filtering.
  it('ADR-044: a LAB_TECH-only user is offered no Résultats section at all', async () => {
    const client = makeClient({ getMe: vi.fn().mockResolvedValue(meTechOnly) })
    render(<StaffApp client={client} initialSection="results" />)

    const nav = within(await screen.findByRole('navigation', { name: 'Navigation principale' }))
    expect(nav.queryByRole('button', { name: 'Résultats' })).not.toBeInTheDocument()
    // And the release surface itself is nowhere on screen.
    expect(screen.queryByRole('heading', { name: 'Libération des résultats' })).not.toBeInTheDocument()
  })

  it('ADR-044: the release page refuses a LAB_TECH-only user even when reached directly', async () => {
    // Mounted past the navigation on purpose. Hiding the section is a usability
    // measure, not the protection; the page states its own refusal, and the
    // server refuses the request regardless of either.
    const client = makeClient({ getMe: vi.fn().mockResolvedValue(meTechOnly) })
    renderWithWorkspace(
      <LabProvider>
        <LabReleasePage />
      </LabProvider>,
      { client },
    )

    expect(
      await screen.findByText("Vous n'êtes pas autorisé(e) à libérer des résultats pour ce laboratoire."),
    ).toBeInTheDocument()
  })

  it('REL1: inspecting an order shows patient, provider, and every effective observation', async () => {
    const client = makeClient()
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText('Clinique de la Paix')).toBeInTheDocument()
    expect(await screen.findByText(/Glycémie : 5.4000 mmol\/L/)).toBeInTheDocument()
  })

  it('UI-R1: the release confirmation dialog carries the exact required wording', async () => {
    const client = makeClient()
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
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
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Libérer les résultats' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la libération' }))

    await waitFor(() => expect(releaseLabOrderResults).toHaveBeenCalledTimes(1))
    expect(releaseLabOrderResults).toHaveBeenCalledWith('lab-a', 'order-a', {
      expected_result_fingerprint: 'fingerprint-1',
    })
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
    render(<StaffApp client={client} initialSection="results" />)
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
    render(<StaffApp client={client} initialSection="results" />)
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
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    // The panel's own inspection read uses `makeClient()`'s default `getLabOrderResults`
    // (`makeInspection()`, ordering_provider_name "Clinique de la Paix") -- unambiguous against
    // this queue row's own distinct "Queue Row Provider" text.
    await screen.findByText('Clinique de la Paix')

    const select = await screen.findByLabelText('Établissement actif')
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
    render(<StaffApp client={client} initialSection="results" />)

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
    render(<StaffApp client={client} initialSection="results" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText('Impossible de vérifier de manière sûre cet ordre.'),
    ).toBeInTheDocument()
  })

  it('incomplete entry shows a truthful blocking message, never a release button', async () => {
    const client = makeClient({
      getLabOrderResults: vi.fn().mockResolvedValue(makeInspection({ all_items_entry_complete: false })),
    })
    render(<StaffApp client={client} initialSection="results" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText(
        "Au moins une analyse n'a pas encore de saisie terminée -- la libération n'est pas encore possible.",
      ),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Libérer les résultats' })).not.toBeInTheDocument()
  })

  // -- DH-LAB-005 §44-47: the "Amendements" section --------------------------------------------

  it('RQ-A1: the Amendements tab loads the amendment release queue', async () => {
    const client = makeClient()
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('tab', { name: 'Amendements' }))
    expect(await screen.findByText('Amendment Queue Provider')).toBeInTheDocument()
    expect(screen.getByText(/Amendement n°1/)).toBeInTheDocument()
  })

  it('AF-UI1: opening an amendment shows its items, "Modifié dans cet amendement", and status textually', async () => {
    const client = makeClient()
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('tab', { name: 'Amendements' }))
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText('Modifié dans cet amendement')).toBeInTheDocument()
    expect(screen.getByText('En préparation')).toBeInTheDocument()
  })

  it('AR-UI1: releasing an amendment calls the API with the exact inspected fingerprint', async () => {
    const releaseLabOrderAmendment = vi.fn().mockResolvedValue({
      release_version_id: 'version-2',
      version_number: 2,
      lab_result_release_id: null,
      amendment_id: 'amendment-a',
      created_at: '2026-09-18T12:05:00Z',
      is_amendment: true,
    })
    const client = makeClient({ releaseLabOrderAmendment })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('tab', { name: 'Amendements' }))
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Libérer l'amendement" }))
    expect(
      await screen.findByText(/Libérer cet amendement \?/),
    ).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Confirmer la libération' }))

    await waitFor(() => expect(releaseLabOrderAmendment).toHaveBeenCalledTimes(1))
    expect(releaseLabOrderAmendment).toHaveBeenCalledWith('lab-a', 'order-a', 'amendment-a', {
      expected_result_fingerprint: 'amend-fingerprint-1',
    })
    expect(await screen.findByText(/Amendement libéré/)).toBeInTheDocument()
  })

  it('§42: a stale amendment fingerprint conflict shows the exact required wording and re-inspects, never a silent retry', async () => {
    const releaseLabOrderAmendment = vi
      .fn()
      .mockRejectedValue(new ExchangeApiError(409, 'CONFLICT', 'stale amendment fingerprint'))
    const getLabOrderAmendment = vi
      .fn()
      .mockResolvedValueOnce(makeAmendmentInspection({ result_fingerprint: 'amend-fingerprint-1' }))
      .mockResolvedValueOnce(makeAmendmentInspection({ result_fingerprint: 'amend-fingerprint-2' }))
    const client = makeClient({ releaseLabOrderAmendment, getLabOrderAmendment })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('tab', { name: 'Amendements' }))
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Libérer l'amendement" }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la libération' }))

    expect(
      await screen.findByText(
        'Les résultats de cet amendement ont changé depuis votre vérification. Veuillez les examiner à nouveau avant de les libérer.',
      ),
    ).toBeInTheDocument()
    await waitFor(() => expect(getLabOrderAmendment).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('button', { name: "Libérer l'amendement" })).toBeInTheDocument()
    expect(releaseLabOrderAmendment).toHaveBeenCalledTimes(1)
  })

  it('an amendment with no revisions shows a truthful blocking message, never a release button', async () => {
    const client = makeClient({
      getLabOrderAmendment: vi.fn().mockResolvedValue(makeAmendmentInspection({ ready_to_release: false })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('tab', { name: 'Amendements' }))
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText("Cet amendement n'a encore aucune modification -- la libération n'est pas encore possible."),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Libérer l'amendement" })).not.toBeInTheDocument()
  })

  it('no "Annuler l\'amendement" control exists anywhere in the Amendements section', async () => {
    const client = makeClient()
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('tab', { name: 'Amendements' }))
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Modifié dans cet amendement')
    expect(screen.queryByText(/Annuler l'amendement/)).not.toBeInTheDocument()
  })

  it('VH-UI1: a released order exposes its version history, and Version 1 content is shown', async () => {
    const client = makeClient({
      getLabOrderResults: vi.fn().mockResolvedValue(
        makeInspection({ released: true, released_at: '2026-09-18T11:00:00Z' }),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Voir l'historique des versions" }))
    expect(
      await screen.findByText(
        (_content, node) => node?.tagName === 'P' && /Version 1 — Version initiale/.test(node.textContent ?? ''),
      ),
    ).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Voir le contenu exact' }))
    expect(await screen.findByText(/Glycémie à jeun — Glycémie : 5.4000 mmol\/L/)).toBeInTheDocument()
  })

  /*
   * DH-USABILITY-001 §13/§21 -- the lab's own answer to "which one counts now".
   *
   * The history rows said which version came first and when. They did not say which one is the
   * result in force, so a technician checking what was reported could read superseded values with
   * nothing on screen contradicting them. Same fact, same words as the clinician's screen.
   */
  function historyRow(pattern: RegExp) {
    return screen.findByText(
      (_content, node) => node?.tagName === 'P' && pattern.test(node.textContent ?? ''),
    )
  }

  function amendedHistoryClient(): ExchangeClient {
    return makeClient({
      getLabOrderResults: vi
        .fn()
        .mockResolvedValue(makeInspection({ released: true, released_at: '2026-09-18T11:00:00Z' })),
      getLabOrderReleaseVersions: vi.fn().mockResolvedValue({
        order_id: 'order-a',
        versions: [
          {
            release_version_id: 'version-1',
            version_number: 1,
            lab_result_release_id: 'release-1',
            amendment_id: null,
            created_at: '2026-09-18T11:00:00Z',
            is_amendment: false,
          },
          {
            release_version_id: 'version-2',
            version_number: 2,
            lab_result_release_id: null,
            amendment_id: 'amendment-a',
            created_at: '2026-09-18T12:05:00Z',
            is_amendment: true,
          },
        ],
      }),
    })
  }

  it('VH-UI2: with an amendment released, exactly one version row is named current -- the amendment', async () => {
    const user = userEvent.setup()
    render(<StaffApp client={amendedHistoryClient()} initialSection="results" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Voir l'historique des versions" }))

    await historyRow(/Version 1 — Version initiale — Remplacé/)
    await historyRow(/Version 2 — Amendement — Résultat actuel/)
    // Not merely "one of each": a reversed derivation would satisfy that and mislabel both.
    expect(screen.queryByText((_c, n) => n?.tagName === 'P' && /Version 1 — Version initiale — Résultat actuel/.test(n.textContent ?? ''))).toBeNull()
  })

  it('VH-UI3: opening a replaced version\'s exact content says so before the values', async () => {
    const user = userEvent.setup()
    render(<StaffApp client={amendedHistoryClient()} initialSection="results" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Voir l'historique des versions" }))
    await historyRow(/Version 1 — Version initiale — Remplacé/)

    // The first row is version 1 -- the one that was replaced.
    await user.click(screen.getAllByRole('button', { name: 'Voir le contenu exact' })[0])

    expect(
      await screen.findByText('Version remplacée — ce n’est plus le résultat de référence.'),
    ).toBeInTheDocument()
    // And the exact content is still shown: this labels the values, it does not withhold them.
    expect(await screen.findByText(/Glycémie à jeun — Glycémie : 5.4000 mmol\/L/)).toBeInTheDocument()
  })

  it('VH-UI4: a single released version is named current and carries no replacement line', async () => {
    const client = makeClient({
      getLabOrderResults: vi
        .fn()
        .mockResolvedValue(makeInspection({ released: true, released_at: '2026-09-18T11:00:00Z' })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Voir l'historique des versions" }))

    await historyRow(/Version 1 — Version initiale — Résultat actuel/)
    await user.click(await screen.findByRole('button', { name: 'Voir le contenu exact' }))

    await screen.findByText(/Glycémie à jeun — Glycémie : 5.4000 mmol\/L/)
    expect(
      screen.queryByText('Version remplacée — ce n’est plus le résultat de référence.'),
    ).not.toBeInTheDocument()
  })

  it('switching to Amendements never calls the original release queue/inspection endpoints, and vice versa', async () => {
    const getReleaseQueue = vi.fn().mockResolvedValue(makeQueue())
    const getAmendmentReleaseQueue = vi.fn().mockResolvedValue(makeAmendmentQueue())
    const client = makeClient({ getReleaseQueue, getAmendmentReleaseQueue })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="results" />)
    await screen.findByText('Fatouma Robleh')
    expect(getAmendmentReleaseQueue).not.toHaveBeenCalled()

    await user.click(await screen.findByRole('tab', { name: 'Amendements' }))
    await screen.findByText('Amendment Queue Provider')
    expect(getReleaseQueue).toHaveBeenCalledTimes(1)
  })
})
