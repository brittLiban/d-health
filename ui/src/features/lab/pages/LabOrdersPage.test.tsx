import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../../api/exchangeClient'
import type {
  Amendment,
  AmendmentInspection,
  ExchangeMeResponse,
  LabCollectionResponse,
  LabObservationHistory,
  LabOrderBlock,
  LabSpecimen,
  LabWorkflowOrder,
  LabWorkflowPatientIdentity,
  ResultEntryWorkspace,
} from '../../../api/exchangeTypes'
import { capabilitiesForRoles } from '../../../test/renderWithWorkspace'
import { StaffApp } from '../../staff/StaffApp'

const me: ExchangeMeResponse = {
  identity_id: 'identity-1',
  issuer: 'issuer',
  subject: 'subject',
  display_name: 'Technicien Synthétique',
  email: null,
  platform_admin: false,
  memberships: [
    {
      organization_id: 'lab-a',
      organization_name: 'Laboratoire A',
      organization_type: 'LAB',
      organization_status: 'ACTIVE',
      membership_status: 'ACTIVE',
      roles: ['LAB_TECH'],
      capabilities: ['lab.queue.work'],
    },
  ],
}

const meWithTwoLabs: ExchangeMeResponse = {
  ...me,
  memberships: [
    me.memberships[0],
    { ...me.memberships[0], organization_id: 'lab-b', organization_name: 'Laboratoire B' },
  ],
}

// DHEALTH-ACCESS-CAPABILITIES-001: `capabilities` must be overridden alongside `roles`. Spreading
// the LAB_TECH membership and changing only `roles` left this "releaser-only" person still holding
// `lab.queue.work` — the very authority this fixture exists to withhold.
const meReleaserOnly: ExchangeMeResponse = {
  ...me,
  memberships: [
    {
      ...me.memberships[0],
      roles: ['LAB_RESULT_RELEASER'],
      capabilities: capabilitiesForRoles(['LAB_RESULT_RELEASER']),
    },
  ],
}

function makePatient(overrides: Partial<LabWorkflowPatientIdentity> = {}): LabWorkflowPatientIdentity {
  return {
    patient_id: '11111111-1111-4111-8111-111111111111',
    display_name: 'Fatouma Robleh',
    date_of_birth: '1988-06-02',
    ...overrides,
  }
}

function makeOrder(overrides: Partial<LabWorkflowOrder> = {}): LabWorkflowOrder {
  return {
    order_id: 'order-a',
    status: 'SELECTED',
    selected_at: '2026-09-18T08:00:00Z',
    updated_at: '2026-09-18T08:00:00Z',
    patient_display_name: 'Fatouma Robleh',
    patient: makePatient(),
    ordering_provider_name: 'Clinique de la Paix',
    requested_tests: [
      {
        catalog_code_snapshot: 'GLU',
        catalog_display_name_snapshot: 'Glycémie à jeun',
        selected_price_amount: '1000',
        selected_price_currency: 'DJF',
      },
    ],
    collection_recorded_at: null,
    active_block: null,
    ...overrides,
  }
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(me),
    listLabOrders: vi.fn().mockResolvedValue([makeOrder()]),
    getLabOrder: vi.fn().mockResolvedValue(makeOrder()),
    acceptLabOrder: vi.fn(),
    rejectLabOrder: vi.fn(),
    recordLabOrderCollection: vi.fn(),
    startProcessingLabOrder: vi.fn(),
    getResultEntryWorkspace: vi.fn().mockResolvedValue(makeResultEntryWorkspace()),
    recordNativeResultValues: vi.fn(),
    completeResultEntry: vi.fn(),
    correctNativeObservation: vi.fn(),
    getLabObservationHistory: vi.fn().mockResolvedValue(makeHistory()),
    listLabOrderAmendments: vi.fn().mockResolvedValue({ order_id: 'order-a', amendments: [] }),
    startLabOrderAmendment: vi.fn(),
    // DHEALTH-LAB-BLOCKED-001: the panel reads block history for *every* order it opens, not only
    // blocked ones -- "has this been blocked before?" is a question about an order that looks
    // healthy right now. So every test that opens a panel needs this stubbed, not just the block
    // tests.
    listLabOrderBlocks: vi.fn().mockResolvedValue([]),
    // DHEALTH-SPECIMEN-001: the panel reads specimen history for every order it opens, for exactly
    // the reason block history is read for every order -- "how many physical samples does this
    // order have?" is a question about an order that looks ordinary, and an empty list is the
    // answer. Stubbed here so every panel-opening test has it, not only the specimen tests.
    listLabOrderSpecimens: vi.fn().mockResolvedValue([]),
    blockLabOrder: vi.fn(),
    resolveLabOrderBlock: vi.fn(),
    getLabOrderAmendment: vi.fn(),
    correctAmendmentNativeObservation: vi.fn(),
    ...overrides,
  } as unknown as ExchangeClient
}

function makeAmendment(overrides: Partial<Amendment> = {}): Amendment {
  return {
    amendment_id: 'amendment-a',
    lab_order_id: 'order-a',
    amendment_number: 1,
    base_release_version_id: 'version-1',
    status: 'PREPARING',
    started_by_identity_id: 'identity-1',
    started_at: '2026-09-18T12:00:00Z',
    released_by_identity_id: null,
    released_at: null,
    ...overrides,
  }
}

function makeAmendmentInspection(overrides: Partial<AmendmentInspection> = {}): AmendmentInspection {
  return {
    amendment: makeAmendment(),
    ready_to_release: false,
    result_fingerprint: 'amend-fingerprint-1',
    items: [
      {
        item_id: 'item-a',
        catalog_code_snapshot: 'GLU',
        catalog_display_name_snapshot: 'Glycémie à jeun',
        entry_completed_at: '2026-09-18T10:40:00Z',
        changed_in_this_amendment: false,
        observations: [
          {
            observation_id: 'obs-a',
            source_code: 'DEMO_A',
            source_system: 'dhealth-exchange',
            display_label_snapshot: 'Demo Measure A',
            result_type: 'NUMERIC',
            numeric_value: '10.0',
            numeric_unit: 'demo-unit',
            reference_range_low: null,
            reference_range_high: null,
            text_value: null,
            recorded_at: '2026-09-18T10:40:00Z',
            amendment_id: null,
            template_observation_definition_id: 'def-a',
          },
        ],
      },
    ],
    ...overrides,
  }
}

function makeHistory(overrides: Partial<LabObservationHistory> = {}): LabObservationHistory {
  return {
    order_id: 'order-a',
    item_id: 'item-a',
    observations: [],
    ...overrides,
  }
}

function makeResultEntryWorkspace(overrides: Partial<ResultEntryWorkspace> = {}): ResultEntryWorkspace {
  return {
    order_id: 'order-a',
    order_status: 'IN_PROGRESS',
    items: [
      {
        item_id: 'item-a',
        catalog_code_snapshot: 'GLU',
        catalog_display_name_snapshot: 'Glycémie à jeun',
        template: {
          template_id: 'template-a',
          version: 1,
          is_synthetic: true,
          observations: [
            {
              definition_id: 'def-a',
              source_code: 'DEMO_A',
              display_label: 'Demo Measure A',
              result_type: 'NUMERIC',
              numeric_unit: 'demo-unit',
              required: true,
              sort_order: 0,
            },
          ],
        },
        template_pinned: false,
        entry_mode: 'NATIVE_TEMPLATE',
        entry_completed: false,
        entry_completed_at: null,
        entered_values: [],
      },
    ],
    ...overrides,
  }
}

/**
 * DHEALTH-SPECIMEN-001: one physical specimen, as the specimen history read returns it.
 *
 * No specimen type, container, volume or status field -- the backend models none, so a test fixture
 * that invented one would be asserting a shape the product does not have.
 */
function makeSpecimen(overrides: Partial<LabSpecimen> = {}): LabSpecimen {
  return {
    id: 'specimen-1',
    order_id: 'order-a',
    recorded_at: '2026-09-18T10:42:00Z',
    recorded_by_identity_id: 'identity-tech-1',
    idempotency_key: null,
    ...overrides,
  }
}

function makeCollectionResponse(
  overrides: { order_id?: string; recorded_at?: string; specimen?: LabSpecimen | null } = {},
): LabCollectionResponse {
  // `specimen` defaults to a real one rather than `null`: `null` is the narrow legacy case (a
  // milestone recorded before specimens existed), and defaulting to it would have made every test
  // that does not care about specimens silently exercise the legacy path.
  return {
    order_id: 'order-a',
    recorded_at: '2026-09-18T10:42:00Z',
    specimen: makeSpecimen(),
    ...overrides,
  }
}

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
}

/** First call returns SELECTED (the initial panel open); every later call returns
 * `terminalStatus` -- simulates a mutation that actually committed server-side, discovered only
 * by the post-mutation recheck (never by the initial load, which must see the pre-mutation
 * state). */
function makeGetLabOrderRecheckMock(terminalStatus: LabWorkflowOrder['status']) {
  let calls = 0
  return vi.fn().mockImplementation((_labId: string, orderId: string) => {
    calls += 1
    return Promise.resolve(makeOrder({ order_id: orderId, status: calls === 1 ? 'SELECTED' : terminalStatus }))
  })
}

/** Same shape as `makeGetLabOrderRecheckMock`, but for collection: first call (the initial panel
 * open) returns ACCEPTED with no collection milestone; every later call (the post-mutation
 * recheck) returns ACCEPTED with a recorded milestone -- collection never changes `status`. */
function makeGetLabOrderRecheckMockForCollection() {
  let calls = 0
  return vi.fn().mockImplementation((_labId: string, orderId: string) => {
    calls += 1
    return Promise.resolve(
      makeOrder({
        order_id: orderId,
        status: 'ACCEPTED',
        collection_recorded_at: calls === 1 ? null : '2026-09-18T10:42:00Z',
      }),
    )
  })
}

/** General lost-response recheck mock carrying collection state on both calls -- the first call
 * is the initial panel open (`initialStatus`/`initialCollectionRecordedAt`), every later call is
 * the post-mutation recheck (`terminalStatus`/`recordedAt`). */
function makeGetLabOrderRecheckWithCollection(
  terminalStatus: LabWorkflowOrder['status'],
  recordedAt: string,
  options: {
    initialStatus?: LabWorkflowOrder['status']
    initialCollectionRecordedAt?: string | null
  } = {},
) {
  let calls = 0
  const initialStatus = options.initialStatus ?? 'ACCEPTED'
  const initialCollectionRecordedAt = options.initialCollectionRecordedAt ?? null
  return vi.fn().mockImplementation((_labId: string, orderId: string) => {
    calls += 1
    return Promise.resolve(
      makeOrder({
        order_id: orderId,
        status: calls === 1 ? initialStatus : terminalStatus,
        collection_recorded_at: calls === 1 ? initialCollectionRecordedAt : recordedAt,
      }),
    )
  })
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

describe('LabOrdersPage', () => {
  // -- Q-group: queue rendering ------------------------------------------------------------

  it('Q1/Q2/Q4/Q5: an assigned order renders with patient name primary, provider, and tests', async () => {
    const client = makeClient()
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(screen.getByText('Clinique de la Paix')).toBeInTheDocument()
    expect(screen.getByText('Glycémie à jeun')).toBeInTheDocument()
  })

  it('Q3: date of birth is rendered when known', async () => {
    const client = makeClient()
    render(<StaffApp client={client} initialSection="orders" />)
    expect(await screen.findByText(/Né\(e\) le/)).toBeInTheDocument()
  })

  it('Q6: the technical order reference is present but secondary, never the primary label', async () => {
    const client = makeClient()
    render(<StaffApp client={client} initialSection="orders" />)
    await screen.findByText('Fatouma Robleh')
    expect(screen.getByText(/Référence de l'ordre/)).toBeInTheDocument()
  })

  it('Q7: no marketplace price is shown in the work queue', async () => {
    const client = makeClient()
    render(<StaffApp client={client} initialSection="orders" />)
    await screen.findByText('Fatouma Robleh')
    expect(screen.queryByText(/DJF/)).not.toBeInTheDocument()
    expect(screen.queryByText('1000')).not.toBeInTheDocument()
  })

  it.each([
    ['SELECTED', /Nouvel ordre — à accepter/],
    ['ACCEPTED', /^Accepté$/],
    ['IN_PROGRESS', /^En cours$/],
    ['COMPLETED', /Résultats libérés/],
    ['REJECTED', /^Refusé$/],
  ] as const)('Q8-Q12: status %s is labelled truthfully', async (status, expected) => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status })]),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    expect(await screen.findByText(expected)).toBeInTheDocument()
  })

  // -- A-group: accept/reject actions -------------------------------------------------------

  it('A1/A2: a SELECTED order exposes both Accept and Reject controls', async () => {
    const client = makeClient()
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByRole('button', { name: "Accepter l'ordre" })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Refuser l'ordre" })).toBeInTheDocument()
  })

  it('A3: accepting calls the exact accept endpoint for the exact lab/order', async () => {
    const acceptLabOrder = vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' }))
    const client = makeClient({ acceptLabOrder, getLabOrder: makeGetLabOrderRecheckMock('ACCEPTED') })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Accepter l'ordre" }))
    await waitFor(() => expect(acceptLabOrder).toHaveBeenCalledWith('lab-a', 'order-a'))
  })

  it('A4/A7: reject requires explicit confirmation before calling the reject endpoint', async () => {
    const rejectLabOrder = vi.fn().mockResolvedValue(makeOrder({ status: 'REJECTED' }))
    const client = makeClient({ rejectLabOrder, getLabOrder: makeGetLabOrderRecheckMock('REJECTED') })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Refuser l'ordre" }))

    // Confirmation step first -- no mutation call yet.
    expect(await screen.findByText(/Refuser cet ordre \?/)).toBeInTheDocument()
    expect(rejectLabOrder).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Confirmer le refus' }))
    await waitFor(() => expect(rejectLabOrder).toHaveBeenCalledWith('lab-a', 'order-a'))
  })

  it('A5: an accepted order no longer exposes the Reject control', async () => {
    const client = makeClient({
      acceptLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' })),
      getLabOrder: makeGetLabOrderRecheckMock('ACCEPTED'),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Accepter l'ordre" }))

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: "Refuser l'ordre" })).not.toBeInTheDocument(),
    )
    expect(screen.queryByRole('button', { name: "Accepter l'ordre" })).not.toBeInTheDocument()
  })

  it('A6: a rejected order no longer exposes the Accept control', async () => {
    const client = makeClient({
      rejectLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'REJECTED' })),
      getLabOrder: makeGetLabOrderRecheckMock('REJECTED'),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Refuser l'ordre" }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer le refus' }))

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: "Accepter l'ordre" })).not.toBeInTheDocument(),
    )
  })

  it('A8: an accept response whose order_id disagrees fails closed', async () => {
    const client = makeClient({
      acceptLabOrder: vi.fn().mockResolvedValue(makeOrder({ order_id: 'order-DIFFERENT', status: 'ACCEPTED' })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Accepter l'ordre" }))
    expect(await screen.findByText('Impossible de vérifier de manière sûre cet ordre.')).toBeInTheDocument()
  })

  it('A9: a reject response whose order_id disagrees fails closed', async () => {
    const client = makeClient({
      rejectLabOrder: vi.fn().mockResolvedValue(makeOrder({ order_id: 'order-DIFFERENT', status: 'REJECTED' })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Refuser l'ordre" }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer le refus' }))
    expect(await screen.findByText('Impossible de vérifier de manière sûre cet ordre.')).toBeInTheDocument()
  })

  // -- LR-group: lost-response recovery ------------------------------------------------------

  it('LR1: accept commits but the HTTP response is lost -- recheck shows success, no error', async () => {
    const client = makeClient({
      acceptLabOrder: vi.fn().mockRejectedValue(new Error('network lost')),
      getLabOrder: makeGetLabOrderRecheckMock('ACCEPTED'),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Accepter l'ordre" }))

    await waitFor(() => expect(screen.getAllByText('Accepté').length).toBeGreaterThan(0))
    expect(screen.queryByText("L'action n'a pas pu être confirmée. Vous pouvez réessayer.")).not.toBeInTheDocument()
  })

  it('LR2: reject commits but the response is lost -- recheck shows success, no error', async () => {
    const client = makeClient({
      rejectLabOrder: vi.fn().mockRejectedValue(new Error('network lost')),
      getLabOrder: makeGetLabOrderRecheckMock('REJECTED'),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Refuser l'ordre" }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer le refus' }))

    await waitFor(() => expect(screen.getAllByText('Refusé').length).toBeGreaterThan(0))
    expect(screen.queryByText("L'action n'a pas pu être confirmée. Vous pouvez réessayer.")).not.toBeInTheDocument()
  })

  it('LR3: accept response ambiguous, order still SELECTED -- explicit retry stays available', async () => {
    const client = makeClient({
      acceptLabOrder: vi.fn().mockRejectedValue(new Error('timeout')),
      getLabOrder: vi.fn().mockImplementation((_labId, orderId) =>
        Promise.resolve(makeOrder({ order_id: orderId, status: 'SELECTED' })),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Accepter l'ordre" }))

    expect(await screen.findByText("L'action n'a pas pu être confirmée. Vous pouvez réessayer.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Accepter l'ordre" })).toBeInTheDocument()
  })

  it('LR5: an accept that lost a race to a concurrent reject shows REJECTED, never a false accept', async () => {
    const client = makeClient({
      acceptLabOrder: vi.fn().mockRejectedValue(new Error('conflict')),
      getLabOrder: makeGetLabOrderRecheckMock('REJECTED'),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Accepter l'ordre" }))

    await waitFor(() => expect(screen.getAllByText('Refusé').length).toBeGreaterThan(0))
    expect(
      screen.getByText("Cet ordre a déjà été traité différemment -- l'état actuel est affiché ci-dessous."),
    ).toBeInTheDocument()
  })

  // -- RR-group: race/stale-response safety --------------------------------------------------

  it('RR1: a stale queue response from lab A discarded after switching to lab B', async () => {
    const staleQueue = deferred<LabWorkflowOrder[]>()
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue(meWithTwoLabs),
      listLabOrders: vi.fn((labId: string) =>
        labId === 'lab-a'
          ? staleQueue.promise
          : Promise.resolve([makeOrder({ order_id: 'order-b', patient: makePatient({ display_name: 'Patient Lab B' }) })]),
      ),
    })
    render(<StaffApp client={client} initialSection="orders" />)

    const select = await screen.findByLabelText('Établissement actif')
    await userEvent.setup().selectOptions(select, 'lab-b')
    await screen.findByText('Patient Lab B')

    await act(async () => {
      staleQueue.resolve([makeOrder({ patient: makePatient({ display_name: 'Patient Lab A' }) })])
      await Promise.resolve()
    })

    expect(screen.queryByText('Patient Lab A')).not.toBeInTheDocument()
    expect(screen.getByText('Patient Lab B')).toBeInTheDocument()
  })

  it('RR1b: switching labs clears prior queue rows immediately, before the new fetch resolves', async () => {
    const labBQueue = deferred<LabWorkflowOrder[]>()
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue(meWithTwoLabs),
      listLabOrders: vi.fn((labId: string) =>
        labId === 'lab-a'
          ? Promise.resolve([makeOrder({ patient: makePatient({ display_name: 'Patient Lab A' }) })])
          : labBQueue.promise,
      ),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await screen.findByText('Patient Lab A')

    const select = await screen.findByLabelText('Établissement actif')
    await userEvent.setup().selectOptions(select, 'lab-b')

    await waitFor(() => expect(screen.queryByText('Patient Lab A')).not.toBeInTheDocument())

    await act(async () => {
      labBQueue.resolve([])
      await Promise.resolve()
    })
  })

  it('RR2: a stale detail response for order A discarded after opening order B', async () => {
    const detailA = deferred<LabWorkflowOrder>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ order_id: 'order-a', patient: makePatient({ display_name: 'Patient A' }) }),
        makeOrder({ order_id: 'order-b', patient: makePatient({ display_name: 'Patient B' }) }),
      ]),
      getLabOrder: vi.fn((_labId: string, orderId: string) =>
        orderId === 'order-a'
          ? detailA.promise
          : Promise.resolve(makeOrder({ order_id: 'order-b', patient: makePatient({ display_name: 'Patient B' }) })),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    const examineButtons = await screen.findAllByRole('button', { name: /^Examiner/ })
    await user.click(examineButtons[0]) // opens order-a, stalls
    await user.click(examineButtons[1]) // opens order-b

    // "Patient B" appears twice once order-b's own detail has loaded: once in its queue row,
    // once in the now-open detail panel's own full-variant identity block.
    await waitFor(() => expect(screen.getAllByText('Patient B').length).toBe(2))
    // "Patient A" must still appear only in its own queue row -- never in the panel.
    expect(screen.getAllByText('Patient A').length).toBe(1)

    await act(async () => {
      detailA.resolve(makeOrder({ order_id: 'order-a' }))
      await Promise.resolve()
    })

    // order-a's stale response must never land in the panel now showing order-b.
    expect(screen.getAllByText('Patient A').length).toBe(1)
    expect(screen.getAllByText('Patient B').length).toBe(2)
  })

  it('RR5: a late accept response for order A never mutates the now-visible order B', async () => {
    const acceptA = deferred<LabWorkflowOrder>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ order_id: 'order-a', patient: makePatient({ display_name: 'Patient A' }) }),
        makeOrder({ order_id: 'order-b', patient: makePatient({ display_name: 'Patient B' }) }),
      ]),
      getLabOrder: vi.fn((_labId: string, orderId: string) =>
        Promise.resolve(
          makeOrder({
            order_id: orderId,
            patient: makePatient({ display_name: orderId === 'order-a' ? 'Patient A' : 'Patient B' }),
          }),
        ),
      ),
      acceptLabOrder: vi.fn(() => acceptA.promise),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient A" }))
    await user.click(await screen.findByRole('button', { name: "Accepter l'ordre" })) // stalls on acceptA

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient B" }))
    // order-b's own detail is now open and still SELECTED -- its own accept action is available.
    await screen.findByRole('button', { name: "Accepter l'ordre" })

    await act(async () => {
      acceptA.resolve(makeOrder({ order_id: 'order-a', status: 'ACCEPTED' }))
    })

    // order-a's late accept must never mutate the panel now showing order-b: "Patient B" stays at
    // 2 (its own queue row + the still-open panel), "Patient A" stays at 1 (queue row only). A
    // regression of the §41 generation guard swaps the panel back to order-a's (now-ACCEPTED)
    // detail via the success path's own `loadPanel(orderId, {isRecheck:true})` call, which
    // inverts these counts -- proven by disabling the guard and confirming this exact assertion
    // then fails, rather than trusting a button-presence check the pre-fix code also satisfies
    // (its recheck fetch still returns SELECTED, so "Accepter l'ordre" stays visible either way).
    await waitFor(() => {
      expect(screen.getAllByText('Patient B').length).toBe(2)
      expect(screen.getAllByText('Patient A').length).toBe(1)
    })
  })

  it('RR6: a late reject response for order A never mutates the now-visible order B', async () => {
    const rejectA = deferred<LabWorkflowOrder>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ order_id: 'order-a', patient: makePatient({ display_name: 'Patient A' }) }),
        makeOrder({ order_id: 'order-b', patient: makePatient({ display_name: 'Patient B' }) }),
      ]),
      getLabOrder: vi.fn((_labId: string, orderId: string) =>
        Promise.resolve(
          makeOrder({
            order_id: orderId,
            patient: makePatient({ display_name: orderId === 'order-a' ? 'Patient A' : 'Patient B' }),
          }),
        ),
      ),
      rejectLabOrder: vi.fn(() => rejectA.promise),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient A" }))
    await user.click(await screen.findByRole('button', { name: "Refuser l'ordre" }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer le refus' })) // stalls on rejectA

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient B" }))
    await screen.findByRole('button', { name: "Refuser l'ordre" })

    await act(async () => {
      rejectA.resolve(makeOrder({ order_id: 'order-a', status: 'REJECTED' }))
    })

    // Same discriminating identity-count check as RR5, mirrored for reject -- see its own comment.
    await waitFor(() => {
      expect(screen.getAllByText('Patient B').length).toBe(2)
      expect(screen.getAllByText('Patient A').length).toBe(1)
    })
  })

  it('RR7: a response arriving after unmount produces no error and no state update', async () => {
    const queue = deferred<LabWorkflowOrder[]>()
    const client = makeClient({ listLabOrders: vi.fn().mockReturnValue(queue.promise) })
    const { unmount } = render(<StaffApp client={client} initialSection="orders" />)
    unmount()
    await act(async () => {
      queue.resolve([makeOrder()])
      await Promise.resolve()
    })
  })

  it('RR8: a detail response whose order_id disagrees with the request fails closed', async () => {
    const client = makeClient({
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ order_id: 'order-WRONG' })),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText('Impossible de vérifier de manière sûre cet ordre.')).toBeInTheDocument()
  })

  // -- CUI-group: collection UI (DH-LAB-002) -------------------------------------------------

  it('CUI1: ACCEPTED with no collection shows "Enregistrer la collecte"', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' })),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByRole('button', { name: 'Enregistrer la collecte' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Commencer l'analyse" })).not.toBeInTheDocument()
  })

  it('CUI2: a SELECTED order does not show any collection mutation control', async () => {
    const client = makeClient()
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('button', { name: "Accepter l'ordre" })
    expect(screen.queryByRole('button', { name: 'Enregistrer la collecte' })).not.toBeInTheDocument()
  })

  it('CUI3: a legacy IN_PROGRESS order with no recorded collection offers no collection mutation', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'IN_PROGRESS', collection_recorded_at: null }),
      ]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'IN_PROGRESS', collection_recorded_at: null }),
      ),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    // §49: truthful "not recorded," never a false claim collection did not happen. ("En cours"
    // itself is ambiguous -- it also labels the filter tab -- so wait on this unique text instead.
    await screen.findByText('Collecte non enregistrée dans DHealth')
    expect(screen.queryByRole('button', { name: 'Enregistrer la collecte' })).not.toBeInTheDocument()
    expect(screen.getAllByText('En cours').length).toBeGreaterThan(0)
  })

  it('CUI4: an existing collection timestamp is displayed', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText('Collecte enregistrée')).toBeInTheDocument()
  })

  it('CUI5/CUI6: collection requires explicit confirmation, and cancelling makes no request', async () => {
    const recordLabOrderCollection = vi.fn()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' })),
      recordLabOrderCollection,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))

    expect(await screen.findByText(/Enregistrer la collecte \?/)).toBeInTheDocument()
    expect(recordLabOrderCollection).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Annuler' }))
    expect(screen.queryByText(/Enregistrer la collecte \?/)).not.toBeInTheDocument()
    expect(recordLabOrderCollection).not.toHaveBeenCalled()
  })

  it('CUI7/CUI8: confirming calls the exact record-collection endpoint and re-reads authoritative state', async () => {
    const recordLabOrderCollection = vi.fn().mockResolvedValue(makeCollectionResponse())
    const getLabOrder = makeGetLabOrderRecheckMockForCollection()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder,
      recordLabOrderCollection,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' }))

    // **[Changed 2026-10-01, DHEALTH-SPECIMEN-001.** The call now carries an idempotency key, and
    // asserting its *presence and shape* is the point: without one the server falls back to its
    // keyless regime, where a second collection for this order would silently be answered as a
    // replay. The value is a generated UUID so it cannot be pinned literally -- what is pinned is
    // that one is sent, and that it is a UUID rather than something derived from clinical content.]**
    await waitFor(() => expect(recordLabOrderCollection).toHaveBeenCalledTimes(1))
    const [calledLab, calledOrder, calledOptions] = recordLabOrderCollection.mock.calls[0]
    expect(calledLab).toBe('lab-a')
    expect(calledOrder).toBe('order-a')
    expect(calledOptions.idempotencyKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    )
    expect(await screen.findByText('Collecte enregistrée')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Enregistrer la collecte' })).not.toBeInTheDocument()
  })

  it('CUI9: a collection response whose order_id disagrees fails closed', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' })),
      recordLabOrderCollection: vi.fn().mockResolvedValue(makeCollectionResponse({ order_id: 'order-DIFFERENT' })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' }))
    expect(await screen.findByText('Impossible de vérifier de manière sûre cet ordre.')).toBeInTheDocument()
  })

  it('CUI10: no free-text, time, or specimen field exists anywhere in the panel', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await screen.findByText(/Enregistrer la collecte \?/)
    expect(screen.queryAllByRole('textbox')).toHaveLength(0)
    expect(screen.queryAllByRole('spinbutton')).toHaveLength(0)
  })

  // -- PUI-group: processing UI (DH-LAB-002) -------------------------------------------------

  it('PUI1: ACCEPTED with no collection does not show start-processing', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' })),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('button', { name: 'Enregistrer la collecte' })
    expect(screen.queryByRole('button', { name: "Commencer l'analyse" })).not.toBeInTheDocument()
  })

  it('PUI2: ACCEPTED with collection recorded shows start-processing', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByRole('button', { name: "Commencer l'analyse" })).toBeInTheDocument()
  })

  it('PUI3: start-processing requires explicit confirmation', async () => {
    const startProcessingLabOrder = vi.fn()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ),
      startProcessingLabOrder,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))

    expect(await screen.findByText(/Commencer l'analyse \?/)).toBeInTheDocument()
    expect(startProcessingLabOrder).not.toHaveBeenCalled()
  })

  it('PUI4/PUI6/PUI7: confirming calls the exact start-processing endpoint and the order becomes IN_PROGRESS', async () => {
    const startProcessingLabOrder = vi.fn().mockResolvedValue(
      makeOrder({ status: 'IN_PROGRESS', collection_recorded_at: '2026-09-18T10:42:00Z' }),
    )
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ]),
      getLabOrder: makeGetLabOrderRecheckWithCollection('IN_PROGRESS', '2026-09-18T10:42:00Z', {
        initialCollectionRecordedAt: '2026-09-18T10:42:00Z',
      }),
      startProcessingLabOrder,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await user.click(await screen.findByRole('button', { name: 'Commencer' }))

    await waitFor(() => expect(startProcessingLabOrder).toHaveBeenCalledWith('lab-a', 'order-a'))
    await waitFor(() => expect(screen.getAllByText('En cours').length).toBeGreaterThan(0))
    expect(screen.queryByRole('button', { name: "Commencer l'analyse" })).not.toBeInTheDocument()
  })

  it('PUI5: a start-processing response whose order_id disagrees fails closed', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ),
      startProcessingLabOrder: vi
        .fn()
        .mockResolvedValue(makeOrder({ order_id: 'order-DIFFERENT', status: 'IN_PROGRESS' })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await user.click(await screen.findByRole('button', { name: 'Commencer' }))
    expect(await screen.findByText('Impossible de vérifier de manière sûre cet ordre.')).toBeInTheDocument()
  })

  it('PUI8: once IN_PROGRESS, the controlled Native entry form loads -- never a release control', async () => {
    // DH-LAB-002's original invariant here ("no result-entry control appears once IN_PROGRESS")
    // predates DH-LAB-003/004's own controlled entry form and no longer holds -- the entry form
    // is now the expected, correct content for this state (see the UI-group below for full
    // coverage). What remains true and worth asserting here: no *release* control ever appears
    // on this page (release lives only on the dedicated Libération surface, DH-LAB-004).
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'IN_PROGRESS', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'IN_PROGRESS', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText('Saisie des résultats')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Libérer/i })).not.toBeInTheDocument()
  })

  // -- LR-C/LR-P-group: lost-response recovery (DH-LAB-002) ---------------------------------

  it('LR-C1: collection commits but the response is lost -- recheck shows success, no error', async () => {
    // **[Rewritten 2026-10-01, DHEALTH-SPECIMEN-001.** The scenario is unchanged -- the POST lands
    // server-side and the response is lost -- but the predicate that decides "my request won" is
    // not. It used to be `collection_recorded_at !== null`, a fact about the *order*, which stops
    // being a valid success signal the moment a second collection is recordable: after a
    // recollection that field is already non-null from the first collection, so an uncertain second
    // POST would have been reported as success whether it landed or not.
    //
    // It is now "a specimen carrying **my own** idempotency key exists", read back from the server.
    // The mock below therefore echoes the key the component actually sent, which is what a server
    // that received the request would do. It cannot pass by accident: an empty specimen list makes
    // this same assertion red, which is precisely LR-C2's scenario below.]**
    const recordLabOrderCollection = vi.fn().mockRejectedValue(new Error('network lost'))
    const listLabOrderSpecimens = vi.fn(() => {
      const sentKey = recordLabOrderCollection.mock.calls[0]?.[2]?.idempotencyKey
      return Promise.resolve(sentKey ? [makeSpecimen({ idempotency_key: sentKey })] : [])
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: makeGetLabOrderRecheckWithCollection('ACCEPTED', '2026-09-18T10:42:00Z'),
      recordLabOrderCollection,
      listLabOrderSpecimens,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' }))

    // The specimen history is what now shows the recorded collection, and it shows exactly one --
    // the lost response did not become a second physical specimen.
    await waitFor(() => expect(screen.getByText('Prélèvements (1)')).toBeInTheDocument())
    expect(screen.getByText(/1\. Prélèvement enregistré/)).toBeInTheDocument()
    expect(
      screen.queryByText("L'action n'a pas pu être confirmée. Vous pouvez réessayer."),
    ).not.toBeInTheDocument()
    // Recovery is a read, never a blind re-POST: exactly one collection request was ever sent.
    expect(recordLabOrderCollection).toHaveBeenCalledTimes(1)
  })

  it('LR-C2: collection response lost, order still ACCEPTED/null -- explicit retry stays available', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockImplementation((_labId, orderId) =>
        Promise.resolve(makeOrder({ order_id: orderId, status: 'ACCEPTED', collection_recorded_at: null })),
      ),
      recordLabOrderCollection: vi.fn().mockRejectedValue(new Error('timeout')),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' }))

    expect(await screen.findByText("L'action n'a pas pu être confirmée. Vous pouvez réessayer.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enregistrer la collecte' })).toBeInTheDocument()
  })

  // ── DHEALTH-SPECIMEN-001 ──────────────────────────────────────────────────────────────────────

  it('SP-UI-1: an unconfirmed collection is retried with the SAME idempotency key', async () => {
    // The single most load-bearing client-side assertion in this ticket.
    //
    // A key minted per *request* instead of per *intended collection* would send a different key on
    // the retry, and a first POST that landed late would then have become a second physical
    // specimen -- the exact double-click failure the key exists to prevent, and one no backend test
    // can catch because the server would be correctly honouring two genuinely different keys.
    //
    // This test is what pins `??=` rather than `=` in both confirmation handlers.
    const recordLabOrderCollection = vi.fn().mockRejectedValue(new Error('network lost'))
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockImplementation((_labId: string, orderId: string) =>
        Promise.resolve(
          makeOrder({ order_id: orderId, status: 'ACCEPTED', collection_recorded_at: null }),
        ),
      ),
      // Empty: the server never recorded it, so recovery lands in the "unconfirmed" branch and the
      // retry control stays offered.
      listLabOrderSpecimens: vi.fn().mockResolvedValue([]),
      recordLabOrderCollection,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' }))
    expect(
      await screen.findByText("L'action n'a pas pu être confirmée. Vous pouvez réessayer."),
    ).toBeInTheDocument()

    // The user tries again, through the control that is still offered.
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' }))

    await waitFor(() => expect(recordLabOrderCollection).toHaveBeenCalledTimes(2))
    const firstKey = recordLabOrderCollection.mock.calls[0][2].idempotencyKey
    const secondKey = recordLabOrderCollection.mock.calls[1][2].idempotencyKey
    expect(firstKey).toBeTruthy()
    expect(secondKey).toBe(firstKey)
  })

  it('SP-UI-2: a settled collection is followed by a NEW key, so a real second sample is not a replay', async () => {
    // The complement of SP-UI-1, and the reason the ref is cleared on success rather than left set.
    // If the key survived a confirmed collection, a genuinely new physical sample would be sent with
    // the key the server already knows and would be answered as a replay -- silently discarding a
    // real specimen, which is worse than a duplicate.
    const recordLabOrderCollection = vi.fn().mockResolvedValue(makeCollectionResponse())
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockImplementation((_labId: string, orderId: string) =>
        Promise.resolve(
          makeOrder({
            order_id: orderId,
            status: 'ACCEPTED',
            collection_recorded_at: '2026-09-18T10:42:00Z',
          }),
        ),
      ),
      listLabOrderSpecimens: vi.fn().mockResolvedValue([makeSpecimen()]),
      recordLabOrderCollection,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    // The order already carries a milestone, so the *additional* specimen control is the one offered.
    await user.click(
      await screen.findByRole('button', { name: 'Enregistrer un nouveau prélèvement' }),
    )
    await user.click(await screen.findByRole('button', { name: 'Confirmer le prélèvement' }))
    await waitFor(() => expect(recordLabOrderCollection).toHaveBeenCalledTimes(1))

    await user.click(
      await screen.findByRole('button', { name: 'Enregistrer un nouveau prélèvement' }),
    )
    await user.click(await screen.findByRole('button', { name: 'Confirmer le prélèvement' }))
    await waitFor(() => expect(recordLabOrderCollection).toHaveBeenCalledTimes(2))

    const firstKey = recordLabOrderCollection.mock.calls[0][2].idempotencyKey
    const secondKey = recordLabOrderCollection.mock.calls[1][2].idempotencyKey
    expect(firstKey).toBeTruthy()
    expect(secondKey).not.toBe(firstKey)
  })

  it('SP-UI-3: two specimens render oldest-first, and nothing calls the second a recollection', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'IN_PROGRESS' })]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'IN_PROGRESS', collection_recorded_at: '2026-09-18T09:12:00Z' }),
      ),
      listLabOrderSpecimens: vi.fn().mockResolvedValue([
        makeSpecimen({ id: 'specimen-1', recorded_at: '2026-09-18T09:12:00Z' }),
        makeSpecimen({ id: 'specimen-2', recorded_at: '2026-09-18T09:46:00Z' }),
      ]),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByText('Prélèvements (2)')).toBeInTheDocument()
    const rows = screen.getAllByText(/Prélèvement enregistré/)
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toMatch(/^1\./)
    expect(rows[1].textContent).toMatch(/^2\./)

    // The wording the domain is entitled to, and nothing beyond it. DHealth does not know *why* a
    // second sample was recorded -- an order may legitimately need more than one -- so no row may
    // imply the first was rejected or replaced. "Prélèvement effectué" is excluded for a different
    // reason: the server recorded that someone entered it, never that collection physically
    // happened at that instant.
    const panel = document.body.textContent ?? ''
    for (const forbidden of [
      'recollecte',
      'Recollecte',
      'remplacement',
      'Prélèvement rejeté',
      'Prélèvement refusé',
      'Prélèvement effectué',
      'Nouveau prélèvement enregistré',
    ]) {
      expect(panel).not.toContain(forbidden)
    }
  })

  it('SP-UI-4: the single milestone timestamp is NOT shown beside a specimen history', async () => {
    // Two displays of an overlapping fact. For the first collection they are the same instant by
    // construction; after a second one the milestone line would keep showing the *first* instant
    // next to a two-row history, which reads as a contradiction.
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T09:12:00Z' }),
      ),
      listLabOrderSpecimens: vi.fn().mockResolvedValue([makeSpecimen()]),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByText('Prélèvements (1)')).toBeInTheDocument()
    expect(screen.queryByText('Collecte enregistrée')).not.toBeInTheDocument()
  })

  it('SP-UI-5: a pre-specimen order keeps the milestone wording and claims no specimen count', async () => {
    // The honest historical case: a collection recorded before specimens were modelled. The
    // milestone is the only truth, and how many physical samples it stood for is unknown -- so
    // "Prélèvements (0)" would be a false claim and must not appear.
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T09:12:00Z' }),
      ),
      listLabOrderSpecimens: vi.fn().mockResolvedValue([]),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByText('Collecte enregistrée')).toBeInTheDocument()
    expect(screen.queryByText('Prélèvements (0)')).not.toBeInTheDocument()
    expect(screen.queryByText(/Prélèvements \(/)).not.toBeInTheDocument()
  })

  it('SP-UI-6: the additional-specimen control appears only where the server would accept it', async () => {
    // Status-driven, exactly as every other action on this page is. Hiding a control that would be
    // refused is usability; the boundary stays server-side, which is why the backend suite asserts
    // the same four refusals independently.
    const cases: Array<{
      status: LabWorkflowOrder['status']
      collection: string | null
      offered: boolean
    }> = [
      { status: 'ACCEPTED', collection: '2026-09-18T09:12:00Z', offered: true },
      { status: 'IN_PROGRESS', collection: '2026-09-18T09:12:00Z', offered: true },
      // No milestone yet: the *primary* "Enregistrer la collecte" action is the right control.
      { status: 'ACCEPTED', collection: null, offered: false },
      { status: 'SELECTED', collection: null, offered: false },
      { status: 'COMPLETED', collection: '2026-09-18T09:12:00Z', offered: false },
      { status: 'REJECTED', collection: null, offered: false },
    ]

    for (const { status, collection, offered } of cases) {
      const client = makeClient({
        listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status })]),
        getLabOrder: vi
          .fn()
          .mockResolvedValue(makeOrder({ status, collection_recorded_at: collection })),
        listLabOrderSpecimens: vi.fn().mockResolvedValue(collection ? [makeSpecimen()] : []),
      })
      const user = userEvent.setup()
      const { unmount } = render(<StaffApp client={client} initialSection="orders" />)
      await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
      await waitFor(() => expect(screen.getByText(/Établissement prescripteur/)).toBeInTheDocument())

      const control = screen.queryByRole('button', { name: 'Enregistrer un nouveau prélèvement' })
      if (offered) {
        expect(control, `expected the control for ${status}/${collection}`).toBeInTheDocument()
      } else {
        expect(control, `did not expect the control for ${status}/${collection}`).toBeNull()
      }
      unmount()
    }
  })

  it('SP-UI-7: a blocked order offers no collection control at all', async () => {
    // BLOCKED means this laboratory cannot currently continue, and DHEALTH-LAB-BLOCKED-001's rule
    // that every continuation refuses must not have been punched through by this ticket.
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'BLOCKED' })]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({
          status: 'BLOCKED',
          collection_recorded_at: '2026-09-18T09:12:00Z',
          active_block: makeBlock(),
        }),
      ),
      listLabOrderSpecimens: vi.fn().mockResolvedValue([makeSpecimen()]),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await waitFor(() => expect(screen.getByText('Prélèvements (1)')).toBeInTheDocument())

    expect(
      screen.queryByRole('button', { name: 'Enregistrer un nouveau prélèvement' }),
    ).toBeNull()
    expect(screen.queryByRole('button', { name: 'Enregistrer la collecte' })).toBeNull()
  })

  it('SP-UI-8: the specimen history is read per opened order, and a failure is reported not hidden', async () => {
    const listLabOrderSpecimens = vi.fn().mockRejectedValue(new Error('history unavailable'))
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T09:12:00Z' }),
      ),
      listLabOrderSpecimens,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(
      await screen.findByText('Impossible de charger les prélèvements de cet ordre.'),
    ).toBeInTheDocument()
    // A failed history read must not be rendered as "no specimens": the milestone line is shown
    // instead, which is the one thing still known to be true.
    expect(screen.queryByText(/Prélèvements \(/)).not.toBeInTheDocument()
    expect(screen.getByText('Collecte enregistrée')).toBeInTheDocument()
    expect(listLabOrderSpecimens).toHaveBeenCalledWith('lab-a', 'order-a', expect.anything())
  })

  it('LR-C3: collection response lost, order moved on -- current state shown, never fabricated', async () => {
    let calls = 0
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      // First call is the initial panel open (must be ACCEPTED, no collection, so the button can
      // be clicked); every later call is the post-failure recheck, reporting the order moved on
      // with no collection ever recorded.
      getLabOrder: vi.fn().mockImplementation((_labId, orderId) => {
        calls += 1
        return Promise.resolve(
          makeOrder({
            order_id: orderId,
            status: calls === 1 ? 'ACCEPTED' : 'CANCELLED',
            collection_recorded_at: null,
          }),
        )
      }),
      recordLabOrderCollection: vi.fn().mockRejectedValue(new Error('conflict')),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' }))

    await waitFor(() => expect(screen.getAllByText('Annulé').length).toBeGreaterThan(0))
    expect(
      screen.getByText("Cet ordre a déjà été traité différemment -- l'état actuel est affiché ci-dessous."),
    ).toBeInTheDocument()
    expect(screen.getByText('Collecte non enregistrée dans DHealth')).toBeInTheDocument()
  })

  it('LR-P1: processing commits but the response is lost -- recheck shows success, no error', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ]),
      getLabOrder: vi
        .fn()
        .mockResolvedValue(makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' })),
      startProcessingLabOrder: vi.fn().mockRejectedValue(new Error('network lost')),
    })
    client.getLabOrder = makeGetLabOrderRecheckWithCollection('IN_PROGRESS', '2026-09-18T10:42:00Z', {
      initialCollectionRecordedAt: '2026-09-18T10:42:00Z',
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await user.click(await screen.findByRole('button', { name: 'Commencer' }))

    await waitFor(() => expect(screen.getAllByText('En cours').length).toBeGreaterThan(0))
    expect(
      screen.queryByText("L'action n'a pas pu être confirmée. Vous pouvez réessayer."),
    ).not.toBeInTheDocument()
  })

  it('LR-P2: processing ambiguous, ACCEPTED + collection present -- explicit retry stays available', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ]),
      getLabOrder: vi.fn().mockImplementation((_labId, orderId) =>
        Promise.resolve(
          makeOrder({ order_id: orderId, status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
        ),
      ),
      startProcessingLabOrder: vi.fn().mockRejectedValue(new Error('timeout')),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await user.click(await screen.findByRole('button', { name: 'Commencer' }))

    expect(await screen.findByText("L'action n'a pas pu être confirmée. Vous pouvez réessayer.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Commencer l'analyse" })).toBeInTheDocument()
  })

  it('LR-P3: processing ambiguous, ACCEPTED + no collection -- prerequisite not met, no auto-retry', async () => {
    let calls = 0
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ]),
      // First call is the initial panel open (collection present, so "Commencer l'analyse" can be
      // clicked); the recheck after the ambiguous failure reports the milestone gone -- proving
      // this branch reads the fresh authoritative state, never the stale panel it started from.
      getLabOrder: vi.fn().mockImplementation((_labId, orderId) => {
        calls += 1
        return Promise.resolve(
          makeOrder({
            order_id: orderId,
            status: 'ACCEPTED',
            collection_recorded_at: calls === 1 ? '2026-09-18T10:42:00Z' : null,
          }),
        )
      }),
      startProcessingLabOrder: vi.fn().mockRejectedValue(new Error('timeout')),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await user.click(await screen.findByRole('button', { name: 'Commencer' }))

    expect(
      await screen.findByText("La collecte n'a pas été enregistrée dans DHealth -- l'analyse ne peut pas commencer avant."),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enregistrer la collecte' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Commencer l'analyse" })).not.toBeInTheDocument()
  })

  // -- RR-C/RR-P/RR-L/RR-U-group: race/stale-response safety (DH-LAB-002) -------------------

  it('RR-C1: a late collection response for order A never mutates the now-visible order B', async () => {
    const collectA = deferred<LabCollectionResponse>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ order_id: 'order-a', status: 'ACCEPTED', patient: makePatient({ display_name: 'Patient A' }) }),
        makeOrder({ order_id: 'order-b', status: 'ACCEPTED', patient: makePatient({ display_name: 'Patient B' }) }),
      ]),
      getLabOrder: vi.fn((_labId: string, orderId: string) =>
        Promise.resolve(
          makeOrder({
            order_id: orderId,
            status: 'ACCEPTED',
            patient: makePatient({ display_name: orderId === 'order-a' ? 'Patient A' : 'Patient B' }),
          }),
        ),
      ),
      recordLabOrderCollection: vi.fn(() => collectA.promise),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient A" }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' })) // stalls

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient B" }))
    await screen.findByRole('button', { name: 'Enregistrer la collecte' })

    await act(async () => {
      collectA.resolve(makeCollectionResponse({ order_id: 'order-a' }))
    })

    await waitFor(() => {
      expect(screen.getAllByText('Patient B').length).toBe(2)
      expect(screen.getAllByText('Patient A').length).toBe(1)
    })
  })

  it('RR-C2: a late collection recheck for order A never mutates the now-visible order B', async () => {
    const recheckA = deferred<LabWorkflowOrder>()
    let callsForA = 0
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ order_id: 'order-a', status: 'ACCEPTED', patient: makePatient({ display_name: 'Patient A' }) }),
        makeOrder({ order_id: 'order-b', status: 'ACCEPTED', patient: makePatient({ display_name: 'Patient B' }) }),
      ]),
      // order-a's first getLabOrder call is the initial panel open (must resolve so the
      // "Enregistrer la collecte" button can be clicked); its second call is the post-failure
      // recheck, which stalls until resolved below.
      getLabOrder: vi.fn((_labId: string, orderId: string) => {
        if (orderId === 'order-a') {
          callsForA += 1
          if (callsForA === 1) {
            return Promise.resolve(
              makeOrder({ order_id: 'order-a', status: 'ACCEPTED', patient: makePatient({ display_name: 'Patient A' }) }),
            )
          }
          return recheckA.promise
        }
        return Promise.resolve(
          makeOrder({ order_id: 'order-b', status: 'ACCEPTED', patient: makePatient({ display_name: 'Patient B' }) }),
        )
      }),
      recordLabOrderCollection: vi.fn().mockRejectedValue(new Error('network lost')),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient A" }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' })) // fails, stalls on recheck

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient B" }))
    await screen.findByRole('button', { name: 'Enregistrer la collecte' })

    await act(async () => {
      recheckA.resolve(makeOrder({ order_id: 'order-a', status: 'ACCEPTED', collection_recorded_at: '2026-09-18T10:42:00Z' }))
    })

    await waitFor(() => {
      expect(screen.getAllByText('Patient B').length).toBe(2)
      expect(screen.getAllByText('Patient A').length).toBe(1)
    })
  })

  it('RR-P1: a late processing response for order A never mutates the now-visible order B', async () => {
    const startA = deferred<LabWorkflowOrder>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({
          order_id: 'order-a',
          status: 'ACCEPTED',
          collection_recorded_at: '2026-09-18T10:42:00Z',
          patient: makePatient({ display_name: 'Patient A' }),
        }),
        makeOrder({
          order_id: 'order-b',
          status: 'ACCEPTED',
          collection_recorded_at: '2026-09-18T10:42:00Z',
          patient: makePatient({ display_name: 'Patient B' }),
        }),
      ]),
      getLabOrder: vi.fn((_labId: string, orderId: string) =>
        Promise.resolve(
          makeOrder({
            order_id: orderId,
            status: 'ACCEPTED',
            collection_recorded_at: '2026-09-18T10:42:00Z',
            patient: makePatient({ display_name: orderId === 'order-a' ? 'Patient A' : 'Patient B' }),
          }),
        ),
      ),
      startProcessingLabOrder: vi.fn(() => startA.promise),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient A" }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await user.click(await screen.findByRole('button', { name: 'Commencer' })) // stalls

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient B" }))
    await screen.findByRole('button', { name: "Commencer l'analyse" })

    await act(async () => {
      startA.resolve(makeOrder({ order_id: 'order-a', status: 'IN_PROGRESS', collection_recorded_at: '2026-09-18T10:42:00Z' }))
    })

    await waitFor(() => {
      expect(screen.getAllByText('Patient B').length).toBe(2)
      expect(screen.getAllByText('Patient A').length).toBe(1)
    })
  })

  it('RR-P2: a late processing recheck for order A never mutates the now-visible order B', async () => {
    const recheckA = deferred<LabWorkflowOrder>()
    let callsForA = 0
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({
          order_id: 'order-a',
          status: 'ACCEPTED',
          collection_recorded_at: '2026-09-18T10:42:00Z',
          patient: makePatient({ display_name: 'Patient A' }),
        }),
        makeOrder({
          order_id: 'order-b',
          status: 'ACCEPTED',
          collection_recorded_at: '2026-09-18T10:42:00Z',
          patient: makePatient({ display_name: 'Patient B' }),
        }),
      ]),
      // order-a's first getLabOrder call is the initial panel open; its second call is the
      // post-failure recheck, which stalls until resolved below.
      getLabOrder: vi.fn((_labId: string, orderId: string) => {
        if (orderId === 'order-a') {
          callsForA += 1
          if (callsForA === 1) {
            return Promise.resolve(
              makeOrder({
                order_id: 'order-a',
                status: 'ACCEPTED',
                collection_recorded_at: '2026-09-18T10:42:00Z',
                patient: makePatient({ display_name: 'Patient A' }),
              }),
            )
          }
          return recheckA.promise
        }
        return Promise.resolve(
          makeOrder({
            order_id: 'order-b',
            status: 'ACCEPTED',
            collection_recorded_at: '2026-09-18T10:42:00Z',
            patient: makePatient({ display_name: 'Patient B' }),
          }),
        )
      }),
      startProcessingLabOrder: vi.fn().mockRejectedValue(new Error('network lost')),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient A" }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await user.click(await screen.findByRole('button', { name: 'Commencer' })) // fails, stalls on recheck

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient B" }))
    await screen.findByRole('button', { name: "Commencer l'analyse" })

    await act(async () => {
      recheckA.resolve(
        makeOrder({ order_id: 'order-a', status: 'IN_PROGRESS', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      )
    })

    await waitFor(() => {
      expect(screen.getAllByText('Patient B').length).toBe(2)
      expect(screen.getAllByText('Patient A').length).toBe(1)
    })
  })

  it('RR-L1: switching labs invalidates an outstanding collection action', async () => {
    const collectStall = deferred<LabCollectionResponse>()
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue(meWithTwoLabs),
      listLabOrders: vi.fn((labId: string) =>
        Promise.resolve([
          makeOrder({
            order_id: labId === 'lab-a' ? 'order-a' : 'order-b',
            status: 'ACCEPTED',
            patient: makePatient({ display_name: labId === 'lab-a' ? 'Patient Lab A' : 'Patient Lab B' }),
          }),
        ]),
      ),
      getLabOrder: vi.fn((labId: string, orderId: string) =>
        Promise.resolve(
          makeOrder({
            order_id: orderId,
            status: 'ACCEPTED',
            patient: makePatient({ display_name: labId === 'lab-a' ? 'Patient Lab A' : 'Patient Lab B' }),
          }),
        ),
      ),
      recordLabOrderCollection: vi.fn(() => collectStall.promise),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' })) // stalls

    const select = await screen.findByLabelText('Établissement actif')
    await user.selectOptions(select, 'lab-b')
    await screen.findByText('Patient Lab B')

    await act(async () => {
      collectStall.resolve(makeCollectionResponse({ order_id: 'order-a' }))
    })

    expect(screen.queryByText('Patient Lab A')).not.toBeInTheDocument()
    expect(screen.getByText('Patient Lab B')).toBeInTheDocument()
  })

  it('RR-L2: switching labs invalidates an outstanding processing action', async () => {
    const startStall = deferred<LabWorkflowOrder>()
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue(meWithTwoLabs),
      listLabOrders: vi.fn((labId: string) =>
        Promise.resolve([
          makeOrder({
            order_id: labId === 'lab-a' ? 'order-a' : 'order-b',
            status: 'ACCEPTED',
            collection_recorded_at: '2026-09-18T10:42:00Z',
            patient: makePatient({ display_name: labId === 'lab-a' ? 'Patient Lab A' : 'Patient Lab B' }),
          }),
        ]),
      ),
      getLabOrder: vi.fn((labId: string, orderId: string) =>
        Promise.resolve(
          makeOrder({
            order_id: orderId,
            status: 'ACCEPTED',
            collection_recorded_at: '2026-09-18T10:42:00Z',
            patient: makePatient({ display_name: labId === 'lab-a' ? 'Patient Lab A' : 'Patient Lab B' }),
          }),
        ),
      ),
      startProcessingLabOrder: vi.fn(() => startStall.promise),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await user.click(await screen.findByRole('button', { name: 'Commencer' })) // stalls

    const select = await screen.findByLabelText('Établissement actif')
    await user.selectOptions(select, 'lab-b')
    await screen.findByText('Patient Lab B')

    await act(async () => {
      startStall.resolve(makeOrder({ order_id: 'order-a', status: 'IN_PROGRESS' }))
    })

    expect(screen.queryByText('Patient Lab A')).not.toBeInTheDocument()
    expect(screen.getByText('Patient Lab B')).toBeInTheDocument()
  })

  it('RR-U1: a collection/processing response arriving after unmount produces no error and no state update', async () => {
    const collectStall = deferred<LabCollectionResponse>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' })),
      recordLabOrderCollection: vi.fn(() => collectStall.promise),
    })
    const user = userEvent.setup()
    const { unmount } = render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' })) // stalls

    unmount()
    await act(async () => {
      collectStall.resolve(makeCollectionResponse())
      await Promise.resolve()
    })
  })

  // -- UI-group: controlled Native result-entry form (DH-LAB-004) ---------------------------

  function inProgressOrder(overrides: Partial<LabWorkflowOrder> = {}): LabWorkflowOrder {
    return makeOrder({
      status: 'IN_PROGRESS',
      collection_recorded_at: '2026-09-18T10:42:00Z',
      ...overrides,
    })
  }

  it('UI1/UI2: an IN_PROGRESS order loads the workspace and renders definitions in sort order', async () => {
    const workspace = makeResultEntryWorkspace({
      items: [
        {
          item_id: 'item-a',
          catalog_code_snapshot: 'GLU',
          catalog_display_name_snapshot: 'Glycémie à jeun',
          template: {
            template_id: 'template-a',
            version: 1,
            is_synthetic: false,
            // The backend's own `_to_template_record` always sorts by `sort_order` before this
            // response is ever built -- the array itself already arrives in sort order (0 then
            // 1), matching what a real request would return. This test asserts the frontend
            // preserves that order when rendering, never reorders or reverses it.
            observations: [
              {
                definition_id: 'def-1',
                source_code: 'DEMO_A',
                display_label: 'First Measure',
                result_type: 'TEXT',
                numeric_unit: null,
                required: true,
                sort_order: 0,
              },
              {
                definition_id: 'def-2',
                source_code: 'DEMO_B',
                display_label: 'Second Measure',
                result_type: 'NUMERIC',
                numeric_unit: null,
                required: false,
                sort_order: 1,
              },
            ],
          },
          template_pinned: false,
          entry_mode: 'NATIVE_TEMPLATE',
          entry_completed: false,
          entry_completed_at: null,
          entered_values: [],
        },
      ],
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryWorkspace: vi.fn().mockResolvedValue(workspace),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Saisie des résultats')

    const fields = await screen.findAllByLabelText(/Measure/)
    expect(fields).toHaveLength(2)
    // Rendering order, not merely presence: "First Measure"'s own field must precede "Second
    // Measure"'s in document order.
    expect(
      fields[0].compareDocumentPosition(fields[1]) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(await screen.findByLabelText(/First Measure/)).toBeInTheDocument()
    expect(await screen.findByLabelText(/Second Measure/)).toBeInTheDocument()
  })

  it('UI3/UI4/UI5/UI6: result type, unit, and required/optional are shown truthfully', async () => {
    const workspace = makeResultEntryWorkspace({
      items: [
        {
          item_id: 'item-a',
          catalog_code_snapshot: 'GLU',
          catalog_display_name_snapshot: 'Glycémie à jeun',
          template: {
            template_id: 'template-a',
            version: 1,
            is_synthetic: false,
            observations: [
              {
                definition_id: 'def-1',
                source_code: 'DEMO_A',
                display_label: 'Numeric Measure',
                result_type: 'NUMERIC',
                numeric_unit: 'mg/dL',
                required: true,
                sort_order: 0,
              },
              {
                definition_id: 'def-2',
                source_code: 'DEMO_B',
                display_label: 'Text Measure',
                result_type: 'TEXT',
                numeric_unit: null,
                required: false,
                sort_order: 1,
              },
            ],
          },
          template_pinned: false,
          entry_mode: 'NATIVE_TEMPLATE',
          entry_completed: false,
          entry_completed_at: null,
          entered_values: [],
        },
      ],
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryWorkspace: vi.fn().mockResolvedValue(workspace),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByLabelText(/Numeric Measure \(Requis\) — mg\/dL/)).toBeInTheDocument()
    expect(await screen.findByLabelText(/Text Measure \(Facultatif\)/)).toBeInTheDocument()
  })

  it('UI7: an unentered definition renders as a labeled, editable input -- never hardcoded', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    const input = await screen.findByLabelText(/Demo Measure A \(Requis\) — demo-unit/)
    expect(input).toBeInTheDocument()
    expect(input.tagName).toBe('INPUT')
  })

  it('UI8: no reference range is displayed anywhere in the entry form', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Saisie des résultats')
    // ("Référence de l'ordre" is a pre-existing, unrelated label -- match the clinical-range
    // terms specifically, not the substring "référence" alone.)
    expect(
      screen.queryByText(/plage de référence|valeur de référence|intervalle de référence/i),
    ).not.toBeInTheDocument()
  })

  it('UI9: an unstarted item with no active template shows a truthful blocking message', async () => {
    const workspace = makeResultEntryWorkspace({
      items: [
        {
          item_id: 'item-a',
          catalog_code_snapshot: 'GLU',
          catalog_display_name_snapshot: 'Glycémie à jeun',
          template: null,
          template_pinned: false,
          entry_mode: 'UNSTARTED_NO_TEMPLATE',
          entry_completed: false,
          entry_completed_at: null,
          entered_values: [],
        },
      ],
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryWorkspace: vi.fn().mockResolvedValue(workspace),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText(
        "Aucune structure de résultat n'est configurée pour cette analyse -- la saisie native n'est pas disponible.",
      ),
    ).toBeInTheDocument()
  })

  it('UI10: an already-entered value is shown read-only, never as an editable input', async () => {
    const workspace = makeResultEntryWorkspace({
      items: [
        {
          item_id: 'item-a',
          catalog_code_snapshot: 'GLU',
          catalog_display_name_snapshot: 'Glycémie à jeun',
          template: {
            template_id: 'template-a',
            version: 1,
            is_synthetic: true,
            observations: [
              {
                definition_id: 'def-a',
                source_code: 'DEMO_A',
                display_label: 'Demo Measure A',
                result_type: 'NUMERIC',
                numeric_unit: 'demo-unit',
                required: true,
                sort_order: 0,
              },
            ],
          },
          template_pinned: true,
          entry_mode: 'NATIVE_TEMPLATE',
          entry_completed: false,
          entry_completed_at: null,
          entered_values: [
            {
              definition_id: 'def-a',
              observation_id: 'obs-1',
              numeric_value: '7.2000',
              text_value: null,
              recorded_at: '2026-09-18T10:50:00Z',
            },
          ],
        },
      ],
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryWorkspace: vi.fn().mockResolvedValue(workspace),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText('7.2000 demo-unit')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Demo Measure A/)).not.toBeInTheDocument()
  })

  it('UI11: a synthetic template displays the demo warning', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText(/Configuration de démonstration — ne pas utiliser pour des soins réels\./),
    ).toBeInTheDocument()
  })

  it('UI12: Patient identity remains visible alongside the entry form', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Saisie des résultats')
    expect(screen.getAllByText('Fatouma Robleh').length).toBeGreaterThan(0)
  })

  it('UI13: "Enregistrer les valeurs" is disabled with no draft and enabled once a value is typed', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    const saveButton = await screen.findByRole('button', { name: 'Enregistrer les valeurs' })
    expect(saveButton).toBeDisabled()
    await user.type(await screen.findByLabelText(/Demo Measure A/), '7.2')
    expect(saveButton).toBeEnabled()
  })

  it('UI14: saving posts only template identity, definition identity, and value -- never structure', async () => {
    const recordNativeResultValues = vi.fn().mockResolvedValue({
      order_id: 'order-a',
      item_id: 'item-a',
      result_id: 'result-1',
      template_id: 'template-a',
      values: [
        {
          definition_id: 'def-a',
          observation_id: 'obs-1',
          numeric_value: '7.2000',
          text_value: null,
          recorded_at: '2026-09-18T10:50:00Z',
          created: true,
        },
      ],
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      recordNativeResultValues,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.type(await screen.findByLabelText(/Demo Measure A/), '7.2')
    await user.click(await screen.findByRole('button', { name: 'Enregistrer les valeurs' }))

    await waitFor(() => expect(recordNativeResultValues).toHaveBeenCalledTimes(1))
    expect(recordNativeResultValues).toHaveBeenCalledWith('lab-a', 'order-a', 'item-a', {
      template_id: 'template-a',
      values: [{ definition_id: 'def-a', numeric_value: '7.2' }],
    })
  })


  /*
   * DH-USABILITY-001 §18 — result entry without reaching for the mouse.
   *
   * A technician types a value and then has to find and click a button. §18 asks whether the
   * whole entry can be done from the keyboard; it could not. Enter now does exactly what the
   * primary button beside the field does, which is the only version of this worth having: a key
   * that does something the visible control does not would be worse than no key at all.
   */
  it('UI-KB1: pressing Enter in an entry field records the values, exactly as the button does', async () => {
    const recordNativeResultValues = vi.fn().mockResolvedValue({
      order_id: 'order-a',
      item_id: 'item-a',
      result_id: 'result-1',
      template_id: 'template-a',
      values: [
        {
          definition_id: 'def-a',
          observation_id: 'obs-1',
          numeric_value: '7.2000',
          text_value: null,
          recorded_at: '2026-09-18T10:50:00Z',
          created: true,
        },
      ],
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      recordNativeResultValues,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.type(await screen.findByLabelText(/Demo Measure A/), '7.2{Enter}')

    await waitFor(() => expect(recordNativeResultValues).toHaveBeenCalledTimes(1))
    expect(recordNativeResultValues).toHaveBeenCalledWith('lab-a', 'order-a', 'item-a', {
      template_id: 'template-a',
      values: [{ definition_id: 'def-a', numeric_value: '7.2' }],
    })
  })

  it('UI-KB2: Enter in an empty entry field records nothing', async () => {
    const recordNativeResultValues = vi.fn()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      recordNativeResultValues,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    const field = await screen.findByLabelText(/Demo Measure A/)
    field.focus()
    await user.keyboard('{Enter}')

    // The same guard as the disabled button: nothing typed, nothing sent.
    expect(recordNativeResultValues).not.toHaveBeenCalled()
  })

  it('UI-KB3: Enter never completes entry -- that step keeps its own two gestures', async () => {
    const completeResultEntry = vi.fn()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      completeResultEntry,
      recordNativeResultValues: vi.fn().mockResolvedValue({
        order_id: 'order-a',
        item_id: 'item-a',
        result_id: 'result-1',
        template_id: 'template-a',
        values: [],
      }),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.type(await screen.findByLabelText(/Demo Measure A/), '7.2{Enter}')

    // Ending entry is the step that closes this item to further plain entry. A key press that
    // happened to land on it would be a gesture nobody chose to make.
    expect(completeResultEntry).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Terminer la saisie' })).toBeInTheDocument()
  })

  it('UI15: completion is blocked until every required definition has a value, with its own confirmation', async () => {
    const completeResultEntry = vi.fn()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      completeResultEntry,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    const completeButton = await screen.findByRole('button', { name: 'Terminer la saisie' })
    expect(completeButton).toBeDisabled()
    expect(completeResultEntry).not.toHaveBeenCalled()
  })

  // -- RR-group: result-entry-workspace race/stale-response safety (DH-LAB-004) -------------

  function workspaceWithItemName(orderId: string, itemName: string): ResultEntryWorkspace {
    return makeResultEntryWorkspace({
      order_id: orderId,
      items: [
        {
          item_id: `item-${itemName}`,
          catalog_code_snapshot: itemName,
          catalog_display_name_snapshot: itemName,
          template: null,
          template_pinned: false,
          entry_mode: 'UNSTARTED_NO_TEMPLATE',
          entry_completed: false,
          entry_completed_at: null,
          entered_values: [],
        },
      ],
    })
  }

  it('RR-WORKSPACE1: a late workspace response for order A never mutates the now-visible order B', async () => {
    const workspaceA = deferred<ResultEntryWorkspace>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        inProgressOrder({ order_id: 'order-a', patient: makePatient({ display_name: 'Patient A' }) }),
        inProgressOrder({ order_id: 'order-b', patient: makePatient({ display_name: 'Patient B' }) }),
      ]),
      getLabOrder: vi.fn((_labId: string, orderId: string) =>
        Promise.resolve(
          inProgressOrder({
            order_id: orderId,
            patient: makePatient({ display_name: orderId === 'order-a' ? 'Patient A' : 'Patient B' }),
          }),
        ),
      ),
      getResultEntryWorkspace: vi.fn((_labId: string, orderId: string) =>
        orderId === 'order-a'
          ? workspaceA.promise
          : Promise.resolve(workspaceWithItemName('order-b', 'Item Only In B')),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient A" }))
    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient B" }))
    await screen.findByText('Item Only In B')

    await act(async () => {
      workspaceA.resolve(workspaceWithItemName('order-a', 'Item Only In A'))
    })

    // order-a's late workspace content must never render under order-b's now-visible panel.
    // Verified discriminating: disabling `loadWorkspace`'s own generation check alone makes this
    // fail (the stale content actually leaks) -- confirmed manually, then the check restored.
    expect(screen.queryByText('Item Only In A')).not.toBeInTheDocument()
    expect(screen.getByText('Item Only In B')).toBeInTheDocument()
  })

  it('RR-WORKSPACE2: a late workspace error for order A after order B succeeded is discarded', async () => {
    const workspaceA = deferred<ResultEntryWorkspace>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        inProgressOrder({ order_id: 'order-a', patient: makePatient({ display_name: 'Patient A' }) }),
        inProgressOrder({ order_id: 'order-b', patient: makePatient({ display_name: 'Patient B' }) }),
      ]),
      getLabOrder: vi.fn((_labId: string, orderId: string) =>
        Promise.resolve(
          inProgressOrder({
            order_id: orderId,
            patient: makePatient({ display_name: orderId === 'order-a' ? 'Patient A' : 'Patient B' }),
          }),
        ),
      ),
      getResultEntryWorkspace: vi.fn((_labId: string, orderId: string) =>
        orderId === 'order-a'
          ? workspaceA.promise
          : Promise.resolve(workspaceWithItemName('order-b', 'Item Only In B')),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient A" }))
    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient B" }))
    await screen.findByText('Item Only In B')

    await act(async () => {
      workspaceA.reject(new Error('network lost'))
      await Promise.resolve()
    })

    expect(
      screen.queryByText('Impossible de charger la structure des résultats pour cet ordre.'),
    ).not.toBeInTheDocument()
    expect(screen.getByText('Item Only In B')).toBeInTheDocument()
  })

  it('RR-WORKSPACE3: switching labs invalidates an outstanding workspace request', async () => {
    const workspaceStall = deferred<ResultEntryWorkspace>()
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue(meWithTwoLabs),
      listLabOrders: vi.fn((labId: string) =>
        Promise.resolve([
          inProgressOrder({
            order_id: labId === 'lab-a' ? 'order-a' : 'order-b',
            patient: makePatient({ display_name: labId === 'lab-a' ? 'Patient Lab A' : 'Patient Lab B' }),
          }),
        ]),
      ),
      getLabOrder: vi.fn((labId: string, orderId: string) =>
        Promise.resolve(
          inProgressOrder({
            order_id: orderId,
            patient: makePatient({ display_name: labId === 'lab-a' ? 'Patient Lab A' : 'Patient Lab B' }),
          }),
        ),
      ),
      getResultEntryWorkspace: vi.fn((labId: string) =>
        labId === 'lab-a'
          ? workspaceStall.promise
          : Promise.resolve(workspaceWithItemName('order-b', 'Item Only Lab B')),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    const select = await screen.findByLabelText('Établissement actif')
    await user.selectOptions(select, 'lab-b')
    // Switching labs closes the panel entirely (LAB-001 §12) -- reopen lab B's own order.
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Item Only Lab B')

    await act(async () => {
      workspaceStall.resolve(workspaceWithItemName('order-a', 'Item Only Lab A'))
    })

    expect(screen.queryByText('Item Only Lab A')).not.toBeInTheDocument()
    expect(screen.getByText('Item Only Lab B')).toBeInTheDocument()
  })

  it('RR-WORKSPACE4: a workspace response arriving after unmount produces no error and no state update', async () => {
    const workspaceStall = deferred<ResultEntryWorkspace>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryWorkspace: vi.fn(() => workspaceStall.promise),
    })
    const { unmount } = render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    unmount()
    await act(async () => {
      workspaceStall.resolve(makeResultEntryWorkspace())
      await Promise.resolve()
    })
  })

  it('RR-WORKSPACE5: a workspace response whose order_id disagrees with the request fails closed', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryWorkspace: vi
        .fn()
        .mockResolvedValue(makeResultEntryWorkspace({ order_id: 'order-WRONG' })),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText('Impossible de vérifier de manière sûre cet ordre.'),
    ).toBeInTheDocument()
  })

  // -- Capability model / negative controls --------------------------------------------------

  it('§11: a releaser-only user sees a truthful "not yet available" message, never a fabricated queue', async () => {
    const client = makeClient({ getMe: vi.fn().mockResolvedValue(meReleaserOnly) })
    render(<StaffApp client={client} initialSection="orders" />)
    expect(
      await screen.findByText(
        'Les fonctions de validation/libération des résultats seront disponibles dans la prochaine étape.',
      ),
    ).toBeInTheDocument()
  })

  it('§13: a user with no eligible lab membership sees a truthful empty state, not a blank page', async () => {
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({ ...me, memberships: [] }),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    expect(
      // One neutral statement for both organization types now: the account holds
      // no usable membership, which is the true and complete fact. Naming a
      // laboratory specifically would have been wrong for a clinician, and
      // naming a clinic wrong for a technician.
      await screen.findByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).toBeInTheDocument()
  })

  it('§7: inactive lab membership is excluded from eligibility', async () => {
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [{ ...me.memberships[0], membership_status: 'REVOKED' }],
      }),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    expect(
      // One neutral statement for both organization types now: the account holds
      // no usable membership, which is the true and complete fact. Naming a
      // laboratory specifically would have been wrong for a clinician, and
      // naming a clinic wrong for a technician.
      await screen.findByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).toBeInTheDocument()
  })

  it('§7: inactive lab organization is excluded from eligibility', async () => {
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [{ ...me.memberships[0], organization_status: 'SUSPENDED' }],
      }),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    expect(
      // One neutral statement for both organization types now: the account holds
      // no usable membership, which is the true and complete fact. Naming a
      // laboratory specifically would have been wrong for a clinician, and
      // naming a clinic wrong for a technician.
      await screen.findByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).toBeInTheDocument()
  })

  // -- DH-LAB-005 PC-group: pre-release Native controlled correction ------------------------

  it('PC-UI1: an entered Native value offers Corriger, and the dialog carries the exact §20 wording', async () => {
    const client = makeClient({
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'IN_PROGRESS' })),
      getResultEntryWorkspace: vi.fn().mockResolvedValue(
        makeResultEntryWorkspace({
          items: [
            {
              ...makeResultEntryWorkspace().items[0],
              entered_values: [
                {
                  definition_id: 'def-a',
                  observation_id: 'obs-a',
                  numeric_value: '10.0',
                  text_value: null,
                  recorded_at: '2026-09-18T10:00:00Z',
                },
              ],
            },
          ],
        }),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText('Valeur actuelle')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Corriger' }))
    expect(
      await screen.findByText("Cette correction conservera l'ancienne valeur dans l'historique."),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirmer la correction' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Annuler' })).toBeInTheDocument()
  })

  it('PC-UI2: confirming a correction submits only the new value, never structural fields', async () => {
    const correctNativeObservation = vi.fn().mockResolvedValue({})
    const client = makeClient({
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'IN_PROGRESS' })),
      correctNativeObservation,
      getResultEntryWorkspace: vi.fn().mockResolvedValue(
        makeResultEntryWorkspace({
          items: [
            {
              ...makeResultEntryWorkspace().items[0],
              entered_values: [
                {
                  definition_id: 'def-a',
                  observation_id: 'obs-a',
                  numeric_value: '10.0',
                  text_value: null,
                  recorded_at: '2026-09-18T10:00:00Z',
                },
              ],
            },
          ],
        }),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Corriger' }))
    await user.type(screen.getByLabelText('Nouvelle valeur'), '12.5')
    await user.click(screen.getByRole('button', { name: 'Confirmer la correction' }))

    await waitFor(() => expect(correctNativeObservation).toHaveBeenCalledTimes(1))
    expect(correctNativeObservation).toHaveBeenCalledWith(
      'lab-a',
      'order-a',
      'item-a',
      'obs-a',
      { numeric_value: '12.5' },
    )
  })

  it('PC-UI3: history distinguishes the current value from a superseded one, never as equally current', async () => {
    const client = makeClient({
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'IN_PROGRESS' })),
      getResultEntryWorkspace: vi.fn().mockResolvedValue(
        makeResultEntryWorkspace({
          items: [
            {
              ...makeResultEntryWorkspace().items[0],
              entered_values: [
                {
                  definition_id: 'def-a',
                  observation_id: 'obs-b',
                  numeric_value: '12.5',
                  text_value: null,
                  recorded_at: '2026-09-18T10:05:00Z',
                },
              ],
            },
          ],
        }),
      ),
      getLabObservationHistory: vi.fn().mockResolvedValue(
        makeHistory({
          observations: [
            {
              order_id: 'order-a',
              item_id: 'item-a',
              result_id: 'result-a',
              observation_id: 'obs-a',
              source_code: 'DEMO_A',
              source_system: 'dhealth-exchange',
              display_label_snapshot: 'Demo Measure A',
              result_type: 'NUMERIC',
              numeric_value: '10.0',
              numeric_unit: 'demo-unit',
              reference_range_low: null,
              reference_range_high: null,
              text_value: null,
              recorded_at: '2026-09-18T10:00:00Z',
              recorded_by_identity_id: 'identity-1',
              supersedes_observation_id: null,
              superseded_at: '2026-09-18T10:05:00Z',
              template_observation_definition_id: 'def-a',
            },
            {
              order_id: 'order-a',
              item_id: 'item-a',
              result_id: 'result-a',
              observation_id: 'obs-b',
              source_code: 'DEMO_A',
              source_system: 'dhealth-exchange',
              display_label_snapshot: 'Demo Measure A',
              result_type: 'NUMERIC',
              numeric_value: '12.5',
              numeric_unit: 'demo-unit',
              reference_range_low: null,
              reference_range_high: null,
              text_value: null,
              recorded_at: '2026-09-18T10:05:00Z',
              recorded_by_identity_id: 'identity-1',
              supersedes_observation_id: 'obs-a',
              superseded_at: null,
              template_observation_definition_id: 'def-a',
            },
          ],
        }),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Voir l'historique" }))
    // The label ("Historique"/"Valeur actuelle") is bolded via a nested <strong>, so a plain
    // string/regex matcher (which only checks each element's own direct text nodes) can't see
    // the combined phrase -- a function matcher checking the full list item's textContent is the
    // correct tool here, exactly as Testing Library's own docs recommend for this shape.
    const historyItem = await screen.findByText(
      (_content, node) => node?.tagName === 'LI' && /Historique — Demo Measure A : 10\.0/.test(node.textContent ?? ''),
    )
    const currentItem = screen.getByText(
      (_content, node) => node?.tagName === 'LI' && /Valeur actuelle — Demo Measure A : 12\.5/.test(node.textContent ?? ''),
    )
    expect(historyItem).toBeInTheDocument()
    expect(currentItem).toBeInTheDocument()
  })

  // -- DH-LAB-005 AD/AC-group: amendment discovery, start/resume, and controlled correction -

  it('AD-UI1: a COMPLETED order with no amendment offers "Commencer un amendement", never "Reprendre"', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'COMPLETED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'COMPLETED' })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByRole('button', { name: 'Commencer un amendement' })).toBeInTheDocument()
    expect(screen.queryByText(/Reprendre l'amendement/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Annuler l'amendement/)).not.toBeInTheDocument()
  })

  it('AD-UI2: a COMPLETED order with a PREPARING amendment resumes it directly, never offering to start a second one', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'COMPLETED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'COMPLETED' })),
      listLabOrderAmendments: vi
        .fn()
        .mockResolvedValue({ order_id: 'order-a', amendments: [makeAmendment()] }),
      getLabOrderAmendment: vi.fn().mockResolvedValue(makeAmendmentInspection()),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText('Amendement en cours (n°1)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Commencer un amendement' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Annuler l'amendement/)).not.toBeInTheDocument()
  })

  it('confirming "Commencer un amendement" calls startLabOrderAmendment and then resumes the new amendment', async () => {
    const startLabOrderAmendment = vi.fn().mockResolvedValue(makeAmendment())
    const listLabOrderAmendments = vi
      .fn()
      .mockResolvedValueOnce({ order_id: 'order-a', amendments: [] })
      .mockResolvedValueOnce({ order_id: 'order-a', amendments: [makeAmendment()] })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'COMPLETED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'COMPLETED' })),
      startLabOrderAmendment,
      listLabOrderAmendments,
      getLabOrderAmendment: vi.fn().mockResolvedValue(makeAmendmentInspection()),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Commencer un amendement' }))
    expect(
      await screen.findByText(/Commencer un amendement pour cet ordre \?/),
    ).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: "Commencer l'amendement" }))

    await waitFor(() => expect(startLabOrderAmendment).toHaveBeenCalledWith('lab-a', 'order-a'))
    expect(await screen.findByText('Amendement en cours (n°1)')).toBeInTheDocument()
  })

  it('AC-UI1: a template-bound amendment observation shows "Modifié dans cet amendement" and offers Corriger', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'COMPLETED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'COMPLETED' })),
      listLabOrderAmendments: vi
        .fn()
        .mockResolvedValue({ order_id: 'order-a', amendments: [makeAmendment()] }),
      getLabOrderAmendment: vi.fn().mockResolvedValue(
        makeAmendmentInspection({
          items: [{ ...makeAmendmentInspection().items[0], changed_in_this_amendment: true }],
        }),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText('Modifié dans cet amendement')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Corriger' })).toBeInTheDocument()
  })

  it('§58: the durable-revision warning carries the exact wording before this amendment\'s first correction', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'COMPLETED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'COMPLETED' })),
      listLabOrderAmendments: vi
        .fn()
        .mockResolvedValue({ order_id: 'order-a', amendments: [makeAmendment()] }),
      getLabOrderAmendment: vi.fn().mockResolvedValue(makeAmendmentInspection({ ready_to_release: false })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Corriger' }))
    expect(
      await screen.findByText(
        "Cette correction sera conservée dans l'historique de l'amendement. Il n'existe pas d'annulation de l'amendement dans cette version de DHealth.",
        { exact: false },
      ),
    ).toBeInTheDocument()
  })

  it('a subsequent correction in the same amendment no longer repeats the durable-revision warning', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'COMPLETED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'COMPLETED' })),
      listLabOrderAmendments: vi
        .fn()
        .mockResolvedValue({ order_id: 'order-a', amendments: [makeAmendment()] }),
      getLabOrderAmendment: vi.fn().mockResolvedValue(makeAmendmentInspection({ ready_to_release: true })),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Corriger' }))
    expect(
      await screen.findByText("Cette correction conservera l'ancienne valeur dans l'historique."),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/Il n'existe pas d'annulation de l'amendement/),
    ).not.toBeInTheDocument()
  })

  it('correctAmendmentNativeObservation submits only the new value', async () => {
    const correctAmendmentNativeObservation = vi.fn().mockResolvedValue({})
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'COMPLETED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'COMPLETED' })),
      listLabOrderAmendments: vi
        .fn()
        .mockResolvedValue({ order_id: 'order-a', amendments: [makeAmendment()] }),
      getLabOrderAmendment: vi.fn().mockResolvedValue(makeAmendmentInspection()),
      correctAmendmentNativeObservation,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Corriger' }))
    await user.type(screen.getByLabelText('Nouvelle valeur'), '99.9')
    await user.click(screen.getByRole('button', { name: 'Confirmer la correction' }))

    await waitFor(() => expect(correctAmendmentNativeObservation).toHaveBeenCalledTimes(1))
    expect(correctAmendmentNativeObservation).toHaveBeenCalledWith(
      'lab-a',
      'order-a',
      'amendment-a',
      'item-a',
      'obs-a',
      { numeric_value: '99.9' },
    )
  })

  it('a stale amendment correction conflict re-reads authoritative state, never a blind retry', async () => {
    const correctAmendmentNativeObservation = vi
      .fn()
      .mockRejectedValue(new ExchangeApiError(409, 'CONFLICT', 'stale predecessor'))
    const getLabOrderAmendment = vi.fn().mockResolvedValue(makeAmendmentInspection())
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'COMPLETED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'COMPLETED' })),
      listLabOrderAmendments: vi
        .fn()
        .mockResolvedValue({ order_id: 'order-a', amendments: [makeAmendment()] }),
      getLabOrderAmendment,
      correctAmendmentNativeObservation,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Corriger' }))
    await user.type(screen.getByLabelText('Nouvelle valeur'), '99.9')
    await user.click(screen.getByRole('button', { name: 'Confirmer la correction' }))

    expect(
      await screen.findByText(
        'Cette valeur a déjà été modifiée depuis votre dernière consultation. La valeur actuelle est affichée ci-dessous.',
      ),
    ).toBeInTheDocument()
    await waitFor(() => expect(getLabOrderAmendment).toHaveBeenCalledTimes(2))
  })

  /**
   * DH-MVP-POLISH-001 — RE1–RE6, typed is not recorded.
   *
   * There is no autosave here and there must not be: a half-typed haemoglobin must
   * never reach the exchange. The cost of that choice is that a technician can
   * believe they have recorded a value they have only typed, so the interface has to
   * say which values are still only typed — and tell the truth about what a refresh
   * costs, because these drafts live in component memory alone and are never
   * persisted to browser storage.
   */
  describe('unsaved result values', () => {
    async function openEntry() {
      const client = makeClient({
        listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
        getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      })
      const user = userEvent.setup()
      render(<StaffApp client={client} initialSection="orders" />)
      await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
      await screen.findByRole('button', { name: 'Enregistrer les valeurs' })
      return { client, user }
    }

    it('RE1: says nothing while no value has been typed', async () => {
      await openEntry()
      expect(screen.queryByText(/Non enregistré/)).not.toBeInTheDocument()
      expect(screen.queryByText(/pas encore enregistrée/)).not.toBeInTheDocument()
    })

    it('RE2: marks the specific field that holds an unsaved value', async () => {
      // Field by field, rather than one banner at the foot of a long panel: the
      // technician needs to know *which* measure is not recorded.
      const { user } = await openEntry()
      await user.type(await screen.findByLabelText(/Demo Measure A/), '7.2')

      expect(screen.getByText(/Non enregistré/)).toBeInTheDocument()
    })

    it('RE3: warns about both things that actually discard the value', async () => {
      // Clinical values are deliberately never written to localStorage,
      // sessionStorage or IndexedDB, so this is a real consequence and not a scare.
      //
      // Two events lose a draft, and the marker names both. A refresh. And a failed
      // token refresh: ExchangeAuthProvider sets status `error`, ExchangeAuthGate
      // renders its own notice in place of the tree below it, and this component
      // unmounts. A *successful* silent refresh loses nothing, so the sentence says
      // "expire" rather than claiming any session event costs the work.
      const { user } = await openEntry()
      await user.type(await screen.findByLabelText(/Demo Measure A/), '7.2')

      const marker = screen.getByText(/Non enregistré/)
      expect(marker).toHaveTextContent(/perdue si vous actualisez la page/)
      expect(marker).toHaveTextContent(/votre session expire/)
    })

    it('RE4: counts the unsaved values as a status, not as an error', async () => {
      const { user } = await openEntry()
      await user.type(await screen.findByLabelText(/Demo Measure A/), '7.2')

      const statuses = screen.getAllByRole('status')
      expect(
        statuses.some((node) =>
          /n’est pas encore enregistrée|ne sont pas encore enregistrées/.test(node.textContent ?? ''),
        ),
      ).toBe(true)

      // Nothing has gone wrong; there is simply work not yet committed. Scoped to
      // this message rather than the whole panel, which has its own error slots.
      for (const alert of screen.queryAllByRole('alert')) {
        expect(alert.textContent ?? '').not.toMatch(/pas encore enregistrée/)
      }
    })

    it('RE5: stops marking a field once its value is cleared again', async () => {
      const { user } = await openEntry()
      const input = await screen.findByLabelText(/Demo Measure A/)

      await user.type(input, '7.2')
      expect(screen.getByText(/Non enregistré/)).toBeInTheDocument()

      await user.clear(input)
      expect(screen.queryByText(/Non enregistré/)).not.toBeInTheDocument()
    })

    it('RE6: persists no typed clinical value to browser storage', async () => {
      // The indicator exists precisely because nothing is saved anywhere yet. If a
      // draft ever reached storage, this ticket's PHI constraint would be broken and
      // the warning above would also be a lie.
      const { user } = await openEntry()
      await user.type(await screen.findByLabelText(/Demo Measure A/), '7.2')

      const stored = [
        ...Object.keys(window.localStorage),
        ...Object.keys(window.sessionStorage),
      ].map((key) => (window.localStorage.getItem(key) ?? '') + (window.sessionStorage.getItem(key) ?? ''))

      for (const value of stored) {
        expect(value).not.toContain('7.2')
      }
    })
  })
})

// == DHEALTH-LAB-BLOCKED-001: the cannot-proceed workflow ========================================
//
// The safety invariant the operating-product specification states for this feature is that a
// blocked order must appear as **work owed to a human, never a silent state**. Most of the
// assertions below are about that rather than about the mutation: that the reason is legible on
// the queue row, that it survives a reload, and that the block form is not offered where the
// server would refuse it.

function makeBlock(overrides: Partial<LabOrderBlock> = {}): LabOrderBlock {
  return {
    id: 'block-a',
    reason: 'SPECIMEN_UNUSABLE',
    note: null,
    blocked_at: '2026-09-29T08:30:00Z',
    blocked_by_identity_id: 'identity-1',
    blocked_from_status: 'ACCEPTED',
    resolved_at: null,
    resolved_by_identity_id: null,
    resolution_note: null,
    ...overrides,
  }
}

describe('LabOrdersPage — blocked orders (DHEALTH-LAB-BLOCKED-001)', () => {
  // -- where the action is offered, and where it is not ------------------------------------

  it('B1: an ACCEPTED order offers "Bloquer l\'ordre"', async () => {
    const order = makeOrder({ status: 'ACCEPTED', collection_recorded_at: null })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([order]),
      getLabOrder: vi.fn().mockResolvedValue(order),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByRole('button', { name: "Bloquer l'ordre" })).toBeInTheDocument()
  })

  it('B2: an IN_PROGRESS order offers it too — §16 names two source states, not one', async () => {
    const order = makeOrder({ status: 'IN_PROGRESS', collection_recorded_at: '2026-09-29T08:00:00Z' })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([order]),
      getLabOrder: vi.fn().mockResolvedValue(order),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByRole('button', { name: "Bloquer l'ordre" })).toBeInTheDocument()
  })

  it('B3: a SELECTED order does not offer it, and neither does a COMPLETED one', async () => {
    for (const status of ['SELECTED', 'COMPLETED'] as const) {
      const order = makeOrder({ status })
      const client = makeClient({
        listLabOrders: vi.fn().mockResolvedValue([order]),
        getLabOrder: vi.fn().mockResolvedValue(order),
      })
      const user = userEvent.setup()
      const { unmount } = render(<StaffApp client={client} initialSection="orders" />)
      await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
      // Wait for the panel to settle on something status-specific before asserting an absence.
      await screen.findByText(/Établissement prescripteur/)
      expect(screen.queryByRole('button', { name: "Bloquer l'ordre" })).not.toBeInTheDocument()
      unmount()
    }
  })

  // -- the block interaction ----------------------------------------------------------------

  it('B4: the form asks for a motif and an optional note, and for nothing the server derives', async () => {
    const order = makeOrder({ status: 'ACCEPTED' })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([order]),
      getLabOrder: vi.fn().mockResolvedValue(order),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Bloquer l'ordre" }))

    expect(await screen.findByLabelText('Motif')).toBeInTheDocument()
    expect(screen.getByLabelText(/Note/)).toBeInTheDocument()

    // Every one of the four reasons is offered in French, never as a raw enum key.
    for (const label of [
      'Prélèvement inutilisable',
      'Quantité insuffisante',
      'Patient non présenté',
      'Précision nécessaire',
    ]) {
      expect(screen.getByRole('option', { name: label })).toBeInTheDocument()
    }
    expect(document.body.textContent).not.toContain('SPECIMEN_UNUSABLE')

    // §26: the staff identity, the organization, the time and the resume state are derived. The
    // form must not ask for any of them.
    expect(screen.queryByLabelText(/technicien|identité|organisation|date|heure|reprendre à/i))
      .not.toBeInTheDocument()
  })

  it('B5: confirmation is required, and the request carries only the reason and the note', async () => {
    const order = makeOrder({ status: 'ACCEPTED' })
    const blockLabOrder = vi
      .fn()
      .mockResolvedValue(makeOrder({ status: 'BLOCKED', active_block: makeBlock() }))
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([order]),
      getLabOrder: vi.fn().mockResolvedValue(order),
      blockLabOrder,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Bloquer l'ordre" }))

    // Opening the form is not the mutation.
    expect(blockLabOrder).not.toHaveBeenCalled()

    await user.selectOptions(await screen.findByLabelText('Motif'), 'SPECIMEN_INSUFFICIENT')
    await user.type(screen.getByLabelText(/Note/), 'Tube à moitié rempli')
    await user.click(screen.getByRole('button', { name: 'Confirmer le blocage' }))

    await waitFor(() =>
      expect(blockLabOrder).toHaveBeenCalledWith('lab-a', 'order-a', {
        reason: 'SPECIMEN_INSUFFICIENT',
        note: 'Tube à moitié rempli',
      }),
    )
  })

  it('B6: an untouched note is sent as null, never as an empty string', async () => {
    const order = makeOrder({ status: 'ACCEPTED' })
    const blockLabOrder = vi
      .fn()
      .mockResolvedValue(makeOrder({ status: 'BLOCKED', active_block: makeBlock() }))
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([order]),
      getLabOrder: vi.fn().mockResolvedValue(order),
      blockLabOrder,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Bloquer l'ordre" }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer le blocage' }))

    await waitFor(() =>
      expect(blockLabOrder).toHaveBeenCalledWith('lab-a', 'order-a', {
        reason: 'SPECIMEN_UNUSABLE',
        note: null,
      }),
    )
  })

  // -- what a blocked order looks like ------------------------------------------------------

  it('B7: the queue row states "Bloqué" and the motif in text, never colour alone', async () => {
    const blocked = makeOrder({
      status: 'BLOCKED',
      active_block: makeBlock({ reason: 'PATIENT_DID_NOT_ATTEND' }),
    })
    const client = makeClient({ listLabOrders: vi.fn().mockResolvedValue([blocked]) })
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findByText('Bloqué')).toBeInTheDocument()
    expect(screen.getByText(/Motif : Patient non présenté/)).toBeInTheDocument()
  })

  it('B8: the panel shows the reason, when, by whom, and the note', async () => {
    const blocked = makeOrder({
      status: 'BLOCKED',
      active_block: makeBlock({ note: 'Second tube demandé au service' }),
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([blocked]),
      getLabOrder: vi.fn().mockResolvedValue(blocked),
      listLabOrderBlocks: vi.fn().mockResolvedValue([makeBlock({ note: 'Second tube demandé au service' })]),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByText(/Travail bloqué — Prélèvement inutilisable/)).toBeInTheDocument()
    expect(screen.getAllByText(/Second tube demandé au service/).length).toBeGreaterThan(0)
    // `blocked_by_identity_id` matches the signed-in identity, so the screen says "vous" rather
    // than printing a UUID at a technician.
    expect(screen.getAllByText(/par vous/).length).toBeGreaterThan(0)
    expect(document.body.textContent).not.toContain('identity-1')
  })

  it('B9: "Résoudre le blocage" appears only when there is an active block', async () => {
    const accepted = makeOrder({ status: 'ACCEPTED' })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([accepted]),
      getLabOrder: vi.fn().mockResolvedValue(accepted),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText(/Établissement prescripteur/)

    expect(screen.queryByRole('button', { name: 'Résoudre le blocage' })).not.toBeInTheDocument()
  })

  // -- resolving ----------------------------------------------------------------------------

  it('B10: resolving names the episode in the request and never a destination', async () => {
    const blocked = makeOrder({ status: 'BLOCKED', active_block: makeBlock({ id: 'block-xyz' }) })
    const resolveLabOrderBlock = vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' }))
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([blocked]),
      getLabOrder: vi.fn().mockResolvedValue(blocked),
      listLabOrderBlocks: vi.fn().mockResolvedValue([makeBlock({ id: 'block-xyz' })]),
      resolveLabOrderBlock,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Résoudre le blocage' }))

    // The confirmation quotes the server's own recorded resume state; it does not offer a choice.
    expect(await screen.findByText(/Résoudre ce blocage \?/)).toBeInTheDocument()
    expect(document.body.textContent).toMatch(/restera dans l'historique/)
    expect(resolveLabOrderBlock).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Confirmer la reprise' }))
    await waitFor(() =>
      expect(resolveLabOrderBlock).toHaveBeenCalledWith('lab-a', 'order-a', 'block-xyz'),
    )
  })

  it('B11: the user is never asked to choose the state the order resumes to', async () => {
    const blocked = makeOrder({
      status: 'BLOCKED',
      active_block: makeBlock({ blocked_from_status: 'IN_PROGRESS' }),
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([blocked]),
      getLabOrder: vi.fn().mockResolvedValue(blocked),
      listLabOrderBlocks: vi.fn().mockResolvedValue([makeBlock({ blocked_from_status: 'IN_PROGRESS' })]),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Résoudre le blocage' }))

    // It *states* where the order will go, read from `blocked_from_status`. Scoped to the
    // confirmation itself: "En cours" is also a queue filter tab, and matching that instead would
    // make this assertion pass without the sentence ever being rendered.
    const confirmation = await screen.findByRole('alert')
    expect(confirmation.textContent).toMatch(/reprendra à l'état/)
    expect(confirmation.textContent).toMatch(/En cours/)
    // ...and offers no control to change it.
    expect(screen.queryByLabelText(/reprendre à|destination|état/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /état|destination/i })).not.toBeInTheDocument()
  })

  // -- history ------------------------------------------------------------------------------

  it('B12: resolved episodes stay visible, with who resolved them and when', async () => {
    const blocked = makeOrder({ status: 'BLOCKED', active_block: makeBlock({ id: 'block-2' }) })
    const history = [
      makeBlock({
        id: 'block-2',
        reason: 'CLARIFICATION_REQUIRED',
        blocked_from_status: 'IN_PROGRESS',
      }),
      makeBlock({
        id: 'block-1',
        reason: 'SPECIMEN_INSUFFICIENT',
        note: 'Premier problème',
        resolved_at: '2026-09-29T09:00:00Z',
        resolved_by_identity_id: 'identity-1',
        resolution_note: 'Nouveau tube reçu',
      }),
    ]
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([blocked]),
      getLabOrder: vi.fn().mockResolvedValue(blocked),
      listLabOrderBlocks: vi.fn().mockResolvedValue(history),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByRole('heading', { name: 'Blocages' })).toBeInTheDocument()
    // Both episodes survive: the current one and the one already dealt with. A single mutable
    // `block_reason` column could never show this.
    expect(screen.getByText(/Quantité insuffisante — résolu/)).toBeInTheDocument()
    expect(screen.getByText(/Précision nécessaire — en cours/)).toBeInTheDocument()
    expect(screen.getByText(/Premier problème/)).toBeInTheDocument()
    expect(screen.getByText(/Nouveau tube reçu/)).toBeInTheDocument()
    expect(screen.getByText(/Résolu .* par vous/)).toBeInTheDocument()
  })

  it('B13: an order that has never been blocked shows no Blocages section', async () => {
    const order = makeOrder({ status: 'ACCEPTED' })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([order]),
      getLabOrder: vi.fn().mockResolvedValue(order),
      listLabOrderBlocks: vi.fn().mockResolvedValue([]),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText(/Établissement prescripteur/)

    expect(screen.queryByRole('heading', { name: 'Blocages' })).not.toBeInTheDocument()
  })

  // -- the queue filter ---------------------------------------------------------------------

  it('B14: "Bloqués" is its own filter — a state nobody can list is a silent state', async () => {
    const listLabOrders = vi.fn().mockResolvedValue([])
    const client = makeClient({ listLabOrders })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(await screen.findByRole('tab', { name: 'Bloqués' }))
    await waitFor(() =>
      expect(listLabOrders).toHaveBeenCalledWith(
        'lab-a',
        expect.objectContaining({ status: 'BLOCKED' }),
      ),
    )
  })

  // -- the unknown-reason fallback ----------------------------------------------------------

  it('B15: an unrecognised reason key renders a neutral label, never the raw enum', async () => {
    const blocked = makeOrder({
      status: 'BLOCKED',
      // A deployment-skew shape: the server knows a reason this build does not.
      active_block: makeBlock({ reason: 'ANALYSER_OFFLINE' as LabOrderBlock['reason'] }),
    })
    const client = makeClient({ listLabOrders: vi.fn().mockResolvedValue([blocked]) })
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findByText(/Motif : Motif non précisé/)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('ANALYSER_OFFLINE')
  })
})
