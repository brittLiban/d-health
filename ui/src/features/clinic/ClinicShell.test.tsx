import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ClinicProvider } from './ClinicContext'
import { ClinicShell } from './ClinicShell'
import type { ExchangeClient } from '../../api/exchangeClient'

describe('ClinicShell', () => {
  const mockClient = {
    getMe: vi.fn().mockResolvedValue({
      identity_id: 'id-1',
      issuer: 'issuer',
      subject: 'sub',
      display_name: 'Dr. Test',
      email: 'test@clinic.dj',
      platform_admin: false,
      memberships: [
        {
          organization_id: 'org-1',
          organization_name: 'Clinique de la Paix',
          organization_type: 'PROVIDER',
          organization_status: 'ACTIVE',
          membership_status: 'ACTIVE',
          roles: ['CLINICIAN'],
        },
      ],
    }),
    listOrders: vi.fn().mockResolvedValue([]),
    getOrder: vi.fn().mockResolvedValue({}),
    getOrderResults: vi.fn().mockResolvedValue({}),
    getReleaseVersions: vi.fn().mockResolvedValue({}),
    bindPatientReference: vi.fn().mockResolvedValue({}),
  } as unknown as ExchangeClient

  it('renders French-first navigation items and marks active tab with aria-current="page"', async () => {
    const onSelectTab = vi.fn()
    render(
      <ClinicProvider client={mockClient}>
        <ClinicShell activeTab="home" onSelectTab={onSelectTab}>
          <div>Page content</div>
        </ClinicShell>
      </ClinicProvider>,
    )

    expect(screen.getByRole('navigation', { name: 'Navigation DHealth Clinique' })).toBeInTheDocument()

    const homeBtn = screen.getByRole('button', { name: 'Accueil' })
    const patientsBtn = screen.getByRole('button', { name: 'Patients' })
    const ordersBtn = screen.getByRole('button', { name: 'Ordres de laboratoire' })
    const resultsBtn = screen.getByRole('button', { name: 'Résultats' })

    expect(homeBtn).toHaveAttribute('aria-current', 'page')
    expect(patientsBtn).not.toHaveAttribute('aria-current')
    expect(ordersBtn).not.toHaveAttribute('aria-current')
    expect(resultsBtn).not.toHaveAttribute('aria-current')

    const user = userEvent.setup()
    await user.click(ordersBtn)
    expect(onSelectTab).toHaveBeenCalledWith('orders')
  })

  it('displays the active clinic name in the header badge', async () => {
    render(
      <ClinicProvider client={mockClient}>
        <ClinicShell activeTab="home" onSelectTab={vi.fn()}>
          <div>Page content</div>
        </ClinicShell>
      </ClinicProvider>,
    )

    expect(await screen.findByText('Clinique de la Paix')).toBeInTheDocument()
    expect(screen.getByText('DHEALTH CLINIQUE')).toBeInTheDocument()
  })

  it('calls onSwitchApp and onLogout callbacks', async () => {
    const onSwitchApp = vi.fn()
    const onLogout = vi.fn()
    const user = userEvent.setup()

    render(
      <ClinicProvider client={mockClient}>
        <ClinicShell
          activeTab="home"
          onSelectTab={vi.fn()}
          onSwitchApp={onSwitchApp}
          onLogout={onLogout}
        >
          <div>Page content</div>
        </ClinicShell>
      </ClinicProvider>,
    )

    await user.click(screen.getByRole('button', { name: 'Passer au connecteur Maternité' }))
    expect(onSwitchApp).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Se déconnecter' }))
    expect(onLogout).toHaveBeenCalledTimes(1)
  })
})
