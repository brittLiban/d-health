import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ClinicApp } from './ClinicApp'
import { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'

describe('ClinicApp', () => {
  let mockClient: ExchangeClient

  const sampleMe = {
    identity_id: 'id-1',
    issuer: 'issuer',
    subject: 'sub',
    display_name: 'Dr. Ayan',
    email: 'ayan@clinic.dj',
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
        date_of_birth: null,
        contact_channel_type: null,
        contact_value: null,
      },
      status: 'IN_PROGRESS' as const,
      created_at: '2026-09-15T07:00:00Z',
      item_count: 2,
    },
    {
      id: 'ord-5555-6666',
      provider_organization_id: 'prov-1',
      patient_reference_id: 'pat-ref-7777',
      practitioner_reference_id: 'prac-4444',
      patient: {
        patient_id: 'patient-7777',
        display_name: 'Warsame Ali',
        date_of_birth: '1975-11-20',
        contact_channel_type: 'PHONE',
        contact_value: '+25377222333',
      },
      status: 'COMPLETED' as const,
      created_at: '2026-09-15T08:00:00Z',
      item_count: 1,
    },
  ]

  beforeEach(() => {
    mockClient = {
      getMe: vi.fn().mockResolvedValue(sampleMe),
      listOrders: vi.fn().mockResolvedValue(sampleOrders),
      getOrder: vi.fn(),
      getOrderResults: vi.fn().mockResolvedValue({
        order_id: 'ord-5555-6666',
        order_status: 'COMPLETED',
        patient: {
          patient_id: 'patient-7777',
          display_name: 'Warsame Ali',
          date_of_birth: '1975-11-20',
          contact_channel_type: 'PHONE',
          contact_value: '+25377222333',
        },
        release_version_id: 'rel-v1',
        release_version_number: 1,
        is_amendment: false,
        results: [
          {
            item_id: 'item-1',
            catalog_code_snapshot: 'GLU',
            catalog_display_name_snapshot: 'Glycémie à jeun',
            entry_completed_at: '2026-09-15T08:30:00Z',
            observations: [
              {
                observation_id: 'obs-1',
                source_code: 'GLU',
                source_system: 'LOINC',
                display_label_snapshot: 'Glycémie',
                result_type: 'NUMERIC',
                numeric_value: '5.4',
                numeric_unit: 'mmol/L',
                reference_range_low: '3.9',
                reference_range_high: '6.1',
                text_value: null,
                recorded_at: '2026-09-15T08:30:00Z',
              },
            ],
          },
        ],
      }),
      getReleaseVersions: vi.fn().mockResolvedValue({
        order_id: 'ord-5555-6666',
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
      createPatientWithOutcome: vi.fn(),
      listPatients: vi.fn().mockResolvedValue([]),
      searchPatients: vi.fn().mockResolvedValue([]),
      getPatientRegistration: vi.fn().mockResolvedValue(null),
      getPatientReference: vi.fn(),
    } as unknown as ExchangeClient
  })

  it('renders home page with truthful order and result counts derived from Exchange API', async () => {
    render(<ClinicApp client={mockClient} />)

    expect(await screen.findByRole('heading', { name: 'Accueil Clinique' })).toBeInTheDocument()
    expect(await screen.findAllByText('1')).toHaveLength(2) // 1 active order, 1 completed order
    expect(screen.getByText('Ordres actifs')).toBeInTheDocument()
    expect(screen.getByText('Résultats disponibles')).toBeInTheDocument()
    expect(screen.getByText('À revoir')).toBeInTheDocument()
  })

  it('DH-CLINIC-005: navigates to "Ordres de laboratoire" and renders the orders table with human patient identity, never a raw reference token', async () => {
    const user = userEvent.setup()
    render(<ClinicApp client={mockClient} />)

    await user.click(screen.getByRole('button', { name: 'Ordres de laboratoire' }))
    expect(await screen.findByRole('heading', { name: 'Ordres de laboratoire' })).toBeInTheDocument()

    // The server-resolved Patient directory identity is the primary column now -- a raw
    // PatientReference token is never shown as identity (DH-CLINIC-005 §22/AC6).
    expect(screen.getByText('Amina Hassan')).toBeInTheDocument()
    expect(screen.getByText('Warsame Ali')).toBeInTheDocument()
    expect(screen.queryByText('pat-ref-…')).not.toBeInTheDocument()
    expect(screen.queryByText(/pat-ref-3333|pat-ref-7777/)).not.toBeInTheDocument()
    expect(screen.getByText("En cours d'analyse")).toBeInTheDocument()
  })

  it('navigates to the native Patients workflow', async () => {
    const user = userEvent.setup()
    render(<ClinicApp client={mockClient} />)

    await user.click(screen.getByRole('button', { name: 'Patients' }))
    expect(await screen.findByRole('heading', { name: 'Patients' })).toBeInTheDocument()
    expect(screen.getByLabelText('Rechercher un patient')).toBeInTheDocument()
    expect(await screen.findByText('Aucun patient trouvé')).toBeInTheDocument()
  })

  it('navigates to "Résultats" and allows inspecting released observations for a completed order', async () => {
    const user = userEvent.setup()
    render(<ClinicApp client={mockClient} />)

    await user.click(screen.getByRole('button', { name: 'Résultats' }))
    expect(await screen.findByRole('heading', { name: 'Résultats de laboratoire' })).toBeInTheDocument()

    // Click on Consulter for completed order
    await user.click(screen.getByRole('button', { name: /^Consulter/ }))

    expect(await screen.findByText(/Glycémie à jeun/)).toBeInTheDocument()
    expect(screen.getByText('5.4')).toBeInTheDocument()
    expect(screen.getByText('mmol/L')).toBeInTheDocument()
    expect(screen.getByText('3.9 - 6.1')).toBeInTheDocument()
    expect(screen.getByText('Version 1')).toBeInTheDocument()
    expect(screen.getByText('À examiner')).toBeInTheDocument()
  })

  it('renders truthful unauthenticated state on 401 without mock fallback', async () => {
    vi.mocked(mockClient.getMe).mockRejectedValueOnce(
      new ExchangeApiError(401, 'UNAUTHORIZED', 'credentials missing or invalid'),
    )

    render(<ClinicApp client={mockClient} />)

    expect(await screen.findByText('Session non authentifiée')).toBeInTheDocument()
    expect(
      screen.getByText("Veuillez vous connecter pour accéder à l'application DHealth Clinique."),
    ).toBeInTheDocument()
  })

  it('renders truthful empty state when clinician holds no active provider memberships', async () => {
    vi.mocked(mockClient.getMe).mockResolvedValueOnce({
      ...sampleMe,
      memberships: [],
    })

    render(<ClinicApp client={mockClient} />)

    expect(await screen.findByText('Aucune organisation clinique active')).toBeInTheDocument()
  })

  it('renders truthful empty state when user holds only LAB memberships or lacks CLINICIAN role', async () => {
    vi.mocked(mockClient.getMe).mockResolvedValueOnce({
      ...sampleMe,
      memberships: [
        {
          organization_id: 'lab-org-1',
          organization_name: 'Laboratoire Central',
          organization_type: 'LAB',
          organization_status: 'ACTIVE',
          membership_status: 'ACTIVE',
          roles: ['LAB_TECH'],
        },
        {
          organization_id: 'clinic-org-2',
          organization_name: 'Clinique B',
          organization_type: 'PROVIDER',
          organization_status: 'ACTIVE',
          membership_status: 'ACTIVE',
          roles: ['ORG_ADMIN'], // lacks CLINICIAN role
        },
      ],
    })

    render(<ClinicApp client={mockClient} />)

    expect(await screen.findByText('Aucune organisation clinique active')).toBeInTheDocument()
  })

  it('renders recoverable ErrorState when Exchange API network call fails', async () => {
    vi.mocked(mockClient.getMe).mockRejectedValueOnce(
      new ExchangeApiError(0, 'NETWORK_ERROR', 'Échec de connexion réseau'),
    )

    render(<ClinicApp client={mockClient} />)

    expect(await screen.findByText('Connexion impossible')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Réessayer' })).toBeInTheDocument()

    // Clicking retry calls reload
    vi.mocked(mockClient.getMe).mockResolvedValueOnce(sampleMe)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Réessayer' }))

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Accueil Clinique' })).toBeInTheDocument()
    })
  })
})
