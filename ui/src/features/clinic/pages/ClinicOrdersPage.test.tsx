import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../../api/exchangeClient'
import type { ExchangeMeResponse, LabOrderSummary, PatientIdentitySummary } from '../../../api/exchangeTypes'
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
    render(<ClinicApp client={client} initialTab="orders" />)

    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(screen.queryByText('Réf. Patient')).not.toBeInTheDocument()
    expect(screen.queryByText(/^ref-11111111/)).not.toBeInTheDocument()
  })

  it('OU2/OU3: shows DOB and contact when known', async () => {
    const client = makeClient({ listOrders: vi.fn().mockResolvedValue([makeOrder()]) })
    render(<ClinicApp client={client} initialTab="orders" />)

    expect(await screen.findByText('Né(e) le 2 juin 1988')).toBeInTheDocument()
    expect(screen.getByText('Téléphone : +25377100001')).toBeInTheDocument()
  })

  it('OU4: the order technical reference remains available, as a secondary column', async () => {
    const client = makeClient({ listOrders: vi.fn().mockResolvedValue([makeOrder()]) })
    render(<ClinicApp client={client} initialTab="orders" />)

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
    render(<ClinicApp client={client} initialTab="orders" />)

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
    render(<ClinicApp client={client} initialTab="orders" />)

    expect(await screen.findAllByText('Fatouma Robleh')).toHaveLength(2)
    const rows = document.querySelectorAll('[data-patient-id]')
    expect(rows).toHaveLength(2)
    expect(rows[0].getAttribute('data-patient-id')).not.toBe(rows[1].getAttribute('data-patient-id'))
  })

  it('falls back to a truthful empty state with no orders', async () => {
    const client = makeClient({ listOrders: vi.fn().mockResolvedValue([]) })
    render(<ClinicApp client={client} initialTab="orders" />)

    expect(await screen.findByText('Aucun ordre de laboratoire')).toBeInTheDocument()
  })
})
