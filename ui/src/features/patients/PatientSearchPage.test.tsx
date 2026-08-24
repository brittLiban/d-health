import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { PatientSearchPage } from './PatientSearchPage'
import { searchPatients, discoverIdentifierSource } from '../../api/client'
import { ApiError, type OpenMrsPatient } from '../../api/types'

vi.mock('../../api/client', () => ({
  searchPatients: vi.fn(),
  discoverIdentifierSource: vi.fn(),
  generateIdentifier: vi.fn(),
  createPatient: vi.fn(),
  findPatientByIdentifier: vi.fn(),
}))

const mockedSearch = vi.mocked(searchPatients)

const patient = (overrides: Partial<OpenMrsPatient> = {}): OpenMrsPatient => ({
  uuid: 'uuid-1',
  display: 'Verification Test',
  identifiers: [{ identifier: '10001MJ' }],
  person: { gender: 'F', birthdate: '1998-01-01', age: 27 },
  ...overrides,
})

async function runSearch(term = 'Test') {
  const user = userEvent.setup()
  await user.click(screen.getByLabelText('Rechercher un patient'))
  await user.type(screen.getByLabelText('Rechercher un patient'), term)
  await user.click(screen.getByRole('button', { name: 'Rechercher' }))
  return user
}

describe('PatientSearchPage', () => {
  beforeEach(() => {
    mockedSearch.mockReset()
  })

  it('renders the search field with a visible, associated label', () => {
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    expect(screen.getByLabelText('Rechercher un patient')).toBeInTheDocument()
  })

  it('shows a loading state while the search is in flight', async () => {
    let resolveSearch: (value: OpenMrsPatient[]) => void = () => {}
    mockedSearch.mockReturnValue(new Promise((resolve) => (resolveSearch = resolve)))
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    await runSearch()
    expect(screen.getByRole('status')).toBeInTheDocument()
    resolveSearch([])
  })

  it('renders one patient card on a successful single-result search', async () => {
    mockedSearch.mockResolvedValue([patient()])
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    await runSearch()
    expect(await screen.findByText('Verification Test')).toBeInTheDocument()
    expect(screen.getByText('10001MJ')).toBeInTheDocument()
  })

  it('renders multiple patient cards when the search returns more than one match', async () => {
    mockedSearch.mockResolvedValue([
      patient({ uuid: 'a', display: 'Patient A' }),
      patient({ uuid: 'b', display: 'Patient B' }),
    ])
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    await runSearch()
    expect(await screen.findByText('Patient A')).toBeInTheDocument()
    expect(screen.getByText('Patient B')).toBeInTheDocument()
  })

  it('renders "Aucun patient trouvé" for an empty result set, not an error', async () => {
    mockedSearch.mockResolvedValue([])
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    await runSearch('nobody')
    expect(await screen.findByText('Aucun patient trouvé')).toBeInTheDocument()
  })

  it('opens the registration form from the zero-results state, prefilled from the search terms', async () => {
    mockedSearch.mockResolvedValue([])
    vi.mocked(discoverIdentifierSource).mockReturnValue(new Promise(() => {}))
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    const user = await runSearch('Aicha Guelleh')
    await user.click(await screen.findByRole('button', { name: 'Nouveau patient' }))
    expect(screen.getByRole('heading', { name: 'Nouveau patient' })).toBeInTheDocument()
    expect(screen.getByLabelText('Prénom')).toHaveValue('Aicha')
    expect(screen.getByLabelText('Nom')).toHaveValue('Guelleh')
  })

  it('a new search hides an open registration form', async () => {
    mockedSearch.mockResolvedValueOnce([]).mockResolvedValueOnce([patient()])
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    const user = await runSearch('nobody')
    await user.click(await screen.findByRole('button', { name: 'Nouveau patient' }))
    expect(screen.getByRole('heading', { name: 'Nouveau patient' })).toBeInTheDocument()

    await user.clear(screen.getByLabelText('Rechercher un patient'))
    await user.type(screen.getByLabelText('Rechercher un patient'), 'Test')
    await user.click(screen.getByRole('button', { name: 'Rechercher' }))

    expect(await screen.findByText('Verification Test')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Nouveau patient' })).not.toBeInTheDocument()
  })

  it('renders a connection banner, not a generic error, for NETWORK_ERROR', async () => {
    mockedSearch.mockRejectedValue(new ApiError('NETWORK_ERROR', 'Connexion au serveur impossible.'))
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    await runSearch()
    expect(await screen.findByText(/Connexion perdue/)).toBeInTheDocument()
  })

  it('renders "Accès refusé" for an unauthorized search', async () => {
    mockedSearch.mockRejectedValue(new ApiError('NOT_AUTHORIZED', 'no'))
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    await runSearch()
    expect(await screen.findByRole('alert')).toHaveTextContent('Accès refusé')
  })

  it('calls onOpenPatient with the selected patient', async () => {
    mockedSearch.mockResolvedValue([patient()])
    const onOpenPatient = vi.fn()
    render(<PatientSearchPage onOpenPatient={onOpenPatient} />)
    const user = await runSearch()
    await user.click(await screen.findByRole('button', { name: 'Ouvrir le dossier' }))
    expect(onOpenPatient).toHaveBeenCalledWith(patient())
  })

  it('supports search by keyboard alone, without a mouse', async () => {
    mockedSearch.mockResolvedValue([patient()])
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    const user = userEvent.setup()
    await user.tab()
    expect(screen.getByLabelText('Rechercher un patient')).toHaveFocus()
    await user.keyboard('Test{Enter}')
    await waitFor(() => expect(mockedSearch).toHaveBeenCalledWith('Test'))
  })

  it('never persists patient data to localStorage, sessionStorage, or IndexedDB', async () => {
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
    // jsdom does not implement IndexedDB by default; only assert on it if the
    // environment actually provides one, but always assert on Storage, which
    // backs both localStorage and sessionStorage in jsdom.
    const indexedDbOpenSpy = window.indexedDB ? vi.spyOn(window.indexedDB, 'open') : undefined

    mockedSearch.mockResolvedValue([patient()])
    render(<PatientSearchPage onOpenPatient={vi.fn()} />)
    const user = await runSearch()
    await user.click(await screen.findByRole('button', { name: 'Ouvrir le dossier' }))

    expect(setItemSpy).not.toHaveBeenCalled()
    if (indexedDbOpenSpy) {
      expect(indexedDbOpenSpy).not.toHaveBeenCalled()
    }
  })
})
