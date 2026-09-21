/**
 * DH-MVP-ONBOARD-001 §62 — activating production routing from the platform UI.
 *
 * The property these tests protect is that the screen never pretends to be the
 * authority. It may decline to offer a control, and the server decides whether
 * the activation happens; when the two disagree, the server's refusal is what an
 * operator sees, reason by reason, and no success is shown.
 *
 * The other property is the one the whole ticket turns on: a configured route and
 * an available laboratory are separate facts, and a lab that loses its staffing
 * must read as both configured and unavailable rather than as either alone.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../api/exchangeClient'
import type {
  FacilityEnrollment,
  FacilityReadiness,
  ProviderLabRoute,
  ReadinessFinding,
} from '../../api/exchangeTypes'
import { readinessFindingLabel } from './onboardingVocabulary'
import { RouteNetwork } from './RouteNetwork'

function facility(overrides: Partial<FacilityEnrollment> = {}): FacilityEnrollment {
  return {
    organization_id: 'org-1',
    organization_name: 'Établissement',
    organization_type: 'LAB',
    organization_status: 'ACTIVE',
    participation_mode: 'NATIVE',
    onboarding_status: 'READY_FOR_ROUTING',
    integration_feasibility: null,
    existing_system_name: null,
    existing_system_vendor: null,
    existing_system_version: null,
    hosting_model: null,
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
    interfaces: [],
    open_blockers: [],
    ...overrides,
  }
}

const PROVIDER = facility({
  organization_id: 'org-provider',
  organization_name: 'Clinique Al Rahma',
  organization_type: 'PROVIDER',
})

const READY_LAB = facility({
  organization_id: 'org-lab-ready',
  organization_name: 'Laboratoire Central',
})

const CONNECT_LAB = facility({
  organization_id: 'org-lab-connect',
  organization_name: 'Laboratoire Connecté',
  participation_mode: 'CONNECT',
  onboarding_status: 'AWAITING_CONNECTOR',
})

function readiness(
  organizationId: string,
  allowed: boolean,
  findings: ReadinessFinding[] = [],
): FacilityReadiness {
  return {
    organization_id: organizationId,
    participation_mode: 'NATIVE',
    onboarding_status: allowed ? 'READY_FOR_ROUTING' : 'ORGANIZATION_READY',
    production_routing_allowed: allowed,
    findings,
  }
}

const NO_RELEASER: ReadinessFinding[] = [
  { code: 'lab_technician_present', satisfied: true, blocking: true, detail: null },
  { code: 'lab_releaser_present', satisfied: false, blocking: true, detail: null },
]

function route(labId: string, active: boolean): ProviderLabRoute {
  return {
    id: `route-${labId}`,
    provider_organization_id: PROVIDER.organization_id,
    lab_organization_id: labId,
    lab_organization_name: 'Laboratoire',
    active,
    created_at: '2026-09-21T08:00:00Z',
    updated_at: '2026-09-21T08:00:00Z',
  }
}

interface Setup {
  labs?: FacilityEnrollment[]
  routes?: ProviderLabRoute[]
  labReadiness?: Record<string, FacilityReadiness>
  setProviderLabRoute?: ReturnType<typeof vi.fn>
}

function client(setup: Setup = {}) {
  const labReadiness = setup.labReadiness ?? {
    [READY_LAB.organization_id]: readiness(READY_LAB.organization_id, true),
  }
  return {
    listPlatformProviderLabRoutes: vi.fn().mockResolvedValue(setup.routes ?? []),
    // Resolves per organization id rather than a bare vi.fn(): a mock that
    // answers the same thing for every facility would let a test pass over a
    // component that asked about the wrong one.
    getFacilityReadiness: vi.fn(async (organizationId: string) => {
      const found = labReadiness[organizationId]
      if (!found) throw new Error(`no readiness fixture for ${organizationId}`)
      return found
    }),
    setProviderLabRoute: setup.setProviderLabRoute ?? vi.fn().mockResolvedValue(route('x', true)),
  } as unknown as ExchangeClient & { setProviderLabRoute: ReturnType<typeof vi.fn> }
}

function renderNetwork(
  c: ExchangeClient,
  setup: Setup = {},
  providerReady: FacilityReadiness | null = readiness(PROVIDER.organization_id, true),
) {
  return render(
    <RouteNetwork
      provider={PROVIDER}
      facilities={[PROVIDER, ...(setup.labs ?? [READY_LAB])]}
      providerReadiness={providerReady}
      client={c}
      onChanged={() => {}}
    />,
  )
}

async function rowFor(name: string | RegExp) {
  const heading = await screen.findByText(name)
  const row = heading.closest('li')
  if (!row) throw new Error('row not found')
  return row
}

describe('route activation from the platform', () => {
  it('RN1: a ready Native laboratory can be activated, after a restated confirmation', async () => {
    const user = userEvent.setup()
    const setProviderLabRoute = vi.fn().mockResolvedValue(route(READY_LAB.organization_id, true))
    const c = client({ setProviderLabRoute })
    renderNetwork(c)

    await user.click(await screen.findByRole('button', { name: 'Activer' }))

    // One gesture must not be enough, and the second one must be described by a
    // restatement naming both ends and what activation permits.
    const confirm = screen.getByRole('button', { name: 'Activer' })
    const restatementId = confirm.getAttribute('aria-describedby')
    expect(restatementId).toBeTruthy()
    const restatement = document.getElementById(restatementId as string)
    expect(restatement?.textContent).toContain('Clinique Al Rahma')
    expect(restatement?.textContent).toContain('Laboratoire Central')
    expect(restatement?.textContent).toMatch(/nouveaux ordres/)
    expect(setProviderLabRoute).not.toHaveBeenCalled()

    await user.click(confirm)
    await waitFor(() =>
      expect(setProviderLabRoute).toHaveBeenCalledWith(
        PROVIDER.organization_id,
        READY_LAB.organization_id,
        true,
      ),
    )
  })

  it('RN2: a Connect laboratory offers no activation, and says why instead', async () => {
    const c = client({
      labs: [CONNECT_LAB],
      labReadiness: {
        // Even generous readiness changes nothing: no connector exists.
        [CONNECT_LAB.organization_id]: readiness(CONNECT_LAB.organization_id, false, [
          { code: 'connector_qualified', satisfied: false, blocking: true, detail: null },
        ]),
      },
    })
    renderNetwork(c, { labs: [CONNECT_LAB] })

    const row = await rowFor('Laboratoire Connecté')
    expect(within(row).queryByRole('button', { name: 'Activer' })).toBeNull()
    expect(row.textContent).toMatch(/Aucun connecteur DHealth/)
    // And the state it reports is the absence of a link, never a claim of one.
    expect(row.textContent).toMatch(/Non connecté/)
  })

  it('RN3: a refusal after readiness changed shows the server reasons and no success', async () => {
    const user = userEvent.setup()
    // The screen was drawn while the lab was ready; by the time the button is
    // pressed it is not. The server is the one that knows.
    const setProviderLabRoute = vi.fn().mockRejectedValue(
      new ExchangeApiError(409, 'CONFLICT', 'conflict', {
        detail: {
          message: "cet établissement n'est pas prêt pour le routage de production",
          blocking_reasons: [{ side: 'lab', code: 'lab_releaser_present' }],
        },
      }),
    )
    const c = client({ setProviderLabRoute })
    renderNetwork(c)

    await user.click(await screen.findByRole('button', { name: 'Activer' }))
    await user.click(screen.getByRole('button', { name: 'Activer' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/refusée par le serveur/)
    expect(alert.textContent).toContain('Laboratoire')
    expect(alert.textContent).toContain(readinessFindingLabel('lab_releaser_present', false))
    // "Aucune autorité de libération active", not the satisfied phrasing.
    expect(alert.textContent).toMatch(/Aucune autorité de libération active/)
    expect(alert.textContent).not.toMatch(/activé|Prêt/)
  })

  it('RN4: a configured route to an unavailable laboratory stays configured', async () => {
    // The ticket's sharpest distinction. Losing a releaser must not read as the
    // relationship having been torn up, and must not read as the lab being fine.
    const c = client({
      labs: [READY_LAB],
      routes: [route(READY_LAB.organization_id, true)],
      labReadiness: {
        [READY_LAB.organization_id]: readiness(READY_LAB.organization_id, false, NO_RELEASER),
      },
    })
    renderNetwork(c, { labs: [READY_LAB] })

    const row = await rowFor('Laboratoire Central')
    expect(row.textContent).toMatch(/Relation configurée/)
    expect(row.textContent).toMatch(/Routage indisponible/)
    expect(row.textContent).toMatch(/Aucune autorité de libération active/)
    // Switching routing off must stay available exactly when a facility cannot
    // take work — it is never gated, here or on the server.
    expect(within(row).getByRole('button', { name: 'Désactiver' })).toBeEnabled()
    expect(within(row).queryByRole('button', { name: 'Activer' })).toBeNull()
  })

  it('RN5: a provider with nobody who may order cannot activate anything', async () => {
    const c = client()
    renderNetwork(
      c,
      {},
      readiness(PROVIDER.organization_id, false, [
        { code: 'clinician_present', satisfied: false, blocking: true, detail: null },
      ]),
    )

    await screen.findByText(/Laboratoire Central/)
    expect(screen.queryByRole('button', { name: 'Activer' })).toBeNull()
    expect(screen.getByText(/Aucun clinicien actif/)).toBeInTheDocument()
  })

  it('RN6: an unreadable laboratory is not reported as unready', async () => {
    // "We could not evaluate this" and "this is not ready" are different claims.
    // Collapsing them would put a false statement about a facility on screen.
    const c = {
      listPlatformProviderLabRoutes: vi.fn().mockResolvedValue([]),
      getFacilityReadiness: vi.fn().mockRejectedValue(new Error('boom')),
      setProviderLabRoute: vi.fn(),
    } as unknown as ExchangeClient
    renderNetwork(c)

    const row = await rowFor('Laboratoire Central')
    expect(row.textContent).toMatch(/Disponibilité non lue/)
    expect(within(row).queryByRole('button', { name: 'Activer' })).toBeNull()
  })

  it('RN7: an unknown readiness code renders as itself rather than vanishing', () => {
    // A blocking reason this build does not have a translation for must still
    // reach the operator. Dropping it would leave a refusal with no explanation.
    expect(readinessFindingLabel('a_code_from_a_newer_backend', false)).toBe(
      'a_code_from_a_newer_backend',
    )
    expect(readinessFindingLabel('a_code_from_a_newer_backend', true)).toBe(
      'a_code_from_a_newer_backend',
    )
  })
})
