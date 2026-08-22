import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import App from './App'
import { searchPatients } from './api/client'

vi.mock('./api/client', () => ({
  searchPatients: vi.fn(),
}))

const mockedSearch = vi.mocked(searchPatients)

describe('App', () => {
  beforeEach(() => {
    mockedSearch.mockReset()
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
})
