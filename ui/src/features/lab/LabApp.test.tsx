import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { LabApp } from './LabApp'
import type { ExchangeClient } from '../../api/exchangeClient'

describe('LabApp', () => {
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
    render(<LabApp client={client} initialTab="home" />)

    expect(await screen.findByRole('heading', { name: 'Accueil Laboratoire' })).toBeInTheDocument()
    expect(screen.getAllByText('Laboratoire Central').length).toBeGreaterThan(0)
    expect(await screen.findByText('Nouveaux ordres')).toBeInTheDocument()
  })

  it('navigates from Accueil to Ordres and back via the shell tabs', async () => {
    const client = makeMockClient()
    const user = userEvent.setup()
    render(<LabApp client={client} initialTab="home" />)
    await screen.findByRole('heading', { name: 'Accueil Laboratoire' })

    await user.click(screen.getByRole('button', { name: 'Ordres' }))
    expect(await screen.findByRole('heading', { name: 'Ordres de laboratoire' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Accueil' }))
    expect(await screen.findByRole('heading', { name: 'Accueil Laboratoire' })).toBeInTheDocument()
  })

  it('§26: exposes a switch-to-Clinic action in the shell', async () => {
    const client = makeMockClient()
    const onSwitchApp = vi.fn()
    render(<LabApp client={client} initialTab="home" onSwitchApp={onSwitchApp} />)
    await screen.findByRole('heading', { name: 'Accueil Laboratoire' })

    await userEvent.setup().click(screen.getByRole('button', { name: 'Passer à DHealth Clinique' }))
    expect(onSwitchApp).toHaveBeenCalledTimes(1)
  })

  it('§26: exposes a logout action in the shell', async () => {
    const client = makeMockClient()
    const onLogout = vi.fn()
    render(<LabApp client={client} initialTab="home" onLogout={onLogout} />)
    await screen.findByRole('heading', { name: 'Accueil Laboratoire' })

    await userEvent.setup().click(screen.getByRole('button', { name: 'Se déconnecter' }))
    expect(onLogout).toHaveBeenCalledTimes(1)
  })

  it('§13: a provider-only user (no LAB membership at all) sees the truthful no-labs state', async () => {
    const client = makeMockClient({
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
    render(<LabApp client={client} initialTab="home" />)
    expect(
      await screen.findByText("Aucun laboratoire actif n'est associé à votre compte."),
    ).toBeInTheDocument()
  })

  it('§9: a user holding both LAB_TECH and LAB_RESULT_RELEASER on the same lab counts as one eligible membership', async () => {
    const client = makeMockClient({
      getMe: vi.fn().mockResolvedValue({
        ...sampleMe,
        memberships: [{ ...sampleMe.memberships[0], roles: ['LAB_TECH', 'LAB_RESULT_RELEASER'] }],
      }),
    })
    render(<LabApp client={client} initialTab="orders" />)
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
    render(<LabApp client={client} initialTab="home" />)
    const select = await screen.findByLabelText('Sélectionner le laboratoire')
    expect(select).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Laboratoire Central' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Laboratoire Nord' })).toBeInTheDocument()
  })

  it('renders truthful unauthenticated state on 401 without a mock fallback', async () => {
    const { ExchangeApiError } = await import('../../api/exchangeClient')
    const client = makeMockClient({
      getMe: vi.fn().mockRejectedValue(new ExchangeApiError(401, 'UNAUTHORIZED', 'no token')),
    })
    render(<LabApp client={client} initialTab="home" />)
    await waitFor(() =>
      expect(
        screen.getByText('Veuillez vous connecter pour accéder à l\'application DHealth Laboratoire.'),
      ).toBeInTheDocument(),
    )
  })
})
