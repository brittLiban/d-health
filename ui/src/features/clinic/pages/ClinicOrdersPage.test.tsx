import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../../api/exchangeClient'
import type {
  ExchangeMeResponse,
  LabOrder,
  LabOrderSummary,
  PatientIdentitySummary,
  ProviderEligibleLab,
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
    id: 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa',
    provider_organization_id: 'provider-a',
    patient_reference_id: 'ref-11111111-1111-4111-8111-111111111111',
    practitioner_reference_id: 'prac-1',
    patient: makePatient(),
    status: 'AWAITING_LAB_SELECTION',
    created_at: '2026-09-17T08:00:00Z',
    item_count: 2,
    lab_assignment: null,
    ...overrides,
  }
}

function makeFullOrder(overrides: Partial<LabOrder> = {}): LabOrder {
  return {
    id: 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa',
    provider_organization_id: 'provider-a',
    patient_reference_id: 'ref-11111111-1111-4111-8111-111111111111',
    practitioner_reference_id: 'prac-1',
    patient: makePatient(),
    status: 'AWAITING_LAB_SELECTION',
    created_at: '2026-09-17T08:00:00Z',
    items: [
      { catalog_entry_id: 'entry-1', catalog_code_snapshot: 'GLU', catalog_display_name_snapshot: 'Glycémie' },
    ],
    lab_assignment: null,
    ...overrides,
  }
}

function makeEligibleLab(overrides: Partial<ProviderEligibleLab> = {}): ProviderEligibleLab {
  return {
    lab_organization_id: 'lab-1',
    lab_organization_name: 'Laboratoire Central',
    public_address: 'Route de Venise, Djibouti',
    public_phone: '+25321350000',
    ...overrides,
  }
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(me),
    listOrders: vi.fn().mockResolvedValue([]),
    ...overrides,
  } as unknown as ExchangeClient
}

describe('ClinicOrdersPage', () => {
  it('OU1/OU5: renders the patient name as the primary identity, never the reference UUID as a column', async () => {
    const client = makeClient({ listOrders: vi.fn().mockResolvedValue([makeOrder()]) })
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(screen.queryByText('Réf. Patient')).not.toBeInTheDocument()
    expect(screen.queryByText(/^ref-11111111/)).not.toBeInTheDocument()
  })

  it('OU2/OU3: shows DOB and contact when known', async () => {
    const client = makeClient({ listOrders: vi.fn().mockResolvedValue([makeOrder()]) })
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findByText('Né(e) le 2 juin 1988')).toBeInTheDocument()
    expect(screen.getByText('Téléphone : +25377100001')).toBeInTheDocument()
  })

  it('OU4: the order technical reference remains available, as a secondary column', async () => {
    const client = makeClient({ listOrders: vi.fn().mockResolvedValue([makeOrder()]) })
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(screen.getByText("Référence de l'ordre")).toBeInTheDocument()
    expect(screen.getByText('aaaaaaaa…')).toBeInTheDocument()
  })

  it('OU6: a name-only patient shows the truthful limited-identity notice, not blocked from the list', async () => {
    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([
        makeOrder({
          patient: makePatient({ date_of_birth: null, contact_channel_type: null, contact_value: null }),
        }),
      ]),
    })
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(
      screen.getByText('Identité limitée — vérifiez le patient avant de continuer.'),
    ).toBeInTheDocument()
  })

  it('OU7: two orders for patients with byte-identical demographics remain two distinct rows', async () => {
    const patientC = makePatient({ patient_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' })
    const patientD = makePatient({ patient_id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd' })
    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([
        makeOrder({ id: 'order-c', patient: patientC }),
        makeOrder({ id: 'order-d', patient: patientD }),
      ]),
    })
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findAllByText('Fatouma Robleh')).toHaveLength(2)
    const rows = document.querySelectorAll('[data-patient-id]')
    expect(rows).toHaveLength(2)
    expect(rows[0].getAttribute('data-patient-id')).not.toBe(rows[1].getAttribute('data-patient-id'))
  })

  it('falls back to a truthful empty state with no orders', async () => {
    const client = makeClient({ listOrders: vi.fn().mockResolvedValue([]) })
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findByText('Aucun ordre de laboratoire')).toBeInTheDocument()
  })

  it('OP1: an assigned order shows the destination lab name, never an action button', async () => {
    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([
        makeOrder({
          status: 'SELECTED',
          lab_assignment: {
            lab_organization_id: 'lab-1',
            lab_organization_name: 'Laboratoire Central',
            selected_at: '2026-09-17T09:00:00Z',
          },
        }),
      ]),
    })
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findByText('Laboratoire Central')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Choisir un laboratoire/ })).not.toBeInTheDocument()
  })

  it('OP2/§36: an unassigned order shows "Non attribué" with a routing action', async () => {
    const client = makeClient({ listOrders: vi.fn().mockResolvedValue([makeOrder()]) })
    render(<StaffApp client={client} initialSection="orders" />)

    expect(await screen.findByText('Non attribué')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Choisir un laboratoire pour Fatouma Robleh' }),
    ).toBeInTheDocument()
  })

  it('a rejected/cancelled order with no destination shows a plain dash, never a routing action', async () => {
    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([makeOrder({ status: 'REJECTED' })]),
    })
    render(<StaffApp client={client} initialSection="orders" />)

    await screen.findByText('Fatouma Robleh')
    expect(screen.queryByRole('button', { name: /Choisir un laboratoire/ })).not.toBeInTheDocument()
  })

  it('OP3/§37: choosing a lab directly from this page loads patient+tests+lab and updates the row on success', async () => {
    const getOrder = vi
      .fn()
      .mockResolvedValueOnce(makeFullOrder())
      .mockResolvedValueOnce(
        makeFullOrder({
          status: 'SELECTED',
          lab_assignment: {
            lab_organization_id: 'lab-1',
            lab_organization_name: 'Laboratoire Central',
            selected_at: '2026-09-17T09:00:00Z',
          },
        }),
      )
    const getEligibleLabs = vi.fn().mockResolvedValue([makeEligibleLab()])
    const assignLab = vi.fn().mockResolvedValue({
      lab_order_id: 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa',
      lab_organization_id: 'lab-1',
      selected_at: '2026-09-17T09:00:00Z',
    })
    const client = makeClient({
      listOrders: vi.fn().mockResolvedValue([makeOrder()]),
      getOrder,
      getEligibleLabs,
      assignLab,
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="orders" />)

    await user.click(
      await screen.findByRole('button', { name: 'Choisir un laboratoire pour Fatouma Robleh' }),
    )

    // §38: patient + ordered tests are shown alongside the lab choice, not the lab alone.
    expect(await screen.findAllByText('Fatouma Robleh')).toHaveLength(2)
    expect(screen.getByText(/Glycémie/)).toBeInTheDocument()
    expect(screen.getByText('Laboratoire Central')).toBeInTheDocument()

    await user.click(screen.getByRole('radio'))
    await user.click(screen.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }))

    await screen.findAllByText('Laboratoire Central')
    expect(screen.queryByText('Non attribué')).not.toBeInTheDocument()
    expect(getOrder).toHaveBeenCalledWith('provider-a', 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa')
  })
})
