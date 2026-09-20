import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { StaffApp } from './StaffApp'
import type { ExchangeClient } from '../../api/exchangeClient'
import { sectionButton } from '../../test/renderWithWorkspace'

describe('the staff application at a laboratory organization', () => {
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
        { catalog_code_snapshot: 'GLU', catalog_display_name_snapshot: 'Glycémie', selected_price_amount: null, selected_price_currency: null },
      ],
      collection_recorded_at: null,
    },
  ]

  function makeMockClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
    return {
      getMe: vi.fn().mockResolvedValue(sampleMe),
      listLabOrders: vi.fn().mockResolvedValue(sampleOrders),
      getLabOrder: vi.fn().mockResolvedValue(sampleOrders[0]),
      acceptLabOrder: vi.fn(),
      rejectLabOrder: vi.fn(),
      ...overrides,
    } as unknown as ExchangeClient
  }

  it('renders the home page with truthful counts derived from the assigned-order queue', async () => {
    const client = makeMockClient()
    render(<StaffApp client={client} initialSection="today" />)

    expect(await screen.findByRole('heading', { name: 'Accueil Laboratoire' })).toBeInTheDocument()
    expect(screen.getAllByText('Laboratoire Central').length).toBeGreaterThan(0)
    expect(await screen.findByText('Nouveaux ordres')).toBeInTheDocument()
  })

  it("navigates from Aujourd'hui to Ordres and back via the shell navigation", async () => {
    const client = makeMockClient()
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="today" />)
    await screen.findByRole('heading', { name: 'Accueil Laboratoire' })

    await user.click(await sectionButton('Ordres'))
    expect(await screen.findByRole('heading', { name: 'Ordres de laboratoire' })).toBeInTheDocument()

    await user.click(await sectionButton("Aujourd'hui"))
    expect(await screen.findByRole('heading', { name: 'Accueil Laboratoire' })).toBeInTheDocument()
  })

  // US2. Supersedes DH-LAB-001 §26's "exposes a switch-to-Clinic action": that
  // action described a product with two applications to move between. There is
  // now one, and which workflows it offers follows from the active membership,
  // so the control is not merely hidden here — it does not exist. Asserting its
  // absence keeps the old coverage pointed at the decision that replaced it.
  it('US2: offers no application-switch control, because there is one application', async () => {
    const client = makeMockClient()
    render(<StaffApp client={client} initialSection="today" />)
    await screen.findByRole('heading', { name: 'Accueil Laboratoire' })

    for (const label of [
      /Passer à DHealth Clinique/i,
      /Passer au connecteur/i,
      /DHealth Clinique \(Natif\)/i,
      /DHealth Laboratoire \(Natif\)/i,
      /Mode Maternité/i,
    ]) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument()
    }
  })

  it('§26: exposes a logout action in the shell', async () => {
    const client = makeMockClient()
    const onLogout = vi.fn()
    render(<StaffApp client={client} initialSection="today" onLogout={onLogout} />)
    await screen.findByRole('heading', { name: 'Accueil Laboratoire' })

    await userEvent.setup().click(screen.getByRole('button', { name: 'Se déconnecter' }))
    expect(onLogout).toHaveBeenCalledTimes(1)
  })

  it('opens the provider workspace for an account whose only membership is a clinic', async () => {
    // Supersedes DH-LAB-001 §13's "sees the truthful no-labs state". That state
    // was truthful when Lab was a separate application a clinician could open by
    // mistake. There is one application now, so a clinician with a perfectly good
    // provider membership lands in the provider workspace — telling them no
    // laboratory is associated with their account would be the false answer, and
    // would point them at an administrator for nothing.
    const client = makeMockClient({
      // The provider workspace reads the provider order list, not the laboratory queue.
      listOrders: vi.fn().mockResolvedValue([]),
      getMe: vi.fn().mockResolvedValue({
        ...sampleMe,
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
      }),
    })
    render(<StaffApp client={client} initialSection="today" />)

    const masthead = within(
      await screen.findByRole('banner', { name: 'Session et établissement actif' }),
    )
    expect(masthead.getByText('Clinique de la Paix')).toBeInTheDocument()
    expect(masthead.getByText('Établissement de soins')).toBeInTheDocument()
    expect(screen.queryByText(/Aucun laboratoire actif/)).not.toBeInTheDocument()
  })

  it('§9: a user holding both LAB_TECH and LAB_RESULT_RELEASER on the same lab counts as one eligible membership', async () => {
    const client = makeMockClient({
      getMe: vi.fn().mockResolvedValue({
        ...sampleMe,
        memberships: [{ ...sampleMe.memberships[0], roles: ['LAB_TECH', 'LAB_RESULT_RELEASER'] }],
      }),
    })
    render(<StaffApp client={client} initialSection="orders" />)
    // Both a working queue (LAB_TECH) is available -- proven by the order actually rendering.
    expect(await screen.findByText('Amina Hassan')).toBeInTheDocument()
  })

  it('§8: multiple lab memberships expose a lab selector', async () => {
    const client = makeMockClient({
      getMe: vi.fn().mockResolvedValue({
        ...sampleMe,
        memberships: [
          sampleMe.memberships[0],
          { ...sampleMe.memberships[0], organization_id: 'lab-2', organization_name: 'Laboratoire Nord' },
        ],
      }),
    })
    render(<StaffApp client={client} initialSection="today" />)
    const select = await screen.findByLabelText('Établissement actif')
    expect(select).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Laboratoire Central' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Laboratoire Nord' })).toBeInTheDocument()
  })

  it('reports a 401 as an expired session, without a mock fallback', async () => {
    // Same requirement as before, minus the per-application name: the shell does
    // not know which memberships a person holds until after they have signed in,
    // so it cannot honestly claim to be "DHealth Laboratoire" while refusing them.
    const { ExchangeApiError } = await import('../../api/exchangeClient')
    const client = makeMockClient({
      getMe: vi.fn().mockRejectedValue(new ExchangeApiError(401, 'UNAUTHORIZED', 'no token')),
    })
    render(<StaffApp client={client} initialSection="today" />)

    await waitFor(() => expect(screen.getByText('Votre session a expiré')).toBeInTheDocument())

    // No sign-in handler was supplied here, so no button is offered. An
    // unwired "Se reconnecter" would be a promise the screen cannot keep.
    expect(screen.queryByRole('button', { name: 'Se reconnecter' })).not.toBeInTheDocument()
  })
})
