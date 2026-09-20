import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../../api/exchangeClient'
import type {
  ExchangeMeResponse,
  LabWorkflowOrder,
  LabWorkflowPatientIdentity,
} from '../../../api/exchangeTypes'
import { StaffApp } from '../../staff/StaffApp'

const meWithTwoLabs: ExchangeMeResponse = {
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
    {
      organization_id: 'lab-b',
      organization_name: 'Laboratoire B',
      organization_type: 'LAB',
      organization_status: 'ACTIVE',
      membership_status: 'ACTIVE',
      roles: ['LAB_TECH'],
    },
  ],
}

function makePatient(): LabWorkflowPatientIdentity {
  return {
    patient_id: '11111111-1111-4111-8111-111111111111',
    display_name: 'Fatouma Robleh',
    date_of_birth: '1988-06-02',
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
    requested_tests: [],
    collection_recorded_at: null,
    ...overrides,
  }
}

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

describe('LabHomePage', () => {
  // DH-LAB-002 §35/§62 (AC21): the LAB-001-disclosed adjacent gap -- LabHome's own count request
  // lifecycle must be hardened to the same tenant/generation discipline as LabOrdersPage's queue,
  // not just "happens to be safe" via useEffect's own cleanup-before-rerun ordering.
  it('a late Lab A count response never overwrites Lab B counts after switching', async () => {
    const labAQueue = deferred<LabWorkflowOrder[]>()
    const client = {
      getMe: vi.fn().mockResolvedValue(meWithTwoLabs),
      listLabOrders: vi.fn((labId: string) =>
        labId === 'lab-a'
          ? labAQueue.promise
          : Promise.resolve([makeOrder({ order_id: 'order-b', status: 'ACCEPTED' })]),
      ),
    } as unknown as ExchangeClient

    render(<StaffApp client={client} initialSection="today" />)
    await screen.findByRole('heading', { name: 'Accueil Laboratoire' })

    const select = await screen.findByLabelText('Établissement actif')
    await userEvent.setup().selectOptions(select, 'lab-b')

    // Lab B's own counts: 1 accepted, 0 new -- wait for them before A's stale response arrives.
    // ("Laboratoire B" itself is ambiguous: it appears both in the <select> option and the
    // "Laboratoire actif" header, so wait on the count render instead.)
    await waitFor(() => expect(screen.getAllByText('Laboratoire B').length).toBeGreaterThan(0))
    await waitFor(() => expect(screen.getByText('1')).toBeInTheDocument())

    await act(async () => {
      // Lab A resolves late with a distinctive, unmistakable count (5 new orders).
      labAQueue.resolve([
        makeOrder({ order_id: 'a1' }),
        makeOrder({ order_id: 'a2' }),
        makeOrder({ order_id: 'a3' }),
        makeOrder({ order_id: 'a4' }),
        makeOrder({ order_id: 'a5' }),
      ])
      await Promise.resolve()
    })

    // Lab A's stale "5" must never render under Lab B's now-active header. Verified
    // discriminating: disabling either the generation check or the `currentLabRef` check alone
    // still passes (each independently blocks this exact scenario, since a lab switch bumps the
    // generation ref in the same render pass); disabling both together makes this assertion fail
    // ("5" renders) -- confirmed manually, then both checks restored.
    expect(screen.queryByText('5')).not.toBeInTheDocument()
  })
})
