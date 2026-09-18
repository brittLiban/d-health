import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../../api/exchangeClient'
import type {
  ExchangeMeResponse,
  LabWorkflowOrder,
  LabWorkflowPatientIdentity,
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
    ...overrides,
  } as unknown as ExchangeClient
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
      await Promise.resolve()
    })

    // order-a's late accept must never mutate the panel now showing order-b.
    expect(screen.getByRole('button', { name: "Accepter l'ordre" })).toBeInTheDocument()
    expect(screen.queryByText('Décision enregistrée — Accepté')).not.toBeInTheDocument()
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
      await Promise.resolve()
    })

    // order-a's late reject must never mutate the panel now showing order-b.
    expect(screen.getByRole('button', { name: "Refuser l'ordre" })).toBeInTheDocument()
    expect(screen.queryByText('Décision enregistrée — Refusé')).not.toBeInTheDocument()
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
