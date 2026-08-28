import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EndAdmissionPanel } from './EndAdmissionPanel'
import * as client from '../../api/client'
import { ApiError } from '../../api/types'
import type { AdmissionReadiness } from './deriveReadiness'
import type { AdmissionReadState } from './useAdmission'

vi.mock('../../api/client', () => ({
  endAdmission: vi.fn(),
}))

const VISIT_UUID = 'visit-1'
const READY: AdmissionReadiness = { status: 'READY' }
const PROVIDER_MISSING: AdmissionReadiness = { status: 'PROVIDER_MISSING' }

function mocked() {
  return { endAdmission: vi.mocked(client.endAdmission) }
}

function stillComplete(): AdmissionReadState {
  return {
    kind: 'resolved',
    status: 'COMPLETE',
    visit: { uuid: VISIT_UUID, visitType: { uuid: 'vt' }, location: { uuid: 'loc' }, stopDatetime: null, encounters: [] },
  }
}

function noLongerActive(): AdmissionReadState {
  return { kind: 'resolved', status: 'NOT_ADMITTED' }
}

async function openAndConfirm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: "Terminer l'admission" }))
  await user.click(screen.getByRole('button', { name: 'Confirmer' }))
}

describe('EndAdmissionPanel — UI-015F §2/§12 the eight required states', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  // --- ACTIVE ---------------------------------------------------------------

  it('ACTIVE: shows the Terminer l’admission button when ready, and never calls the API on render', () => {
    const m = mocked()
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)
    expect(screen.getByRole('button', { name: "Terminer l'admission" })).toBeEnabled()
    expect(m.endAdmission).not.toHaveBeenCalled()
  })

  it('ACTIVE: a missing Provider fails closed — button disabled, French explanation shown', () => {
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={PROVIDER_MISSING} reconcile={reconcile} />)
    expect(screen.getByRole('button', { name: "Terminer l'admission" })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/pas prêt/i)
  })

  // --- CONFIRMATION -----------------------------------------------------------

  it('CONFIRMATION: clicking the button shows the French confirmation prompt; the API is still not called', async () => {
    const user = userEvent.setup()
    const m = mocked()
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    await user.click(screen.getByRole('button', { name: "Terminer l'admission" }))

    expect(screen.getByText('Terminer cette admission en maternité ?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirmer' })).toBeInTheDocument()
    expect(m.endAdmission).not.toHaveBeenCalled()
  })

  it('CONFIRMATION: Annuler returns to ACTIVE without calling the API — the mutation cannot happen without confirmation', async () => {
    const user = userEvent.setup()
    const m = mocked()
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    await user.click(screen.getByRole('button', { name: "Terminer l'admission" }))
    await user.click(screen.getByRole('button', { name: 'Annuler' }))

    expect(screen.queryByText('Terminer cette admission en maternité ?')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Terminer l'admission" })).toBeInTheDocument()
    expect(m.endAdmission).not.toHaveBeenCalled()
  })

  // --- SUBMITTING -------------------------------------------------------------

  it('SUBMITTING: the Confirmer button is disabled/relabeled while in flight, and a second click never fires a second request', async () => {
    const user = userEvent.setup()
    const m = mocked()
    let resolveCall: (value: { status: 'ENDED'; stopDatetime: string }) => void = () => {}
    m.endAdmission.mockReturnValue(
      new Promise((resolve) => {
        resolveCall = resolve
      }),
    )
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    await user.click(screen.getByRole('button', { name: "Terminer l'admission" }))
    await user.click(screen.getByRole('button', { name: 'Confirmer' }))

    expect(screen.queryByRole('button', { name: 'Confirmer' })).not.toBeInTheDocument()
    expect(screen.getByText(/fin en cours/i)).toBeInTheDocument()

    resolveCall({ status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
    await waitFor(() => expect(m.endAdmission).toHaveBeenCalledTimes(1))
  })

  // --- ENDED --------------------------------------------------------------

  it('ENDED: shows a durable French confirmation message, and Fermer reconciles exactly once', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.endAdmission.mockResolvedValue({ status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
    const reconcile = vi.fn().mockResolvedValue(noLongerActive())
    render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    await openAndConfirm(user)

    const message = await screen.findByText('Admission terminée.')
    expect(message).toBeInTheDocument()
    expect(reconcile).not.toHaveBeenCalled() // not raced away before the message is observed

    await user.click(screen.getByRole('button', { name: 'Fermer' }))
    expect(reconcile).toHaveBeenCalledTimes(1)
  })

  // --- ALREADY_ENDED --------------------------------------------------------

  it('ALREADY_ENDED: shows distinct French copy from ENDED, and never mutated (zero write)', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.endAdmission.mockResolvedValue({ status: 'ALREADY_ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
    const reconcile = vi.fn().mockResolvedValue(noLongerActive())
    render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    await openAndConfirm(user)

    expect(await screen.findByText('Cette admission était déjà terminée.')).toBeInTheDocument()
    expect(screen.queryByText('Admission terminée.')).not.toBeInTheDocument()
    expect(m.endAdmission).toHaveBeenCalledTimes(1) // the one call that reported zero write, not a second one
  })

  // --- AMBIGUOUS ------------------------------------------------------------

  it('AMBIGUOUS: an unresolvable write shows the uncertainty state, and Vérifier à nouveau never resends the mutation', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.endAdmission.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'x'))
    const reconcile = vi.fn().mockRejectedValue(new Error('down'))
    render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    await openAndConfirm(user)

    expect(await screen.findByText(/impossible de confirmer/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Vérifier à nouveau' }))

    expect(m.endAdmission).toHaveBeenCalledTimes(1) // never blind-retried
    expect(reconcile).toHaveBeenCalledTimes(2) // once from confirm(), once from verifyAgain()
  })

  it('AMBIGUOUS resolving via reconciliation to ENDED never shows a second confirmation prompt', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.endAdmission.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'x'))
    const reconcile = vi.fn().mockResolvedValue(noLongerActive())
    render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    await openAndConfirm(user)

    expect(await screen.findByText('Admission terminée.')).toBeInTheDocument()
    expect(m.endAdmission).toHaveBeenCalledTimes(1)
  })

  // --- UNAVAILABLE ------------------------------------------------------------

  it('UNAVAILABLE: a NOT_AUTHORIZED failure shows a neutral denial, never a privilege name', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.endAdmission.mockRejectedValue(new ApiError('NOT_AUTHORIZED', 'Accès refusé.'))
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const { container } = render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    await openAndConfirm(user)

    const banner = await screen.findByText(/autorisé/i)
    await waitFor(() => expect(banner.closest('[tabindex]')).toHaveFocus())
    expect(container.textContent ?? '').not.toMatch(/privilege|get providers|daemon/i)
  })

  // --- ERROR ------------------------------------------------------------

  it('ERROR: a definite failure shows a clear French error, with focus moved to it, without fabricating success', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.endAdmission.mockRejectedValue(new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.'))
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const { container } = render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    await openAndConfirm(user)

    const banner = await screen.findByText(/erreur du serveur/i)
    await waitFor(() => expect(banner.closest('[tabindex]')).toHaveFocus())
    expect(screen.queryByText('Admission terminée.')).not.toBeInTheDocument()
    expect(container.textContent ?? '').not.toContain('org.openmrs')
  })

  it('ERROR: Réessayer resubmits directly and can reach ENDED', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.endAdmission
      .mockRejectedValueOnce(new ApiError('SERVER_ERROR', 'x'))
      .mockResolvedValueOnce({ status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
    const reconcile = vi.fn().mockResolvedValue(noLongerActive())
    render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    await openAndConfirm(user)
    await screen.findByText(/erreur du serveur/i)
    await user.click(screen.getByRole('button', { name: 'Réessayer' }))

    expect(await screen.findByText('Admission terminée.')).toBeInTheDocument()
    expect(m.endAdmission).toHaveBeenCalledTimes(2)
  })

  // --- cross-cutting ------------------------------------------------------

  it('never renders a Visit UUID anywhere in the panel, across ACTIVE/ENDED', async () => {
    const user = userEvent.setup()
    const m = mocked()
    m.endAdmission.mockResolvedValue({ status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
    const reconcile = vi.fn().mockResolvedValue(noLongerActive())
    const { container } = render(<EndAdmissionPanel visitUuid={VISIT_UUID} readiness={READY} reconcile={reconcile} />)

    expect(container.textContent ?? '').not.toContain(VISIT_UUID)
    await openAndConfirm(user)
    await screen.findByText('Admission terminée.')
    expect(container.textContent ?? '').not.toContain(VISIT_UUID)
  })
})
