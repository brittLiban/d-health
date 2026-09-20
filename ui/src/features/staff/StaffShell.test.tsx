/**
 * DH-MVP-POLISH-001 — US1–US12, the unified staff shell.
 *
 * Supersedes `ClinicShell.test.tsx`, whose coverage is carried forward rather
 * than dropped: French-first navigation with `aria-current="page"` on the active
 * item (US7), the active establishment named in the masthead (US11), and the
 * logout action reaching its callback (US12) are all still asserted here. What
 * changed is what replaced them — the shell no longer announces which
 * "application" you are in, and the switch-application control is gone (US1,
 * US2).
 *
 * These mount the real `WorkspaceProvider` over a stubbed client, so the load
 * path under test is the one production uses.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError } from '../../api/exchangeClient'
import {
  labMembership,
  meWith,
  providerMembership,
  renderWithWorkspace,
  workspaceClient,
} from '../../test/renderWithWorkspace'
import { StaffWorkspace } from './StaffWorkspace'

const MASTHEAD = { name: 'Session et établissement actif' }

/** The navigation region, so a section button is never confused with a button of
 *  the same name inside the page the section rendered. */
async function nav() {
  return within(await screen.findByRole('navigation', { name: 'Navigation principale' }))
}

async function masthead() {
  return within(await screen.findByRole('banner', MASTHEAD))
}

function renderStaff(memberships = [providerMembership('prov-1', 'Clinique de la Paix')]) {
  const me = meWith(memberships)
  return renderWithWorkspace(<StaffWorkspace />, { me })
}

describe('the unified staff shell', () => {
  it('US1: names the product once and never a per-role sub-application', async () => {
    renderStaff()
    expect(await screen.findByText('DHealth')).toBeInTheDocument()

    // The old shells branded themselves by the role that opened them. A person
    // holding both kinds of membership saw two different products; they hold one
    // account and work in one place.
    expect(screen.queryByText('DHEALTH CLINIQUE')).not.toBeInTheDocument()
    expect(screen.queryByText('DHEALTH LABORATOIRE')).not.toBeInTheDocument()
  })

  it('US2: offers no control for switching application', async () => {
    renderStaff()
    await screen.findByRole('navigation', { name: 'Navigation principale' })

    for (const label of [
      /Passer à DHealth Clinique/i,
      /Passer au connecteur Maternité/i,
      /Mode Maternité/i,
      /DHealth Laboratoire \(Natif\)/i,
    ]) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument()
    }
  })

  it('US3: loads the session exactly once for the whole application', async () => {
    // The defect this ticket set out to remove: ClinicProvider and LabProvider
    // each fetched `/me`, and each wrote the eligibility rule out twice.
    const me = meWith([
      providerMembership('prov-1', 'Clinique de la Paix'),
      labMembership('lab-1', 'Laboratoire Central'),
    ])
    const client = workspaceClient(me)
    renderWithWorkspace(<StaffWorkspace />, { me, client })

    await screen.findByRole('navigation', { name: 'Navigation principale' })
    expect(client.getMe).toHaveBeenCalledTimes(1)
  })

  it('US4: a provider clinician is offered every provider section', async () => {
    renderStaff()
    const sections = await nav()

    for (const label of ["Aujourd'hui", 'Patients', 'Ordres', 'Résultats']) {
      expect(sections.getByRole('button', { name: label })).toBeInTheDocument()
    }
  })

  it('US5: a technician who cannot release results is offered no Résultats section', async () => {
    // ADR-044 keeps entry and release as separate authorities. The navigation
    // must reflect that rather than offer work the server would refuse.
    renderStaff([labMembership('lab-1', 'Laboratoire Central', ['LAB_TECH'])])
    const sections = await nav()

    expect(sections.getByRole('button', { name: 'Ordres' })).toBeInTheDocument()
    expect(sections.queryByRole('button', { name: 'Résultats' })).not.toBeInTheDocument()
  })

  it('US6: a releaser who is not a technician is offered no Ordres section', async () => {
    renderStaff([labMembership('lab-1', 'Laboratoire Central', ['LAB_RESULT_RELEASER'])])
    const sections = await nav()

    expect(sections.getByRole('button', { name: 'Résultats' })).toBeInTheDocument()
    expect(sections.queryByRole('button', { name: 'Ordres' })).not.toBeInTheDocument()
  })

  it('US7: marks the active section with aria-current="page" and no other', async () => {
    renderStaff()
    const sections = await nav()

    expect(sections.getByRole('button', { name: "Aujourd'hui" })).toHaveAttribute('aria-current', 'page')
    for (const label of ['Patients', 'Ordres', 'Résultats']) {
      expect(sections.getByRole('button', { name: label })).not.toHaveAttribute('aria-current')
    }
  })

  it('US8: selecting a section moves the active marker to it', async () => {
    renderStaff()
    const sections = await nav()
    const orders = sections.getByRole('button', { name: 'Ordres' })

    await userEvent.setup().click(orders)

    await waitFor(() => expect(orders).toHaveAttribute('aria-current', 'page'))
    expect(sections.getByRole('button', { name: "Aujourd'hui" })).not.toHaveAttribute('aria-current')
  })

  it('US9: never names a laboratory section for a provider membership', async () => {
    renderStaff()
    await screen.findByRole('navigation', { name: 'Navigation principale' })

    // A laboratory works from orders; the patient directory is a provider's
    // authority (`_require_clinician`). Offering it here would be a promise the
    // server refuses to keep.
    const band = await masthead()
    expect(band.getByText('Établissement de soins')).toBeInTheDocument()
    expect(band.queryByText("Laboratoire d'analyses")).not.toBeInTheDocument()
  })

  it('US10: a laboratory-only account is never told that no *clinic* is active', async () => {
    // The old wording was per-application, so a technician with a perfectly good
    // laboratory membership could be told they had no clinic.
    renderStaff([labMembership('lab-1', 'Laboratoire Central')])
    await screen.findByRole('navigation', { name: 'Navigation principale' })

    expect((await masthead()).getByText("Laboratoire d'analyses")).toBeInTheDocument()
    expect(screen.queryByText(/Aucune organisation clinique/i)).not.toBeInTheDocument()
  })

  it('US11: states which establishment the next request will be made against', async () => {
    renderStaff()
    // Scoped to the masthead: the page below legitimately names the same
    // establishment, and it is the masthead that has to be unambiguous.
    const band = await masthead()

    expect(band.getByText('Clinique de la Paix')).toBeInTheDocument()
    expect(band.getByText('Établissement de soins')).toBeInTheDocument()
    // And as whom, with the roles actually held there.
    expect(band.getByText('Dr. Halima Farah')).toBeInTheDocument()
    expect(band.getByText('CLINICIAN')).toBeInTheDocument()
  })

  it('US12: reaches the logout callback from the shell', async () => {
    const onLogout = vi.fn()
    const me = meWith([providerMembership('prov-1', 'Clinique de la Paix')])
    renderWithWorkspace(<StaffWorkspace onLogout={onLogout} />, { me })

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Se déconnecter' }))
    expect(onLogout).toHaveBeenCalledTimes(1)
  })
})

describe('the staff shell without a usable membership', () => {
  it('states the situation neutrally, naming neither application', async () => {
    const me = meWith([])
    renderWithWorkspace(<StaffWorkspace />, { me })

    expect(
      await screen.findByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).toBeInTheDocument()
    expect(screen.queryByText(/clinique/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/laboratoire/i)).not.toBeInTheDocument()
  })

  it('treats a suspended organization and a revoked membership as unusable', async () => {
    const me = meWith([
      { ...providerMembership('prov-1', 'Clinique Suspendue'), organization_status: 'SUSPENDED' },
      { ...labMembership('lab-1', 'Laboratoire Révoqué'), membership_status: 'REVOKED' },
    ])
    renderWithWorkspace(<StaffWorkspace />, { me })

    expect(
      await screen.findByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).toBeInTheDocument()
    expect(screen.queryByText('Clinique Suspendue')).not.toBeInTheDocument()
    expect(screen.queryByText('Laboratoire Révoqué')).not.toBeInTheDocument()
  })

  it('reports a failed session load as an error with a retry, not as an empty account', async () => {
    const me = meWith([providerMembership('prov-1', 'Clinique de la Paix')])
    const client = workspaceClient(me, {
      getMe: vi.fn().mockRejectedValue(new ExchangeApiError(500, 'SERVER_ERROR', 'boom')),
    })
    renderWithWorkspace(<StaffWorkspace />, { me, client })

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(
      screen.queryByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).not.toBeInTheDocument()
  })
})
