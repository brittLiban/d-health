import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../../api/exchangeClient'
import type {
  ExchangeMeResponse,
  LabWorkflowOrder,
  LabWorkflowPatientIdentity,
  ResultEntryPlan,
} from '../../../api/exchangeTypes'
import { LabApp } from '../LabApp'

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

const meReleaserOnly: ExchangeMeResponse = {
  ...me,
  memberships: [{ ...me.memberships[0], roles: ['LAB_RESULT_RELEASER'] }],
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
    getResultEntryPlan: vi.fn().mockResolvedValue(makeResultEntryPlan()),
    ...overrides,
  } as unknown as ExchangeClient
}

function makeResultEntryPlan(overrides: Partial<ResultEntryPlan> = {}): ResultEntryPlan {
  return {
    order_id: 'order-a',
    order_status: 'IN_PROGRESS',
    all_items_configured: true,
    items: [
      {
        item_id: 'item-a',
        catalog_entry_id: 'entry-a',
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
      },
    ],
    ...overrides,
  }
}

function makeCollectionResponse(overrides: { order_id?: string; recorded_at?: string } = {}) {
  return { order_id: 'order-a', recorded_at: '2026-09-18T10:42:00Z', ...overrides }
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
    render(<LabApp client={client} initialTab="orders" />)

    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(screen.getByText('Clinique de la Paix')).toBeInTheDocument()
    expect(screen.getByText('Glycémie à jeun')).toBeInTheDocument()
  })

  it('Q3: date of birth is rendered when known', async () => {
    const client = makeClient()
    render(<LabApp client={client} initialTab="orders" />)
    expect(await screen.findByText(/Né\(e\) le/)).toBeInTheDocument()
  })

  it('Q6: the technical order reference is present but secondary, never the primary label', async () => {
    const client = makeClient()
    render(<LabApp client={client} initialTab="orders" />)
    await screen.findByText('Fatouma Robleh')
    expect(screen.getByText(/Référence de l'ordre/)).toBeInTheDocument()
  })

  it('Q7: no marketplace price is shown in the work queue', async () => {
    const client = makeClient()
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
    expect(await screen.findByText(expected)).toBeInTheDocument()
  })

  // -- A-group: accept/reject actions -------------------------------------------------------

  it('A1/A2: a SELECTED order exposes both Accept and Reject controls', async () => {
    const client = makeClient()
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByRole('button', { name: "Accepter l'ordre" })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Refuser l'ordre" })).toBeInTheDocument()
  })

  it('A3: accepting calls the exact accept endpoint for the exact lab/order', async () => {
    const acceptLabOrder = vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' }))
    const client = makeClient({ acceptLabOrder, getLabOrder: makeGetLabOrderRecheckMock('ACCEPTED') })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Accepter l'ordre" }))
    await waitFor(() => expect(acceptLabOrder).toHaveBeenCalledWith('lab-a', 'order-a'))
  })

  it('A4/A7: reject requires explicit confirmation before calling the reject endpoint', async () => {
    const rejectLabOrder = vi.fn().mockResolvedValue(makeOrder({ status: 'REJECTED' }))
    const client = makeClient({ rejectLabOrder, getLabOrder: makeGetLabOrderRecheckMock('REJECTED') })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Accepter l'ordre" }))
    expect(await screen.findByText('Impossible de vérifier de manière sûre cet ordre.')).toBeInTheDocument()
  })

  it('A9: a reject response whose order_id disagrees fails closed', async () => {
    const client = makeClient({
      rejectLabOrder: vi.fn().mockResolvedValue(makeOrder({ order_id: 'order-DIFFERENT', status: 'REJECTED' })),
    })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)

    const select = await screen.findByLabelText('Sélectionner le laboratoire')
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
    render(<LabApp client={client} initialTab="orders" />)
    await screen.findByText('Patient Lab A')

    const select = await screen.findByLabelText('Sélectionner le laboratoire')
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
    render(<LabApp client={client} initialTab="orders" />)

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
    render(<LabApp client={client} initialTab="orders" />)

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
    render(<LabApp client={client} initialTab="orders" />)

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
    const { unmount } = render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByRole('button', { name: 'Enregistrer la collecte' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Commencer l'analyse" })).not.toBeInTheDocument()
  })

  it('CUI2: a SELECTED order does not show any collection mutation control', async () => {
    const client = makeClient()
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' }))

    await waitFor(() => expect(recordLabOrderCollection).toHaveBeenCalledWith('lab-a', 'order-a'))
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await user.click(await screen.findByRole('button', { name: 'Commencer' }))
    expect(await screen.findByText('Impossible de vérifier de manière sûre cet ordre.')).toBeInTheDocument()
  })

  it('PUI8: no result-entry control appears once IN_PROGRESS', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([
        makeOrder({ status: 'IN_PROGRESS', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ]),
      getLabOrder: vi.fn().mockResolvedValue(
        makeOrder({ status: 'IN_PROGRESS', collection_recorded_at: '2026-09-18T10:42:00Z' }),
      ),
    })
    render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(await screen.findByText(/La saisie des résultats sera disponible/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /résultat/i })).not.toBeInTheDocument()
  })

  // -- LR-C/LR-P-group: lost-response recovery (DH-LAB-002) ---------------------------------

  it('LR-C1: collection commits but the response is lost -- recheck shows success, no error', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' })),
      recordLabOrderCollection: vi.fn().mockRejectedValue(new Error('network lost')),
    })
    client.getLabOrder = makeGetLabOrderRecheckWithCollection('ACCEPTED', '2026-09-18T10:42:00Z')
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' }))

    await waitFor(() => expect(screen.getAllByText('Collecte enregistrée').length).toBeGreaterThan(0))
    expect(
      screen.queryByText("L'action n'a pas pu être confirmée. Vous pouvez réessayer."),
    ).not.toBeInTheDocument()
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
    render(<LabApp client={client} initialTab="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' }))

    expect(await screen.findByText("L'action n'a pas pu être confirmée. Vous pouvez réessayer.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enregistrer la collecte' })).toBeInTheDocument()
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
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
    const collectA = deferred<{ order_id: string; recorded_at: string }>()
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
    render(<LabApp client={client} initialTab="orders" />)

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
    render(<LabApp client={client} initialTab="orders" />)

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
    render(<LabApp client={client} initialTab="orders" />)

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
    render(<LabApp client={client} initialTab="orders" />)

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
    const collectStall = deferred<{ order_id: string; recorded_at: string }>()
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
    render(<LabApp client={client} initialTab="orders" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' })) // stalls

    const select = await screen.findByLabelText('Sélectionner le laboratoire')
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
    render(<LabApp client={client} initialTab="orders" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await user.click(await screen.findByRole('button', { name: 'Commencer' })) // stalls

    const select = await screen.findByLabelText('Sélectionner le laboratoire')
    await user.selectOptions(select, 'lab-b')
    await screen.findByText('Patient Lab B')

    await act(async () => {
      startStall.resolve(makeOrder({ order_id: 'order-a', status: 'IN_PROGRESS' }))
    })

    expect(screen.queryByText('Patient Lab A')).not.toBeInTheDocument()
    expect(screen.getByText('Patient Lab B')).toBeInTheDocument()
  })

  it('RR-U1: a collection/processing response arriving after unmount produces no error and no state update', async () => {
    const collectStall = deferred<{ order_id: string; recorded_at: string }>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'ACCEPTED' })]),
      getLabOrder: vi.fn().mockResolvedValue(makeOrder({ status: 'ACCEPTED' })),
      recordLabOrderCollection: vi.fn(() => collectStall.promise),
    })
    const user = userEvent.setup()
    const { unmount } = render(<LabApp client={client} initialTab="orders" />)
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await user.click(await screen.findByRole('button', { name: 'Confirmer la collecte' })) // stalls

    unmount()
    await act(async () => {
      collectStall.resolve(makeCollectionResponse())
      await Promise.resolve()
    })
  })

  // -- UI-group: read-only result-entry-plan preview (DH-LAB-003) ---------------------------

  function inProgressOrder(overrides: Partial<LabWorkflowOrder> = {}): LabWorkflowOrder {
    return makeOrder({
      status: 'IN_PROGRESS',
      collection_recorded_at: '2026-09-18T10:42:00Z',
      ...overrides,
    })
  }

  it('UI1/UI2: an IN_PROGRESS order loads the plan and renders observations in sort order', async () => {
    const plan = makeResultEntryPlan({
      items: [
        {
          item_id: 'item-a',
          catalog_entry_id: 'entry-a',
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
        },
      ],
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryPlan: vi.fn().mockResolvedValue(plan),
    })
    render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Structure des résultats')

    // Scope to the observation list items specifically -- `findAllByRole('listitem')` would
    // otherwise also match the unrelated queue row's own `<li>`.
    const observationItems = (await screen.findAllByRole('listitem')).filter((el) =>
      el.textContent?.includes('Measure'),
    )
    expect(observationItems.map((el) => el.textContent)).toEqual([
      expect.stringContaining('First Measure'),
      expect.stringContaining('Second Measure'),
    ])
  })

  it('UI3/UI4/UI5/UI6: result type, unit, and required/optional are shown truthfully', async () => {
    const plan = makeResultEntryPlan({
      items: [
        {
          item_id: 'item-a',
          catalog_entry_id: 'entry-a',
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
        },
      ],
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryPlan: vi.fn().mockResolvedValue(plan),
    })
    render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByText(/Numeric Measure.*Numérique.*mg\/dL.*requis/)).toBeInTheDocument()
    expect(await screen.findByText(/Text Measure.*Texte.*facultatif/)).toBeInTheDocument()
  })

  it('UI7/UI10: no value input or free-form field exists anywhere in the preview', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
    })
    render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Structure des résultats')
    expect(screen.queryAllByRole('textbox')).toHaveLength(0)
    expect(screen.queryAllByRole('spinbutton')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: /Entrer|Compléter|Valider|Libérer/i })).not.toBeInTheDocument()
  })

  it('UI8: no reference range is displayed anywhere in the preview', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
    })
    render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Structure des résultats')
    // ("Référence de l'ordre" is a pre-existing, unrelated label -- match the clinical-range
    // terms specifically, not the substring "référence" alone.)
    expect(
      screen.queryByText(/plage de référence|valeur de référence|intervalle de référence/i),
    ).not.toBeInTheDocument()
  })

  it('UI9: a missing template shows a truthful blocking message, never a fabricated structure', async () => {
    const plan = makeResultEntryPlan({
      all_items_configured: false,
      items: [
        {
          item_id: 'item-a',
          catalog_entry_id: 'entry-a',
          catalog_code_snapshot: 'GLU',
          catalog_display_name_snapshot: 'Glycémie à jeun',
          template: null,
        },
      ],
    })
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryPlan: vi.fn().mockResolvedValue(plan),
    })
    render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText("Aucune structure de résultat n'est configurée pour cette analyse."),
    ).toBeInTheDocument()
  })

  it('UI11: a synthetic template displays the demo warning', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
    })
    render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText(/Configuration de démonstration — ne pas utiliser pour des soins réels\./),
    ).toBeInTheDocument()
  })

  it('UI12: Patient identity remains visible alongside the result-structure preview', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
    })
    render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Structure des résultats')
    expect(screen.getAllByText('Fatouma Robleh').length).toBeGreaterThan(0)
  })

  // -- RR-group: result-entry-plan race/stale-response safety (DH-LAB-003) ------------------

  function planWithItemName(orderId: string, itemName: string): ResultEntryPlan {
    return makeResultEntryPlan({
      order_id: orderId,
      items: [
        {
          item_id: `item-${itemName}`,
          catalog_entry_id: `entry-${itemName}`,
          catalog_code_snapshot: itemName,
          catalog_display_name_snapshot: itemName,
          template: null,
        },
      ],
    })
  }

  it('RR-PLAN1: a late plan response for order A never mutates the now-visible order B', async () => {
    const planA = deferred<ResultEntryPlan>()
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
      getResultEntryPlan: vi.fn((_labId: string, orderId: string) =>
        orderId === 'order-a'
          ? planA.promise
          : Promise.resolve(planWithItemName('order-b', 'Item Only In B')),
      ),
    })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="orders" />)

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient A" }))
    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient B" }))
    await screen.findByText('Item Only In B')

    await act(async () => {
      planA.resolve(planWithItemName('order-a', 'Item Only In A'))
    })

    // order-a's late plan content must never render under order-b's now-visible panel. Verified
    // discriminating: disabling `loadPlan`'s own generation check alone makes this fail (the
    // stale content actually leaks) -- confirmed manually, then the check restored.
    expect(screen.queryByText('Item Only In A')).not.toBeInTheDocument()
    expect(screen.getByText('Item Only In B')).toBeInTheDocument()
  })

  it('RR-PLAN2: a late plan error for order A after order B succeeded is discarded', async () => {
    const planA = deferred<ResultEntryPlan>()
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
      getResultEntryPlan: vi.fn((_labId: string, orderId: string) =>
        orderId === 'order-a'
          ? planA.promise
          : Promise.resolve(planWithItemName('order-b', 'Item Only In B')),
      ),
    })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="orders" />)

    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient A" }))
    await user.click(await screen.findByRole('button', { name: "Examiner l'ordre de Patient B" }))
    await screen.findByText('Item Only In B')

    await act(async () => {
      planA.reject(new Error('network lost'))
      await Promise.resolve()
    })

    expect(
      screen.queryByText('Impossible de charger la structure des résultats pour cet ordre.'),
    ).not.toBeInTheDocument()
    expect(screen.getByText('Item Only In B')).toBeInTheDocument()
  })

  it('RR-PLAN3: switching labs invalidates an outstanding plan request', async () => {
    const planStall = deferred<ResultEntryPlan>()
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
      getResultEntryPlan: vi.fn((labId: string) =>
        labId === 'lab-a'
          ? planStall.promise
          : Promise.resolve(planWithItemName('order-b', 'Item Only Lab B')),
      ),
    })
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="orders" />)

    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))

    const select = await screen.findByLabelText('Sélectionner le laboratoire')
    await user.selectOptions(select, 'lab-b')
    // Switching labs closes the panel entirely (LAB-001 §12) -- reopen lab B's own order.
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Item Only Lab B')

    await act(async () => {
      planStall.resolve(planWithItemName('order-a', 'Item Only Lab A'))
    })

    expect(screen.queryByText('Item Only Lab A')).not.toBeInTheDocument()
    expect(screen.getByText('Item Only Lab B')).toBeInTheDocument()
  })

  it('RR-PLAN4: a plan response arriving after unmount produces no error and no state update', async () => {
    const planStall = deferred<ResultEntryPlan>()
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryPlan: vi.fn(() => planStall.promise),
    })
    const { unmount } = render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    unmount()
    await act(async () => {
      planStall.resolve(makeResultEntryPlan())
      await Promise.resolve()
    })
  })

  it('RR-PLAN5: a plan response whose order_id disagrees with the request fails closed', async () => {
    const client = makeClient({
      listLabOrders: vi.fn().mockResolvedValue([inProgressOrder()]),
      getLabOrder: vi.fn().mockResolvedValue(inProgressOrder()),
      getResultEntryPlan: vi.fn().mockResolvedValue(makeResultEntryPlan({ order_id: 'order-WRONG' })),
    })
    render(<LabApp client={client} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    expect(
      await screen.findByText('Impossible de vérifier de manière sûre cet ordre.'),
    ).toBeInTheDocument()
  })

  // -- Capability model / negative controls --------------------------------------------------

  it('§11: a releaser-only user sees a truthful "not yet available" message, never a fabricated queue', async () => {
    const client = makeClient({ getMe: vi.fn().mockResolvedValue(meReleaserOnly) })
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="orders" />)
    expect(
      await screen.findByText("Aucun laboratoire actif n'est associé à votre compte."),
    ).toBeInTheDocument()
  })

  it('§7: inactive lab membership is excluded from eligibility', async () => {
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [{ ...me.memberships[0], membership_status: 'REVOKED' }],
      }),
    })
    render(<LabApp client={client} initialTab="orders" />)
    expect(
      await screen.findByText("Aucun laboratoire actif n'est associé à votre compte."),
    ).toBeInTheDocument()
  })

  it('§7: inactive lab organization is excluded from eligibility', async () => {
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [{ ...me.memberships[0], organization_status: 'SUSPENDED' }],
      }),
    })
    render(<LabApp client={client} initialTab="orders" />)
    expect(
      await screen.findByText("Aucun laboratoire actif n'est associé à votre compte."),
    ).toBeInTheDocument()
  })
})
