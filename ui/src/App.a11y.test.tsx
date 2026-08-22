import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { axe } from 'jest-axe'
import App from './App'
import { searchPatients } from './api/client'

vi.mock('./api/client', () => ({
  searchPatients: vi.fn(),
}))

const mockedSearch = vi.mocked(searchPatients)

describe('accessibility', () => {
  beforeEach(() => {
    mockedSearch.mockReset()
  })

  it('the empty search screen has no detectable axe violations', async () => {
    const { container } = render(<App />)
    expect(await axe(container)).toHaveNoViolations()
  })

  it('a populated search-results screen has no detectable axe violations', async () => {
    mockedSearch.mockResolvedValue([
      { uuid: 'u1', display: 'Verification Test', identifiers: [{ identifier: '10001MJ' }] },
    ])
    const user = userEvent.setup()
    const { container } = render(<App />)
    await user.type(screen.getByLabelText('Rechercher un patient'), 'Test')
    await user.click(screen.getByRole('button', { name: 'Rechercher' }))
    await screen.findByText('Verification Test')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('the patient workspace screen has no detectable axe violations', async () => {
    mockedSearch.mockResolvedValue([{ uuid: 'u1', display: 'Verification Test' }])
    const user = userEvent.setup()
    const { container } = render(<App />)
    await user.type(screen.getByLabelText('Rechercher un patient'), 'Test')
    await user.click(screen.getByRole('button', { name: 'Rechercher' }))
    await user.click(await screen.findByRole('button', { name: 'Ouvrir le dossier' }))
    expect(await axe(container)).toHaveNoViolations()
  })
})
