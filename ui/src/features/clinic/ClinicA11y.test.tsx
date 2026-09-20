import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { axe } from 'jest-axe'
import { StaffApp } from '../staff/StaffApp'
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
      patient: {
        patient_id: 'patient-3333',
        display_name: 'Amina Hassan',
        date_of_birth: '1990-04-12',
        contact_channel_type: 'PHONE',
        contact_value: '+25377100002',
      },
      status: 'IN_PROGRESS' as const,
      created_at: '2026-09-15T07:00:00Z',
      item_count: 2,
      lab_assignment: null,
    },
  ]

  beforeEach(() => {
    mockClient = {
      getMe: vi.fn().mockResolvedValue(sampleMe),
      listOrders: vi.fn().mockResolvedValue(sampleOrders),
      getOrder: vi.fn(),
      getOrderResults: vi.fn(),
      listResultReviews: vi.fn().mockResolvedValue({
        provider_organization_id: 'prov-1',
        entries: [
          {
            review_id: 'review-1',
            order_id: 'ord-1111-2222',
            release_version_id: 'rel-v1',
            release_version_number: 1,
            is_amendment: false,
            released_at: '2026-09-15T08:30:00Z',
            legacy_unversioned: false,
            review_status: 'PENDING',
            reviewed_at: null,
            patient: sampleOrders[0].patient,
            order_created_at: '2026-09-15T07:00:00Z',
          },
        ],
      }),
      getReleaseVersions: vi.fn().mockResolvedValue({
        order_id: 'ord-1111-2222',
        versions: [
          {
            release_version_id: 'rel-v1',
            release_version_number: 1,
            is_amendment: false,
            released_at: '2026-09-15T08:30:00Z',
            review_status: 'PENDING',
            reviewed_at: null,
            reviewed_by_practitioner_reference_id: null,
          },
        ],
      }),
      getReleaseVersion: vi.fn().mockResolvedValue({
        order_id: 'ord-1111-2222',
        release_version_id: 'rel-v1',
        release_version_number: 1,
        is_amendment: false,
        patient: sampleOrders[0].patient,
        review_status: 'PENDING',
        reviewed_at: null,
        reviewed_by_practitioner_reference_id: null,
        results: [],
      }),
      reviewReleaseVersionNoFollowUp: vi.fn(),
      reviewReleaseVersionFollowUp: vi.fn(),
      reviewLegacyOrderNoFollowUp: vi.fn(),
      reviewLegacyOrderFollowUp: vi.fn(),
      createPatientWithOutcome: vi.fn(),
      listPatients: vi.fn().mockResolvedValue([]),
      searchPatients: vi.fn().mockResolvedValue([]),
      getPatientRegistration: vi.fn().mockResolvedValue(null),
      getPatientReference: vi.fn(),
    } as unknown as ExchangeClient
  })

  it('Clinic home view has no detectable axe violations', async () => {
    const { container } = render(<StaffApp client={mockClient} initialSection="today" />)
    await screen.findByRole('heading', { name: 'Accueil Clinique' })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Clinic orders view has no detectable axe violations', async () => {
    const { container } = render(<StaffApp client={mockClient} initialSection="orders" />)
    await screen.findByRole('heading', { name: 'Ordres de laboratoire' })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Clinic patients view has no detectable axe violations', async () => {
    const { container } = render(<StaffApp client={mockClient} initialSection="patients" />)
    await screen.findByRole('heading', { name: 'Patients' })
    await screen.findByText('Aucun patient trouvé')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Clinic patient creation form has no detectable axe violations', async () => {
    const { container } = render(<StaffApp client={mockClient} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await screen.findByRole('heading', { name: 'Nouveau patient' })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Clinic native patient workspace has no detectable axe violations', async () => {
    mockClient.listPatients = vi.fn().mockResolvedValue([
      {
        patient_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        provider_organization_id: 'provider-a',
        display_name: 'Patient Synthétique',
        date_of_birth: null,
        contact_channel_type: 'PHONE',
        contact_value: '+25377000000',
        primary_patient_reference_id: '11111111-1111-4111-8111-111111111111',
        created_at: new Date().toISOString(),
      },
    ])
    const { container } = render(<StaffApp client={mockClient} initialSection="patients" />)
    await screen.findByText('Patient Synthétique')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Ouvrir' }))
    await screen.findByRole('heading', { name: 'Patient Synthétique' })
    expect(await axe(container)).toHaveNoViolations()
  })

  it('Clinic results view has no detectable axe violations', async () => {
    const { container } = render(<StaffApp client={mockClient} initialSection="results" />)
    await screen.findByRole('heading', { name: 'Résultats de laboratoire' })
    expect(await axe(container)).toHaveNoViolations()
  })
})
