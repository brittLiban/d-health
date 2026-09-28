import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { axe } from 'jest-axe'
import { StaffApp } from '../staff/StaffApp'
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
        capabilities: ['lab.queue.work'],
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
      recordLabOrderCollection: vi.fn(),
      startProcessingLabOrder: vi.fn(),
      getResultEntryWorkspace: vi.fn().mockResolvedValue({
        order_id: 'order-1',
        order_status: 'IN_PROGRESS',
        items: [],
      }),
      recordNativeResultValues: vi.fn(),
      completeResultEntry: vi.fn(),
    } as unknown as ExchangeClient
  })

  it('Lab home view has no detectable axe violations', async () => {
    const { container } = render(<StaffApp client={mockClient} initialSection="today" />)
    await screen.findByRole('heading', { name: 'Accueil Laboratoire' })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Lab orders queue view has no detectable axe violations', async () => {
    const { container } = render(<StaffApp client={mockClient} initialSection="orders" />)
    await screen.findByRole('heading', { name: 'Ordres de laboratoire' })
    await screen.findByText('Amina Hassan')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Lab order detail panel has no detectable axe violations', async () => {
    const { container } = render(<StaffApp client={mockClient} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByRole('button', { name: "Accepter l'ordre" })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Lab reject confirmation step has no detectable axe violations', async () => {
    const { container } = render(<StaffApp client={mockClient} initialSection="orders" />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Refuser l'ordre" }))
    await screen.findByText(/Refuser cet ordre \?/)
    expect(await axe(container)).toHaveNoViolations()
  })

  // -- DH-LAB-002 §69: collection/processing confirmation accessibility ---------------------

  it('Lab collection confirmation step has no detectable axe violations', async () => {
    mockClient = {
      ...mockClient,
      listLabOrders: vi.fn().mockResolvedValue([{ ...sampleOrders[0], status: 'ACCEPTED' }]),
      getLabOrder: vi.fn().mockResolvedValue({ ...sampleOrders[0], status: 'ACCEPTED' }),
    } as unknown as ExchangeClient
    const { container } = render(<StaffApp client={mockClient} initialSection="orders" />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: 'Enregistrer la collecte' }))
    await screen.findByText(/Enregistrer la collecte \?/)
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Lab start-processing confirmation step has no detectable axe violations', async () => {
    const acceptedWithCollection = {
      ...sampleOrders[0],
      status: 'ACCEPTED' as const,
      collection_recorded_at: '2026-09-18T10:42:00Z',
    }
    mockClient = {
      ...mockClient,
      listLabOrders: vi.fn().mockResolvedValue([acceptedWithCollection]),
      getLabOrder: vi.fn().mockResolvedValue(acceptedWithCollection),
    } as unknown as ExchangeClient
    const { container } = render(<StaffApp client={mockClient} initialSection="orders" />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Examiner/ }))
    await user.click(await screen.findByRole('button', { name: "Commencer l'analyse" }))
    await screen.findByText(/Commencer l'analyse \?/)
    expect(await axe(container)).toHaveNoViolations()
  })

  // -- DH-LAB-004 §69: controlled Native result-entry form accessibility --------------------

  it('Lab result-entry form has no detectable axe violations', async () => {
    const inProgress = { ...sampleOrders[0], status: 'IN_PROGRESS' as const }
    mockClient = {
      ...mockClient,
      listLabOrders: vi.fn().mockResolvedValue([inProgress]),
      getLabOrder: vi.fn().mockResolvedValue(inProgress),
      getResultEntryWorkspace: vi.fn().mockResolvedValue({
        order_id: 'order-1',
        order_status: 'IN_PROGRESS',
        items: [
          {
            item_id: 'item-1',
            catalog_code_snapshot: 'GLU',
            catalog_display_name_snapshot: 'Glycémie à jeun',
            template: {
              template_id: 'template-1',
              version: 1,
              is_synthetic: true,
              observations: [
                {
                  definition_id: 'def-1',
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
      }),
    } as unknown as ExchangeClient
    const { container } = render(<StaffApp client={mockClient} initialSection="orders" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Examiner/ }))
    await screen.findByText('Saisie des résultats')
    await screen.findByLabelText(/Demo Measure A/)
    expect(await axe(container)).toHaveNoViolations()
  })
})
