import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { axe } from 'jest-axe'
import App from './App'
import { getMaternityBoardVisits, searchPatients } from './api/client'
import { DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, MATERNITE_LOCATION_UUID } from './api/admissionConstants'

vi.mock('./api/client', () => ({
  searchPatients: vi.fn(),
  getMaternityBoardVisits: vi.fn(),
}))

const mockedSearch = vi.mocked(searchPatients)
const mockedBoard = vi.mocked(getMaternityBoardVisits)

/**
 * These five audit the reference connector's own screens. They name the surface
 * explicitly (DH-MVP-POLISH-001): the bare `/` default is now the staff
 * application, so `render(<App />)` audits the wrong tree — four of these failed
 * outright on it and the fifth passed *vacuously*, running axe over the staff
 * auth gate while claiming to cover the patient search screen.
 *
 * The `?app` reset matters for the same reason it does in `App.test.tsx`: a query
 * string left behind by an earlier test would silently decide the surface here.
 */
describe('accessibility', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/')
    mockedSearch.mockReset()
    mockedBoard.mockReset()
    mockedBoard.mockResolvedValue({ visits: [], truncated: false })
  })

  it('the empty search screen has no detectable axe violations', async () => {
    const { container } = render(<App initialApp="maternity" />)
    expect(await axe(container)).toHaveNoViolations()
  })

  it('a populated search-results screen has no detectable axe violations', async () => {
    mockedSearch.mockResolvedValue([
      { uuid: 'u1', display: 'Verification Test', identifiers: [{ identifier: '10001MJ' }] },
    ])
    const user = userEvent.setup()
    const { container } = render(<App initialApp="maternity" />)
    await user.type(screen.getByLabelText('Rechercher un patient'), 'Test')
    await user.click(screen.getByRole('button', { name: 'Rechercher' }))
    await screen.findByText('Verification Test')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('the patient workspace screen has no detectable axe violations', async () => {
    mockedSearch.mockResolvedValue([{ uuid: 'u1', display: 'Verification Test' }])
    const user = userEvent.setup()
    const { container } = render(<App initialApp="maternity" />)
    await user.type(screen.getByLabelText('Rechercher un patient'), 'Test')
    await user.click(screen.getByRole('button', { name: 'Rechercher' }))
    await user.click(await screen.findByRole('button', { name: 'Ouvrir le dossier' }))
    expect(await axe(container)).toHaveNoViolations()
  })

  it('the empty maternity board has no detectable axe violations', async () => {
    const user = userEvent.setup()
    const { container } = render(<App initialApp="maternity" />)
    await user.click(screen.getByRole('button', { name: /Maternité/ }))
    await screen.findByText('Aucune patiente actuellement admise en maternité.')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('a populated maternity board has no detectable axe violations', async () => {
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
    const { container } = render(<App initialApp="maternity" />)
    await user.click(screen.getByRole('button', { name: /Maternité/ }))
    await screen.findByText('Amina Warsame')
    expect(await axe(container)).toHaveNoViolations()
  })
})
