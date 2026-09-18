import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { axe } from 'jest-axe'
import { LabApp } from './LabApp'
import type { ExchangeClient } from '../../api/exchangeClient'

describe('Lab Accessibility', () => {
  let mockClient: ExchangeClient

  const sampleMe = {
    identity_id: 'id-1',
    issuer: 'issuer',
    subject: 'sub',
    display_name: 'Technicien Ali',
    email: 'ali@lab.dj',
    platform_admin: false,
    memberships: [
      {
        organization_id: 'lab-1',
        organization_name: 'Laboratoire Central',
        organization_type: 'LAB',
        organization_status: 'ACTIVE',
        membership_status: 'ACTIVE',
        roles: ['LAB_TECH'],
      },
    ],
  }

  const sampleOrders = [
    {
      order_id: 'order-1',
      status: 'SELECTED' as const,
      selected_at: '2026-09-18T08:00:00Z',
      updated_at: '2026-09-18T08:00:00Z',
      patient_display_name: 'Amina Hassan',
      patient: {
        patient_id: 'patient-1',
        display_name: 'Amina Hassan',
        date_of_birth: '1990-04-12',
      },
      ordering_provider_name: 'Clinique de la Paix',
      requested_tests: [
        {
          catalog_code_snapshot: 'GLU',
          catalog_display_name_snapshot: 'Glycémie à jeun',
          selected_price_amount: null,
          selected_price_currency: null,
        },
      ],
      collection_recorded_at: null,
    },
  ]

  beforeEach(() => {
    mockClient = {
      getMe: vi.fn().mockResolvedValue(sampleMe),
      listLabOrders: vi.fn().mockResolvedValue(sampleOrders),
      getLabOrder: vi.fn().mockResolvedValue(sampleOrders[0]),
      acceptLabOrder: vi.fn(),
      rejectLabOrder: vi.fn(),
    } as unknown as ExchangeClient
  })

  it('Lab home view has no detectable axe violations', async () => {
    const { container } = render(<LabApp client={mockClient} initialTab="home" />)
    await screen.findByRole('heading', { name: 'Accueil Laboratoire' })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Lab orders queue view has no detectable axe violations', async () => {
    const { container } = render(<LabApp client={mockClient} initialTab="orders" />)
    await screen.findByRole('heading', { name: 'Ordres de laboratoire' })
    await screen.findByText('Amina Hassan')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Lab order detail panel has no detectable axe violations', async () => {
    const { container } = render(<LabApp client={mockClient} initialTab="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('button', { name: "Accepter l'ordre" })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Lab reject confirmation step has no detectable axe violations', async () => {
    const { container } = render(<LabApp client={mockClient} initialTab="orders" />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Refuser l'ordre" }))
    await screen.findByText(/Refuser cet ordre \?/)
    expect(await axe(container)).toHaveNoViolations()
  })
})
