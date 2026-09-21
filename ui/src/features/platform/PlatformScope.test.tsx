/**
 * DH-MVP-ONBOARD-001 — platform scope, and the fake organization nobody built.
 *
 * The central claim under test: a DHealth platform administrator who holds no
 * membership anywhere still reaches network administration, and does so without
 * any organization being invented to satisfy the shell. If a "DHealth
 * Headquarters" ever appears, every capability check and every org-scoped read
 * starts answering questions about a facility that does not exist — so these
 * tests assert the absence directly, not just the presence of the platform UI.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../api/exchangeClient'
import type { ExchangeMeResponse, FacilityEnrollment } from '../../api/exchangeTypes'
import { StaffWorkspace } from '../staff/StaffWorkspace'
import { WorkspaceProvider } from '../workspace/WorkspaceProvider'

function me(overrides: Partial<ExchangeMeResponse> = {}): ExchangeMeResponse {
  return {
    identity_id: 'identity-1',
    issuer: 'issuer',
    subject: 'subject',
    display_name: 'Opérateur Plateforme',
    email: null,
    platform_admin: true,
    memberships: [],
    ...overrides,
  }
}

function facility(overrides: Partial<FacilityEnrollment> = {}): FacilityEnrollment {
  return {
    organization_id: 'org-lab',
    organization_name: 'Laboratoire Central',
    organization_type: 'LAB',
    organization_status: 'ACTIVE',
    participation_mode: 'CONNECT',
    onboarding_status: 'AWAITING_CONNECTOR',
    integration_feasibility: 'CONNECTOR_DEVELOPMENT_REQUIRED',
    existing_system_name: 'XYZ LIS',
    existing_system_vendor: 'ABC Systems',
    existing_system_version: '8.4',
    hosting_model: 'ON_PREMISE',
    technical_contact: null,
    vendor_contact: null,
    documentation_reference: null,
    patient_identifier_namespace: null,
    order_identifier_concept: null,
    report_identifier_concept: null,
    specimen_identifier_concept: null,
    exposes_source_event_timestamps: null,
    exposes_professional_release_event: null,
    notes: null,
    interfaces: [{ interface_type: 'FHIR', verification_state: 'REPORTED', detail: null }],
    open_blockers: [],
    ...overrides,
  }
}

function client(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(me()),
    listFacilities: vi.fn().mockResolvedValue([facility()]),
    getFacilityReadiness: vi.fn().mockResolvedValue({
      organization_id: 'org-lab',
      participation_mode: 'CONNECT',
      onboarding_status: 'AWAITING_CONNECTOR',
      production_routing_allowed: false,
      findings: [
        { code: 'lab_accepting_orders', satisfied: true, blocking: true, detail: null },
        { code: 'connector_qualified', satisfied: false, blocking: true, detail: null },
      ],
    }),
    ...overrides,
  } as unknown as ExchangeClient
}

function renderPlatform(c: ExchangeClient) {
  return render(
    <WorkspaceProvider client={c}>
      <StaffWorkspace />
    </WorkspaceProvider>,
  )
}

describe('platform scope', () => {
  it('PS1: a platform admin with no membership reaches network administration', async () => {
    renderPlatform(client())

    expect(await screen.findByRole('heading', { name: 'Établissements' })).toBeInTheDocument()
    // Not the staff dead end. Telling somebody who administers the whole network
    // that no establishment is attached to their account would be true and useless.
    expect(
      screen.queryByText(/Aucun établissement actif n'est rattaché/),
    ).not.toBeInTheDocument()
  })

  it('PS2: invents no organization to satisfy the shell', async () => {
    renderPlatform(client())
    await screen.findByRole('heading', { name: 'Établissements' })

    const masthead = screen.getByRole('banner', { name: 'Session et établissement actif' })
    // The network is named as the network. No fabricated facility, and no
    // establishment kind claimed for something that is not an establishment.
    expect(within(masthead).getByText('Réseau DHealth')).toBeInTheDocument()
    expect(within(masthead).queryByText(/Headquarters|Siège|DHealth Platform Org/i)).toBeNull()
    expect(within(masthead).queryByText("Laboratoire d'analyses")).toBeNull()
    expect(within(masthead).queryByText('Établissement de soins')).toBeNull()
  })

  it('PS3: an ordinary user with no membership gets the staff message, not the network', async () => {
    renderPlatform(
      client({ getMe: vi.fn().mockResolvedValue(me({ platform_admin: false })) }),
    )

    expect(
      await screen.findByText(/Aucun établissement actif n'est rattaché/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Établissements' })).not.toBeInTheDocument()
  })

  it('PS4: a platform admin who also holds a membership can move between both contexts', async () => {
    const user = userEvent.setup()
    renderPlatform(
      client({
        getMe: vi.fn().mockResolvedValue(
          me({
            memberships: [
              {
                organization_id: 'prov-1',
                organization_name: 'Clinique de la Paix',
                organization_type: 'PROVIDER',
                organization_status: 'ACTIVE',
                membership_status: 'ACTIVE',
                roles: ['CLINICIAN'],
              },
            ],
          }),
        ),
        listPatients: vi.fn().mockResolvedValue([]),
        listOrders: vi.fn().mockResolvedValue([]),
        listResultReviews: vi.fn().mockResolvedValue({ entries: [] }),
      }),
    )

    // Holding a membership means organization scope comes first: the person is
    // primarily a clinician who also administers the network.
    //
    // Scoped to the identity band's own span rather than the whole masthead: the
    // switcher lists this clinic as an `<option>` in both scopes, quite correctly,
    // so an unscoped query cannot tell "we are acting as this clinic" from "this
    // clinic is one of the places we could act as".
    const masthead = await screen.findByRole('banner', { name: 'Session et établissement actif' })
    const identity = () => within(masthead).queryByText('Clinique de la Paix', { selector: 'span' })
    expect(identity()).toBeInTheDocument()

    await user.selectOptions(
      screen.getByLabelText('Contexte actif'),
      screen.getByRole('option', { name: 'Réseau DHealth' }),
    )

    expect(await screen.findByRole('heading', { name: 'Établissements' })).toBeInTheDocument()
    // Platform context does not carry the clinic's identity with it.
    expect(identity()).toBeNull()
    expect(within(masthead).getByText('Réseau DHealth', { selector: 'span' })).toBeInTheDocument()
    // The clinic remains reachable — switching scope is not leaving the facility.
    expect(screen.getByRole('option', { name: 'Clinique de la Paix' })).toBeInTheDocument()
  })

  it('PS5: a stale platform capability is refused by the server and said plainly', async () => {
    // The UI hides what a person cannot do; the server is what actually refuses.
    // A session that believes it is a platform admin after the grant was removed
    // must be told the truth rather than shown a generic failure.
    const forbidden = Object.assign(new Error('forbidden'), { status: 403 })
    renderPlatform(client({ listFacilities: vi.fn().mockRejectedValue(forbidden) }))

    expect(
      await screen.findByText(/n'est pas prêt|ne dispose pas \(ou plus\)/),
    ).toBeInTheDocument()
  })
})

describe('the connect facility, told truthfully', () => {
  /**
   * The status cell of one interface row.
   *
   * The same words appear in that row's own select options, so an unscoped query
   * matches several elements and proves nothing about what is actually being
   * stated. What matters is the cell that reports DHealth's current knowledge.
   */
  function interfaceStatus(name: RegExp) {
    const table = screen.getByRole('table', { name: /Interfaces signalées/ })
    const row = within(table).getByRole('row', { name })
    return within(row).getAllByRole('cell')[0]
  }

  it('PS6: a reported interface is never shown as verified or connected', async () => {
    const user = userEvent.setup()
    renderPlatform(client())

    await user.click(await screen.findByRole('button', { name: /Laboratoire Central/ }))
    await screen.findByRole('table', { name: /Interfaces signalées/ })

    const status = interfaceStatus(/FHIR/)
    expect(status).toHaveTextContent('Signalé par le partenaire')
    expect(status).not.toHaveTextContent(/vérifiée/i)
    // The word that would be a lie.
    expect(document.body.textContent).not.toMatch(/FHIR connecté|interface connectée/i)
  })

  it('PS7: a verified interface still does not make a connector exist', async () => {
    const user = userEvent.setup()
    renderPlatform(
      client({
        listFacilities: vi.fn().mockResolvedValue([
          facility({
            interfaces: [
              { interface_type: 'FHIR', verification_state: 'VERIFIED', detail: null },
            ],
          }),
        ]),
      }),
    )

    await user.click(await screen.findByRole('button', { name: /Laboratoire Central/ }))

    await screen.findByRole('table', { name: /Interfaces signalées/ })
    expect(interfaceStatus(/FHIR/)).toHaveTextContent('Interface vérifiée')
    expect(screen.getByText(/Connecteur DHealth — à construire/)).toBeInTheDocument()
    expect(screen.getByText(/Qualification — non commencée/)).toBeInTheDocument()
    expect(screen.getByText(/Routage de production — désactivé/)).toBeInTheDocument()
  })

  it('PS8: production routing is reported as unavailable, from the server verdict', async () => {
    const user = userEvent.setup()
    renderPlatform(client())

    await user.click(await screen.findByRole('button', { name: /Laboratoire Central/ }))

    await waitFor(() => expect(screen.getByText('Indisponible')).toBeInTheDocument())
    // The reason is the server's own finding, rendered rather than recomputed --
    // and worded as the absence. Under "ce qui empêche la mise en service", the
    // satisfied phrasing ("Connecteur DHealth qualifié") would state the opposite
    // of what is wrong.
    expect(screen.getByText('Connecteur DHealth non qualifié')).toBeInTheDocument()
    expect(screen.queryByText('Connecteur DHealth qualifié')).not.toBeInTheDocument()
  })

  it('PS9: an unintegrable facility is kept, with the reason and the next step', async () => {
    const user = userEvent.setup()
    renderPlatform(
      client({
        listFacilities: vi.fn().mockResolvedValue([
          facility({
            integration_feasibility: 'NOT_SAFELY_INTEGRABLE',
            open_blockers: [
              {
                id: 'blk-1',
                code: 'REQUIRED_INTERFACE_UNAVAILABLE',
                note: "aucune interface supportée par l'éditeur",
                created_at: '2026-09-21T08:00:00Z',
              },
            ],
          }),
        ]),
      }),
    )

    await user.click(await screen.findByRole('button', { name: /Laboratoire Central/ }))

    expect(await screen.findByText(/Intégration actuellement bloquée/)).toBeInTheDocument()
    expect(screen.getByText(/reste enregistré dans DHealth/)).toBeInTheDocument()
    expect(screen.getByText(/Aucune interface utilisable disponible/)).toBeInTheDocument()
  })

  it('PS10: no backend enum reaches the screen', async () => {
    const user = userEvent.setup()
    renderPlatform(client())
    await user.click(await screen.findByRole('button', { name: /Laboratoire Central/ }))
    await screen.findByRole('table', { name: /Interfaces signalées/ })

    for (const enumValue of [
      'AWAITING_CONNECTOR',
      'CONNECT',
      'LAB',
      'REPORTED',
      'ON_PREMISE',
      'CONNECTOR_DEVELOPMENT_REQUIRED',
      'REQUIRED_INTERFACE_UNAVAILABLE',
    ]) {
      expect(document.body.textContent).not.toContain(enumValue)
    }
  })
})
