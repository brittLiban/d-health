import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { StaffApp } from './StaffApp'
import { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import { sectionButton } from '../../test/renderWithWorkspace'

describe('the staff application at a provider organization', () => {
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
      lab_assignment: null,
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
      lab_assignment: null,
    },
  ]

  const sampleReleaseVersionContent = {
    order_id: 'ord-5555-6666',
    release_version_id: 'rel-v1',
    release_version_number: 1,
    is_amendment: false,
    patient: {
      patient_id: 'patient-7777',
      display_name: 'Warsame Ali',
      date_of_birth: '1975-11-20',
      contact_channel_type: 'PHONE',
      contact_value: '+25377222333',
    },
    review_status: 'PENDING',
    reviewed_at: null,
    reviewed_by_practitioner_reference_id: null,
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
  }

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
            order_id: 'ord-5555-6666',
            release_version_id: 'rel-v1',
            release_version_number: 1,
            is_amendment: false,
            released_at: '2026-09-15T08:30:00Z',
            legacy_unversioned: false,
            review_status: 'PENDING',
            reviewed_at: null,
            patient: sampleReleaseVersionContent.patient,
            order_created_at: '2026-09-15T08:00:00Z',
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
      getReleaseVersion: vi.fn().mockResolvedValue(sampleReleaseVersionContent),
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

  it('renders home page with truthful order and result counts derived from Exchange API', async () => {
    render(<StaffApp client={mockClient} />)

    expect(await screen.findByRole('heading', { name: 'Accueil Clinique' })).toBeInTheDocument()
    expect(await screen.findAllByText('1')).toHaveLength(2) // 1 active order, 1 completed order
    expect(screen.getByText('Ordres actifs')).toBeInTheDocument()
    expect(screen.getByText('Résultats disponibles')).toBeInTheDocument()
    expect(screen.getByText('À revoir')).toBeInTheDocument()
  })

  it('DH-CLINIC-005: navigates to "Ordres de laboratoire" and renders the orders table with human patient identity, never a raw reference token', async () => {
    const user = userEvent.setup()
    render(<StaffApp client={mockClient} />)

    await user.click(await sectionButton('Ordres'))
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
    render(<StaffApp client={mockClient} />)

    await user.click(await sectionButton('Patients'))
    expect(await screen.findByRole('heading', { name: 'Patients' })).toBeInTheDocument()
    expect(screen.getByLabelText('Rechercher un patient')).toBeInTheDocument()
    expect(await screen.findByText('Aucun patient trouvé')).toBeInTheDocument()
  })

  it('navigates to "Résultats" and allows inspecting released observations for a completed order', async () => {
    const user = userEvent.setup()
    render(<StaffApp client={mockClient} />)

    await user.click(await sectionButton('Résultats'))
    expect(await screen.findByRole('heading', { name: 'Résultats de laboratoire' })).toBeInTheDocument()

    // Open the pending review queue entry for the completed order
    await user.click(screen.getByRole('button', { name: /^Examiner/ }))

    expect(await screen.findByText(/Glycémie à jeun/)).toBeInTheDocument()
    expect(screen.getByText('5.4')).toBeInTheDocument()
    expect(screen.getByText('mmol/L')).toBeInTheDocument()
    expect(screen.getByText('3.9 - 6.1')).toBeInTheDocument()
    expect(screen.getByText('Version 1')).toBeInTheDocument()
    expect(screen.getByText('À examiner')).toBeInTheDocument()
  })

  it('reports an expired session as an expired session, not as an empty account', async () => {
    // Was "renders truthful unauthenticated state on 401". Same requirement, new
    // wording: the shell no longer has a "DHealth Clinique" to name, and telling
    // someone whose token expired that no establishment is attached to their
    // account would send them to an administrator to fix a problem they do not
    // have.
    vi.mocked(mockClient.getMe).mockRejectedValueOnce(
      new ExchangeApiError(401, 'UNAUTHORIZED', 'credentials missing or invalid'),
    )

    const onReauthenticate = vi.fn()
    render(<StaffApp client={mockClient} onReauthenticate={onReauthenticate} />)

    expect(await screen.findByText('Votre session a expiré')).toBeInTheDocument()

    // The button must actually start a sign-in. Re-requesting the session would
    // only produce the same 401, so a control wired to a retry would look like it
    // works and would not.
    await userEvent.setup().click(screen.getByRole('button', { name: 'Se reconnecter' }))
    expect(onReauthenticate).toHaveBeenCalledTimes(1)
    expect(
      screen.queryByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).not.toBeInTheDocument()
  })

  it('states truthfully that no establishment is attached when the account holds none', async () => {
    vi.mocked(mockClient.getMe).mockResolvedValueOnce({
      ...sampleMe,
      memberships: [],
    })

    render(<StaffApp client={mockClient} />)

    expect(
      await screen.findByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).toBeInTheDocument()
  })

  it('opens the laboratory workspace for an account whose only usable membership is a laboratory', async () => {
    // This supersedes "renders truthful empty state when user holds only LAB
    // memberships". Under two separate applications, opening Clinic with only a
    // laboratory membership was a dead end, and the honest thing to show was an
    // empty state. There is one application now, so the same account simply lands
    // in the workspace its membership actually unlocks — the empty state would now
    // be the false answer.
    //
    // The second membership is excluded because it unlocks nothing this build
    // renders. It used to be an ORG_ADMIN-only provider membership, which was
    // excluded for that same reason -- until DHEALTH-NATIVE-ACCESS-001 gave ORG_ADMIN
    // a screen (Équipe et accès), at which point such a membership became genuinely
    // usable. The fixture therefore names a role with no surface behind it, so this
    // test still exercises the exclusion rule rather than passing for a reason that
    // has since stopped being true. The ORG_ADMIN case is asserted on its own, below.
    //
    // The laboratory workspace reads the laboratory queue, which this provider-
    // shaped client had no reason to stub until now.
    vi.mocked(mockClient).listLabOrders = vi.fn().mockResolvedValue([])
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
          roles: [], // no role at all: nothing here for this person to do
        },
      ],
    })

    render(<StaffApp client={mockClient} />)

    const masthead = within(await screen.findByRole('banner', { name: 'Session et établissement actif' }))
    expect(masthead.getByText('Laboratoire Central')).toBeInTheDocument()
    expect(masthead.getByText("Laboratoire d'analyses")).toBeInTheDocument()

    // The excluded provider membership is not offered anywhere, so it cannot be
    // selected and acted under.
    expect(screen.queryByText('Clinique B')).not.toBeInTheDocument()
    expect(
      screen.queryByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).not.toBeInTheDocument()
  })

  it('opens the workspace for an administrator who holds only ORG_ADMIN', async () => {
    // DHEALTH-NATIVE-ACCESS-001. Before this ticket the same account reached
    // "Aucun établissement actif n'est rattaché à votre compte" — which was the
    // user-facing shape of DHEALTH-NATIVE-SEC-001's P1: a facility's administrator
    // held real, server-honoured authority and could not reach a single screen with
    // it, so withdrawing a colleague's access needed a database administrator.
    //
    // They land on Équipe et accès and get nothing clinical: no Aujourd'hui relay,
    // no Patients, no Ordres, no Résultats. ORG_ADMIN is not clinical authority, and
    // `require_clinician` would refuse it regardless of what was rendered.
    vi.mocked(mockClient).listMemberships = vi.fn().mockResolvedValue([])
    vi.mocked(mockClient.getMe).mockResolvedValueOnce({
      ...sampleMe,
      memberships: [
        {
          organization_id: 'clinic-org-1',
          organization_name: 'Clinique A',
          organization_type: 'PROVIDER',
          organization_status: 'ACTIVE',
          membership_status: 'ACTIVE',
          roles: ['ORG_ADMIN'],
        },
      ],
    })

    render(<StaffApp client={mockClient} />)

    const nav = within(await screen.findByRole('navigation', { name: 'Navigation principale' }))
    expect(nav.getByRole('button', { name: 'Équipe et accès' })).toBeInTheDocument()
    for (const absent of ["Aujourd'hui", 'Patients', 'Ordres', 'Résultats']) {
      expect(nav.queryByRole('button', { name: absent })).toBeNull()
    }
    expect(
      screen.queryByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).not.toBeInTheDocument()
  })

  it('renders recoverable ErrorState when Exchange API network call fails', async () => {
    vi.mocked(mockClient.getMe).mockRejectedValueOnce(
      new ExchangeApiError(0, 'NETWORK_ERROR', 'Échec de connexion réseau'),
    )

    render(<StaffApp client={mockClient} />)

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
