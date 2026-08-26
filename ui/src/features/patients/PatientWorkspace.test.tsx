import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PatientWorkspace } from './PatientWorkspace'
import * as client from '../../api/client'
import type { OpenMrsPatient } from '../../api/types'

// UI-013 wires AdmissionPanel into the Résumé tab; this only verifies the
// integration point (the panel renders with the right patient uuid), not
// AdmissionPanel's own behavior — that is AdmissionPanel.test.tsx's job.
vi.mock('../../api/client', () => ({
  verifyAdmissionMetadata: vi.fn(),
  getSession: vi.fn(),
  getActiveVisits: vi.fn(),
  createMaternityVisit: vi.fn(),
  createAdmissionEncounter: vi.fn(),
}))

const PATIENT: OpenMrsPatient = {
  uuid: 'patient-uuid',
  display: '100001W - Synthetique Test',
  identifiers: [{ identifier: '100001W' }],
  person: { gender: 'F', birthdate: '1995-06-15' },
}

describe('PatientWorkspace', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  it('renders the AdmissionPanel in the Résumé tab, scoped to the open patient', async () => {
    const m = {
      verifyMetadata: vi.mocked(client.verifyAdmissionMetadata),
      getSession: vi.mocked(client.getSession),
      getActiveVisits: vi.mocked(client.getActiveVisits),
    }
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue({ authenticated: false })
    m.getActiveVisits.mockResolvedValue([])

    render(<PatientWorkspace patient={PATIENT} onBack={() => {}} />)

    expect(await screen.findByText('Non admise')).toBeInTheDocument()
    expect(m.getActiveVisits).toHaveBeenCalledWith('patient-uuid')
  })

  it('still shows the honest "not yet available" placeholder for pregnancy context, not invented content', async () => {
    const m = {
      verifyMetadata: vi.mocked(client.verifyAdmissionMetadata),
      getSession: vi.mocked(client.getSession),
      getActiveVisits: vi.mocked(client.getActiveVisits),
    }
    m.verifyMetadata.mockResolvedValue(undefined)
    m.getSession.mockResolvedValue({ authenticated: false })
    m.getActiveVisits.mockResolvedValue([])

    render(<PatientWorkspace patient={PATIENT} onBack={() => {}} />)

    expect(await screen.findByText('Contexte de grossesse non disponible')).toBeInTheDocument()
  })
})
