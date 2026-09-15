import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { axe } from 'jest-axe'
import { ClinicApp } from './ClinicApp'
import type { ExchangeClient } from '../../api/exchangeClient'

describe('Clinic Accessibility', () => {
  let mockClient: ExchangeClient

  const sampleMe = {
    identity_id: 'id-1',
    issuer: 'issuer',
    subject: 'sub',
    display_name: 'Dr. Farah',
    email: 'farah@clinic.dj',
    platform_admin: false,
    memberships: [
      {
        organization_id: 'prov-1',
        organization_name: 'Clinique de la Paix',
        organization_type: 'PROVIDER',
        organization_status: 'ACTIVE',
        membership_status: 'ACTIVE',
        roles: ['CLINICIAN'],
      },
    ],
  }

  const sampleOrders = [
    {
      id: 'ord-1111-2222',
      provider_organization_id: 'prov-1',
      patient_reference_id: 'pat-ref-3333',
      practitioner_reference_id: 'prac-4444',
      status: 'IN_PROGRESS' as const,
      created_at: '2026-09-15T07:00:00Z',
      item_count: 2,
    },
  ]

  beforeEach(() => {
    mockClient = {
      getMe: vi.fn().mockResolvedValue(sampleMe),
      listOrders: vi.fn().mockResolvedValue(sampleOrders),
      getOrder: vi.fn(),
      getOrderResults: vi.fn().mockResolvedValue({
        order_id: 'ord-1111-2222',
        release_version_id: 'rel-v1',
        release_version_number: 1,
        is_amendment: false,
        review_status: 'PENDING_REVIEW',
        reviewed_at: null,
        reviewed_by_practitioner_reference_id: null,
        results: [],
      }),
      getReleaseVersions: vi.fn().mockResolvedValue({
        order_id: 'ord-1111-2222',
        versions: [],
      }),
      bindPatientReference: vi.fn(),
    } as unknown as ExchangeClient
  })

  it('Clinic home view has no detectable axe violations', async () => {
    const { container } = render(<ClinicApp client={mockClient} initialTab="home" />)
    await screen.findByRole('heading', { name: 'Accueil Clinique' })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Clinic orders view has no detectable axe violations', async () => {
    const { container } = render(<ClinicApp client={mockClient} initialTab="orders" />)
    await screen.findByRole('heading', { name: 'Ordres de laboratoire' })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Clinic patients view has no detectable axe violations', async () => {
    const { container } = render(<ClinicApp client={mockClient} initialTab="patients" />)
    await screen.findByRole('heading', { name: 'Dossiers Patients' })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Clinic results view has no detectable axe violations', async () => {
    const { container } = render(<ClinicApp client={mockClient} initialTab="results" />)
    await screen.findByRole('heading', { name: 'Résultats de laboratoire' })
    expect(await axe(container)).toHaveNoViolations()
  })
})
