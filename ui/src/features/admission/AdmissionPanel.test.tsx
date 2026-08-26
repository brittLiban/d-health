import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdmissionPanel } from './AdmissionPanel'
import * as client from '../../api/client'
import { ApiError } from '../../api/types'
import {
  ADMISSION_ENCOUNTER_TYPE_UUID,
  DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
  MATERNITE_LOCATION_UUID,
} from '../../api/admissionConstants'
import type { ActiveVisit } from '../../api/types'

vi.mock('../../api/client', () => ({
  verifyAdmissionMetadata: vi.fn(),
  getSession: vi.fn(),
  getActiveVisits: vi.fn(),
  createMaternityVisit: vi.fn(),
  createAdmissionEncounter: vi.fn(),
}))

const PATIENT_UUID = 'patient-uuid'

const CLINICIAN_READY = {
  authenticated: true,
  user: { uuid: 'clinician-uuid', roles: [{ name: 'Daher Clinician Admission' }] },
  currentProvider: { uuid: 'provider-uuid' },
}

const CLINICIAN_NO_PROVIDER = {
  authenticated: true,
  user: { uuid: 'clinician-uuid', roles: [{ name: 'Daher Clinician Admission' }] },
  currentProvider: null,
}

const RECEPTION = {
  authenticated: true,
  user: { uuid: 'reception-uuid', roles: [{ name: 'Daher Reception' }] },
  currentProvider: null,
}

function maternityVisit(overrides: Partial<ActiveVisit> = {}): ActiveVisit {
  return {
    uuid: 'visit-1',
    visitType: { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, display: 'Daher Maternity Admission' },
    location: { uuid: MATERNITE_LOCATION_UUID, display: 'Maternité' },
    stopDatetime: null,
    encounters: [],
    ...overrides,
  }
}

function withAdmissionEncounter(visit: ActiveVisit): ActiveVisit {
  return {
    ...visit,
    encounters: [{ uuid: 'encounter-1', encounterType: { uuid: ADMISSION_ENCOUNTER_TYPE_UUID }, voided: false }],
  }
}

function mocked() {
  return {
    verifyMetadata: vi.mocked(client.verifyAdmissionMetadata),
    getSession: vi.mocked(client.getSession),
    getActiveVisits: vi.mocked(client.getActiveVisits),
    createVisit: vi.mocked(client.createMaternityVisit),
    createEncounter: vi.mocked(client.createAdmissionEncounter),
  }
}

describe('AdmissionPanel', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  it('shows a loading state first and offers no action while loading', () => {
    const m = mocked()
    m.verifyMetadata.mockReturnValue(new Promise(() => {})) // never resolves
    render(<AdmissionPanel patientUuid={PATIENT_UUID} />)
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /admettre/i })).not.toBeInTheDocument()
  })

  it('an eligible clinician (role + currentProvider) sees Admettre when NOT_ADMITTED', async () => {
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(CLINICIAN_READY)
    m.getActiveVisits.mockResolvedValue([])
    render(<AdmissionPanel patientUuid={PATIENT_UUID} />)

    expect(await screen.findByText('Non admise')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Admettre' })).toBeInTheDocument()
  })

  it('Reception does not see an admission mutation action, and sees a neutral unavailable state', async () => {
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(RECEPTION)
    m.getActiveVisits.mockRejectedValue(new ApiError('NOT_AUTHORIZED', 'denied'))
    render(<AdmissionPanel patientUuid={PATIENT_UUID} />)

    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /admettre/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reprendre/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/500|stack|exception|privilege/i)).not.toBeInTheDocument()
  })

  it('missing currentProvider fails closed: no Admettre button even though the admission role is held', async () => {
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(CLINICIAN_NO_PROVIDER)
    m.getActiveVisits.mockResolvedValue([])
    render(<AdmissionPanel patientUuid={PATIENT_UUID} />)

    expect(await screen.findByText('Non admise')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /admettre/i })).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/pas prêt/i)
  })

  it('COMPLETE renders Admise with no mutation action', async () => {
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(CLINICIAN_READY)
    m.getActiveVisits.mockResolvedValue([withAdmissionEncounter(maternityVisit())])
    render(<AdmissionPanel patientUuid={PATIENT_UUID} />)

    expect(await screen.findByText('Admise')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /admettre/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reprendre/i })).not.toBeInTheDocument()
  })

  it('PARTIAL_VISIT renders Admission incomplète with a Reprendre l’admission action', async () => {
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(CLINICIAN_READY)
    m.getActiveVisits.mockResolvedValue([maternityVisit()])
    render(<AdmissionPanel patientUuid={PATIENT_UUID} />)

    expect(await screen.findByText('Admission incomplète')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Reprendre l'admission" })).toBeInTheDocument()
  })

  it('the partial-recovery action creates only the missing Encounter, never a second Visit', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(CLINICIAN_READY)
    const partial = maternityVisit()
    m.getActiveVisits.mockResolvedValueOnce([partial]).mockResolvedValueOnce([partial]).mockResolvedValueOnce([
      withAdmissionEncounter(partial),
    ])
    m.createEncounter.mockResolvedValue({ uuid: 'encounter-1' })
    render(<AdmissionPanel patientUuid={PATIENT_UUID} />)

    await user.click(await screen.findByRole('button', { name: "Reprendre l'admission" }))

    await waitFor(() => expect(screen.getByText('Admise')).toBeInTheDocument())
    expect(m.createVisit).not.toHaveBeenCalled()
    expect(m.createEncounter).toHaveBeenCalledTimes(1)
  })

  it('AMBIGUOUS offers only Vérifier à nouveau, no Admettre or Reprendre', async () => {
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(CLINICIAN_READY)
    m.getActiveVisits.mockResolvedValue([maternityVisit({ uuid: 'v1' }), maternityVisit({ uuid: 'v2' })])
    render(<AdmissionPanel patientUuid={PATIENT_UUID} />)

    expect(await screen.findByRole('button', { name: 'Vérifier à nouveau' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /admettre/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reprendre/i })).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/impossible de confirmer/i)
  })

  it('surfaces a mutation error in French, with focus moved to it, after a definite rejection', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(CLINICIAN_READY)
    m.getActiveVisits.mockResolvedValue([])
    m.createVisit.mockRejectedValue(new ApiError('VALIDATION_ERROR', 'bad'))
    render(<AdmissionPanel patientUuid={PATIENT_UUID} />)

    await user.click(await screen.findByRole('button', { name: 'Admettre' }))

    const banner = await screen.findByText(/informations fournies/i)
    expect(banner).toBeInTheDocument()
    await waitFor(() => expect(banner.closest('[tabindex]')).toHaveFocus())
  })

  it('never renders a Visit/Encounter/Location UUID anywhere in the panel', async () => {
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(CLINICIAN_READY)
    m.getActiveVisits.mockResolvedValue([withAdmissionEncounter(maternityVisit())])
    const { container } = render(<AdmissionPanel patientUuid={PATIENT_UUID} />)

    await screen.findByText('Admise')
    const text = container.textContent ?? ''
    expect(text).not.toContain(DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID)
    expect(text).not.toContain(ADMISSION_ENCOUNTER_TYPE_UUID)
    expect(text).not.toContain(MATERNITE_LOCATION_UUID)
    expect(text).not.toMatch(/visit|encounter|rest\/v1|http/i)
  })

  it('never renders a raw backend stack trace or privilege exception text', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue(CLINICIAN_READY)
    m.getActiveVisits.mockResolvedValue([])
    m.createVisit.mockRejectedValue(
      new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.'),
    )
    const { container } = render(<AdmissionPanel patientUuid={PATIENT_UUID} />)

    await user.click(await screen.findByRole('button', { name: 'Admettre' }))
    await waitFor(() => expect(screen.getByText(/erreur du serveur/i)).toBeInTheDocument())

    const text = container.textContent ?? ''
    expect(text).not.toContain('org.openmrs')
    expect(text).not.toContain('AuthorizationAdvice')
  })
})
