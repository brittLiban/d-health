import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MaternityBoardPage } from './MaternityBoardPage'
import { getMaternityBoardVisits } from '../../api/client'
import { ApiError } from '../../api/types'
import { DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, MATERNITE_LOCATION_UUID } from '../../api/admissionConstants'
import type { MaternityBoardVisit } from '../../api/types'

vi.mock('../../api/client', () => ({
  getMaternityBoardVisits: vi.fn(),
}))

const mockedGet = vi.mocked(getMaternityBoardVisits)

function visit(overrides: Partial<MaternityBoardVisit> = {}): MaternityBoardVisit {
  return {
    uuid: 'visit-1',
    startDatetime: '2026-08-20T08:00:00.000+0000',
    stopDatetime: null,
    visitType: { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, display: 'Daher Maternity Admission' },
    location: { uuid: MATERNITE_LOCATION_UUID, display: 'Maternité' },
    patient: { uuid: 'p1', display: 'Amina Warsame', identifiers: [{ identifier: '10001AA' }] },
    ...overrides,
  }
}

describe('MaternityBoardPage', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  it('shows a loading state while the board is in flight', async () => {
    mockedGet.mockReturnValue(new Promise(() => {}))
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('renders the required empty-state copy — never implying no patients exist in the facility', async () => {
    mockedGet.mockResolvedValue({ visits: [], truncated: false })
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    expect(await screen.findByText('Aucune patiente actuellement admise en maternité.')).toBeInTheDocument()
  })

  it('renders a patient card for a canonical active maternity admission, including its admission date', async () => {
    mockedGet.mockResolvedValue({ visits: [visit()], truncated: false })
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    expect(await screen.findByText('Amina Warsame')).toBeInTheDocument()
    expect(screen.getByText('10001AA')).toBeInTheDocument()
    expect(screen.getByText(/^Admise le /)).toBeInTheDocument()
  })

  it('renders one card per patient when several are admitted', async () => {
    mockedGet.mockResolvedValue({
      visits: [
        visit({ uuid: 'v1', patient: { uuid: 'p1', display: 'Patiente A' } }),
        visit({ uuid: 'v2', patient: { uuid: 'p2', display: 'Patiente B' } }),
      ],
      truncated: false,
    })
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    expect(await screen.findByText('Patiente A')).toBeInTheDocument()
    expect(screen.getByText('Patiente B')).toBeInTheDocument()
  })

  it('never renders a UUID, visit id, or OpenMRS/FHIR term anywhere on the board', async () => {
    mockedGet.mockResolvedValue({ visits: [visit()], truncated: false })
    const { container } = render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    await screen.findByText('Amina Warsame')
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i)
    expect(text).not.toMatch(/FHIR|OpenMRS|Visit type|Encounter/i)
  })

  it('renders the neutral unavailable state on a Reception-shaped 403, not an error banner', async () => {
    mockedGet.mockRejectedValue(new ApiError('NOT_AUTHORIZED', 'no'))
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    expect(await screen.findByText("Ce tableau n'est pas disponible avec ce compte")).toBeInTheDocument()
  })

  it('renders a connection banner, not a generic error, for NETWORK_ERROR', async () => {
    mockedGet.mockRejectedValue(new ApiError('NETWORK_ERROR', 'no'))
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    expect(await screen.findByText(/Connexion perdue/)).toBeInTheDocument()
  })

  it('renders a generic error state for SERVER_ERROR, with a retry action', async () => {
    mockedGet.mockRejectedValue(new ApiError('SERVER_ERROR', 'no'))
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Réessayer' })).toBeInTheDocument()
  })

  it('flags an ambiguous (duplicate-active-visit) entry inline, without hiding the patient or crashing the board', async () => {
    const patient = { uuid: 'p-dup', display: 'Patiente Dupliquée' }
    mockedGet.mockResolvedValue({
      visits: [visit({ uuid: 'v1', patient }), visit({ uuid: 'v2', patient, startDatetime: '2026-08-22T00:00:00.000+0000' })],
      truncated: false,
    })
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    expect(await screen.findByText('Patiente Dupliquée')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('vérifier')
  })

  it('clicking "Ouvrir le dossier" opens the same patient identity used elsewhere in the app', async () => {
    mockedGet.mockResolvedValue({ visits: [visit()], truncated: false })
    const onOpenPatient = vi.fn()
    render(<MaternityBoardPage onOpenPatient={onOpenPatient} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Ouvrir le dossier' }))
    expect(onOpenPatient).toHaveBeenCalledWith(visit().patient)
  })

  it('the "Actualiser" button refetches the board', async () => {
    mockedGet.mockResolvedValue({ visits: [], truncated: false })
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    await screen.findByText('Aucune patiente actuellement admise en maternité.')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Actualiser' }))
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2))
  })

  it('surfaces truncation honestly rather than silently dropping admissions past the page size', async () => {
    mockedGet.mockResolvedValue({ visits: [visit()], truncated: true })
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    await screen.findByText('Amina Warsame')
    expect(screen.getByText(/au-delà de celles affichées/)).toBeInTheDocument()
  })

  it('never persists patient data to localStorage, sessionStorage, or IndexedDB', async () => {
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
    const indexedDbOpenSpy = window.indexedDB ? vi.spyOn(window.indexedDB, 'open') : undefined
    mockedGet.mockResolvedValue({ visits: [visit()], truncated: false })
    const user = userEvent.setup()
    render(<MaternityBoardPage onOpenPatient={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Ouvrir le dossier' }))

    expect(setItemSpy).not.toHaveBeenCalled()
    if (indexedDbOpenSpy) {
      expect(indexedDbOpenSpy).not.toHaveBeenCalled()
    }
  })
})
