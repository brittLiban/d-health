import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import App from './App'
import { getMaternityBoardVisits, searchPatients } from './api/client'
import { DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, MATERNITE_LOCATION_UUID } from './api/admissionConstants'

vi.mock('./api/client', () => ({
  searchPatients: vi.fn(),
  getMaternityBoardVisits: vi.fn(),
}))

// Native Clinic now sits behind the shared ExchangeAuthProvider (DH-CLINIC-004A).
// These App-level tests exercise app-switching and mounting, not OIDC itself
// (that belongs to ExchangeAuthProvider.test.tsx) — so keycloak-js is faked
// here as an already-authenticated session that resolves instantly. The
// constructor itself is a spy so tests can assert whether a Keycloak session
// was ever created at all, and inspect the latest instance's own logout().
const { keycloakConstructorMock, getLatestKeycloakInstance } = vi.hoisted(() => {
  const instances: Array<{ logout: ReturnType<typeof vi.fn> }> = []
  const keycloakConstructorMock = vi.fn().mockImplementation(function FakeKeycloak() {
    const instance = {
      authenticated: true,
      token: 'fake-access-token',
      onAuthRefreshError: undefined as (() => void) | undefined,
      onAuthLogout: undefined as (() => void) | undefined,
      init: vi.fn().mockResolvedValue(true),
      login: vi.fn().mockResolvedValue(undefined),
      logout: vi.fn().mockResolvedValue(undefined),
      updateToken: vi.fn().mockResolvedValue(false),
    }
    instances.push(instance)
    return instance
  })
  return { keycloakConstructorMock, getLatestKeycloakInstance: () => instances.at(-1) }
})

vi.mock('keycloak-js', () => ({ default: keycloakConstructorMock }))

const mockedSearch = vi.mocked(searchPatients)
const mockedBoard = vi.mocked(getMaternityBoardVisits)

describe('App', () => {
  beforeEach(() => {
    mockedSearch.mockReset()
    mockedBoard.mockReset()
    mockedBoard.mockResolvedValue({ visits: [], truncated: false })
    vi.stubEnv('VITE_EXCHANGE_KEYCLOAK_URL', 'https://id.example.org')
    keycloakConstructorMock.mockClear()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (typeof url === 'string' && url.includes('/exchange/v1/me')) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                identity_id: 'id-1',
                issuer: 'issuer',
                subject: 'sub',
                display_name: 'Dr. Test',
                email: 'dr@clinic.dj',
                platform_admin: false,
                memberships: [
                  {
                    organization_id: 'org-1',
                    organization_name: 'Clinique Espoir',
                    organization_type: 'PROVIDER',
                    organization_status: 'ACTIVE',
                    membership_status: 'ACTIVE',
                    roles: ['CLINICIAN'],
                  },
                ],
              }),
              { status: 200 },
            ),
          )
        }
        if (typeof url === 'string' && url.includes('/orders')) {
          return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))
        }
        return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }))
      }),
    )
  })

  it('selecting a patient from search opens that patient\'s workspace', async () => {
    mockedSearch.mockResolvedValue([
      { uuid: 'u1', display: 'Verification Test', identifiers: [{ identifier: '10001MJ' }] },
    ])
    const user = userEvent.setup()
    render(<App />)

    await user.type(screen.getByLabelText('Rechercher un patient'), 'Test')
    await user.click(screen.getByRole('button', { name: 'Rechercher' }))
    await user.click(await screen.findByRole('button', { name: 'Ouvrir le dossier' }))

    expect(screen.getByRole('heading', { name: 'Verification Test' })).toBeInTheDocument()
    expect(screen.getAllByText('10001MJ').length).toBeGreaterThan(0)
  })

  it('returns to search from the workspace without losing the "Patients" context', async () => {
    mockedSearch.mockResolvedValue([{ uuid: 'u1', display: 'Verification Test' }])
    const user = userEvent.setup()
    render(<App />)

    await user.type(screen.getByLabelText('Rechercher un patient'), 'Test')
    await user.click(screen.getByRole('button', { name: 'Rechercher' }))
    await user.click(await screen.findByRole('button', { name: 'Ouvrir le dossier' }))
    await user.click(screen.getByRole('button', { name: /Retour à la recherche/ }))

    expect(screen.getByLabelText('Rechercher un patient')).toBeInTheDocument()
  })

  it('shows an honest "not yet available" screen for an unbuilt nav item, not a broken one', async () => {
    const user = userEvent.setup()
    render(<App />)
    // Scoped to the sidebar nav landmark specifically -- DH-LAB-001 added a second, unrelated
    // "DHealth Laboratoire (Natif)" header button that also matches a loose /Laboratoire/
    // name search; this placeholder sidebar item (still unbuilt in the reference-connector
    // Maternity nav, distinct from the new native app) is the one this test means to click.
    const nav = screen.getByRole('navigation', { name: 'Navigation principale' })
    await user.click(within(nav).getByRole('button', { name: /Laboratoire/ }))
    expect(screen.getByText("Cet écran n'est pas encore disponible")).toBeInTheDocument()
  })

  it('Maternité opens the real board, not the "Bientôt" placeholder', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: /Maternité/ }))
    expect(await screen.findByText('Patientes actuellement admises')).toBeInTheDocument()
    expect(screen.queryByText("Cet écran n'est pas encore disponible")).not.toBeInTheDocument()
  })

  it('opening a patient from the maternity board reaches the same PatientWorkspace identity path as search', async () => {
    mockedBoard.mockResolvedValue({
      visits: [
        {
          uuid: 'visit-1',
          startDatetime: '2026-08-20T08:00:00.000+0000',
          stopDatetime: null,
          visitType: { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, display: 'Daher Maternity Admission' },
          location: { uuid: MATERNITE_LOCATION_UUID, display: 'Maternité' },
          patient: { uuid: 'p1', display: 'Amina Warsame', identifiers: [{ identifier: '10001AA' }] },
        },
      ],
      truncated: false,
    })
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: /Maternité/ }))
    await user.click(await screen.findByRole('button', { name: 'Ouvrir le dossier' }))

    expect(screen.getByRole('heading', { name: 'Amina Warsame' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retour à Maternité/ })).toBeInTheDocument()
  })

  it('returning from a patient opened via Maternité goes back to the board and refetches it (§9)', async () => {
    mockedBoard.mockResolvedValue({
      visits: [
        {
          uuid: 'visit-1',
          startDatetime: '2026-08-20T08:00:00.000+0000',
          stopDatetime: null,
          visitType: { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, display: 'Daher Maternity Admission' },
          location: { uuid: MATERNITE_LOCATION_UUID, display: 'Maternité' },
          patient: { uuid: 'p1', display: 'Amina Warsame' },
        },
      ],
      truncated: false,
    })
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: /Maternité/ }))
    await waitFor(() => expect(mockedBoard).toHaveBeenCalledTimes(1))
    await user.click(await screen.findByRole('button', { name: 'Ouvrir le dossier' }))
    await user.click(screen.getByRole('button', { name: /Retour à Maternité/ }))

    expect(await screen.findByText('Patientes actuellement admises')).toBeInTheDocument()
    await waitFor(() => expect(mockedBoard).toHaveBeenCalledTimes(2))
  })

  it('can switch to native DHealth Clinic and back to Maternity connector', async () => {
    const user = userEvent.setup()
    render(<App />)

    // Initial view is OpenMRS patient search
    expect(screen.getByLabelText('Rechercher un patient')).toBeInTheDocument()

    // Switch to native Clinic
    await user.click(screen.getByRole('button', { name: 'Basculer vers DHealth Clinique' }))
    expect(await screen.findByText('DHEALTH CLINIQUE')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Navigation DHealth Clinique' })).toBeInTheDocument()

    // Switch back to Maternity connector
    await user.click(screen.getByRole('button', { name: 'Passer au connecteur Maternité' }))
    expect(await screen.findByLabelText('Rechercher un patient')).toBeInTheDocument()
  })

  it('mounts native Clinic directly when initialApp="clinic"', async () => {
    render(<App initialApp="clinic" />)
    expect(await screen.findByText('DHEALTH CLINIQUE')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Navigation DHealth Clinique' })).toBeInTheDocument()
  })

  // DH-LAB-001 §6: '?app=lab' mounts native Lab the same way '?app=clinic' mounts native Clinic.
  // The shared beforeEach's `/exchange/v1/me` fixture has a PROVIDER-only membership, so this also
  // proves §13's truthful no-labs state renders rather than a blank page -- the nav landmark below
  // is what actually proves the Lab shell mounted at all.
  it('mounts native Lab directly when initialApp="lab"', async () => {
    render(<App initialApp="lab" />)
    expect(
      await screen.findByRole('navigation', { name: 'Navigation DHealth Laboratoire' }),
    ).toBeInTheDocument()
    expect(
      await screen.findByText("Aucun laboratoire actif n'est associé à votre compte."),
    ).toBeInTheDocument()
  })

  // DH-LAB-001 §7: switching apps unmounts the ExchangeAuthProvider's in-memory token state only --
  // it must never itself call Keycloak's logout endpoint. Mirrors the identical Clinic-side test
  // above (LabApp has no Lab-side auth code of its own to diverge from Clinic's).
  it('switching from Lab to Clinic does not call Exchange logout', async () => {
    const user = userEvent.setup()
    render(<App initialApp="lab" />)
    await screen.findByRole('navigation', { name: 'Navigation DHealth Laboratoire' })
    const instance = getLatestKeycloakInstance()

    await user.click(screen.getByRole('button', { name: 'Passer à DHealth Clinique' }))
    await screen.findByText('DHEALTH CLINIQUE')

    expect(instance?.logout).not.toHaveBeenCalled()
  })

  // §28: staying on the reference-connector app must never initialize the
  // native Exchange auth layer — no Keycloak instance, no OIDC round trip.
  it('never constructs a Keycloak session while only the Maternity connector is used', async () => {
    render(<App />)
    expect(screen.getByLabelText('Rechercher un patient')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Maternité/ }))
    await screen.findByText('Patientes actuellement admises')

    expect(keycloakConstructorMock).not.toHaveBeenCalled()
  })

  // §15/§16: Clinic logout must terminate the Exchange session specifically —
  // never the connector's OpenMRS/OpenELIS sessions — and switching apps must
  // never be conflated with logging out of either.
  it('Clinic logout calls the Exchange session logout, not OpenMRS/OpenELIS', async () => {
    const user = userEvent.setup()
    render(<App initialApp="clinic" />)
    await screen.findByText('DHEALTH CLINIQUE')

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockClear()

    await user.click(screen.getByRole('button', { name: 'Se déconnecter' }))

    expect(getLatestKeycloakInstance()?.logout).toHaveBeenCalledTimes(1)
    expect(
      fetchMock.mock.calls.some(
        (call: Parameters<typeof fetch>) =>
          typeof call[0] === 'string' && call[0].includes('/openmrs/'),
      ),
    ).toBe(false)
    expect(
      fetchMock.mock.calls.some(
        (call: Parameters<typeof fetch>) =>
          typeof call[0] === 'string' && call[0].includes('/OpenELIS-Global/'),
      ),
    ).toBe(false)
  })

  it('switching from Clinic back to Maternity does not call Exchange logout', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Basculer vers DHealth Clinique' }))
    await screen.findByText('DHEALTH CLINIQUE')
    const instance = getLatestKeycloakInstance()

    await user.click(screen.getByRole('button', { name: 'Passer au connecteur Maternité' }))
    await screen.findByLabelText('Rechercher un patient')

    expect(instance?.logout).not.toHaveBeenCalled()
  })

  it('Maternity connector logout still performs its own OpenMRS/OpenELIS session teardown', async () => {
    const user = userEvent.setup()
    render(<App />)
    expect(screen.getByLabelText('Rechercher un patient')).toBeInTheDocument()

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockClear()

    await user.click(screen.getByRole('button', { name: 'Se déconnecter' }))

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (call: Parameters<typeof fetch>) =>
            typeof call[0] === 'string' && call[0].includes('/openmrs/ws/rest/v1/session'),
        ),
      ).toBe(true)
    })
  })
})

