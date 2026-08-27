import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import App from './App'
import { getMaternityBoardVisits, searchPatients } from './api/client'
import { DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, MATERNITE_LOCATION_UUID } from './api/admissionConstants'

vi.mock('./api/client', () => ({
  searchPatients: vi.fn(),
  getMaternityBoardVisits: vi.fn(),
}))

const mockedSearch = vi.mocked(searchPatients)
const mockedBoard = vi.mocked(getMaternityBoardVisits)

describe('App', () => {
  beforeEach(() => {
    mockedSearch.mockReset()
    mockedBoard.mockReset()
    mockedBoard.mockResolvedValue({ visits: [], truncated: false })
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
    await user.click(screen.getByRole('button', { name: /Laboratoire/ }))
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
})
