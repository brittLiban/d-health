/**
 * DHEALTH-FRONT-DESK-001/ADR-069 — the arrival surface, tested for the things that would make it
 * dangerous rather than merely broken.
 *
 * The properties under test are about **authority and truthfulness**, not layout:
 *
 * - A reception-only membership (`patient.directory.read` + `patient.register` + `visit.check_in`,
 *   and **no role**) can run the whole arrival: search, confirm, check in. Every assertion here
 *   would fail if any control keyed off a job title, because this membership has none.
 * - That same membership is offered **no `Ouvrir`** and no `Commencer la consultation` — §47. The
 *   absence is the usability half of a refusal the server enforces; a button that 403s teaches
 *   people to distrust the interface.
 * - `Nouveau patient` appears only with `patient.register` (§8), so a member who may check in
 *   existing patients but not create records is not offered a form the server would refuse.
 * - Removing `visit.check_in` removes the arrival control, with no role change and no reload —
 *   §26's UI half.
 * - The identity of the patient is on screen **before** anything is created (§31): a click on a
 *   search result opens a confirmation, never a visit.
 * - Nothing computes a waiting time, a priority or a triage order (`AGENTS.md` 2026-09-27,
 *   `docs/NATIVE-OPERATING-PRODUCT.md` §38). The recorded arrival time is rendered; no arithmetic
 *   is performed on it here or anywhere.
 * - A 409 says the patient is already waiting and refetches, rather than offering a retry that will
 *   be refused again — the two-receptionists case is ordinary, not an error to apologise for.
 *
 * Every patient, name and identifier below is synthetic.
 */

import { StrictMode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ExchangeApiError, type ExchangeClient } from '../../api/exchangeClient'
import type {
  ExchangeMeResponse,
  PatientDirectoryEntry,
  WaitingArrival,
} from '../../api/exchangeTypes'
import { StaffApp } from '../staff/StaffApp'

const PROVIDER_ID = 'prov-1'
const RECEPTION_ID = 'identity-reception'
const PATIENT_ID = '11111111-1111-4111-8111-111111111111'
const PATIENT_REF_ID = '22222222-2222-4222-8222-222222222222'
const VISIT_ID = '33333333-3333-4333-8333-333333333333'

const RECEPTION_CAPABILITIES = ['patient.directory.read', 'patient.register', 'visit.check_in']
const CLINICIAN_CAPABILITIES = [
  'order.manage',
  'patient.directory.read',
  'patient.register',
  'result.review',
  'visit.check_in',
  'visit.document',
  'visit.read',
]

/**
 * The membership under test. **`roles: []` is the point of this fixture**, not an omission: the
 * whole architectural claim of ADR-069 is that a person with no job title and three capabilities can
 * do this work, so every test in this file would be meaningless with a role present.
 */
function me(capabilities: readonly string[], roles: readonly string[] = []): ExchangeMeResponse {
  return {
    identity_id: RECEPTION_ID,
    issuer: 'https://keycloak.test/realms/dhealth-exchange',
    subject: 'subject-reception',
    display_name: 'Agent d’accueil',
    email: 'accueil@clinique.test',
    platform_admin: false,
    memberships: [
      {
        organization_id: PROVIDER_ID,
        organization_name: 'Clinique de la Paix',
        organization_type: 'PROVIDER',
        organization_status: 'ACTIVE',
        membership_status: 'ACTIVE',
        roles: [...roles],
        capabilities: [...capabilities],
      },
    ],
  }
}

const PATIENT: PatientDirectoryEntry = {
  patient_id: PATIENT_ID,
  provider_organization_id: PROVIDER_ID,
  display_name: 'Patiente Synthétique',
  date_of_birth: '1994-03-14',
  contact_channel_type: null,
  contact_value: null,
  primary_patient_reference_id: PATIENT_REF_ID,
  created_at: '2026-09-30T07:00:00Z',
}

function arrival(overrides: Partial<WaitingArrival> = {}): WaitingArrival {
  return {
    visit_id: VISIT_ID,
    patient_id: PATIENT_ID,
    patient_display_name: PATIENT.display_name,
    patient_date_of_birth: PATIENT.date_of_birth,
    status: 'CHECKED_IN',
    checked_in_at: '2026-09-30T09:12:00Z',
    ...overrides,
  }
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(me(RECEPTION_CAPABILITIES)),
    listWaitingArrivals: vi.fn().mockResolvedValue([]),
    searchPatients: vi.fn().mockResolvedValue([PATIENT]),
    listPatients: vi.fn().mockResolvedValue([PATIENT]),
    startVisitWithOutcome: vi.fn().mockResolvedValue({ visit: {}, created: true }),
    takeVisit: vi.fn(),
    cancelVisit: vi.fn(),
    // A reception membership never reaches these; stubbed so an accidental call is a visible
    // assertion failure rather than an unhandled rejection that looks like a render bug.
    listOrders: vi.fn().mockRejectedValue(new Error('reception must not list orders')),
    listVisits: vi.fn().mockRejectedValue(new Error('reception must not list visits')),
    ...overrides,
  } as unknown as ExchangeClient
}

function renderToday(client: ExchangeClient) {
  return Object.assign(
    render(
      <StrictMode>
        <StaffApp client={client} initialSection="today" />
      </StrictMode>,
    ),
    { client },
  )
}

async function openTheArrivalForm() {
  await userEvent.click(await screen.findByRole('button', { name: 'Accueillir un patient' }))
}

describe("Aujourd'hui — the arrival surface", () => {
  it('lets a membership with no role at all run the whole arrival', async () => {
    const client = makeClient()
    renderToday(client)

    await openTheArrivalForm()
    await userEvent.type(await screen.findByLabelText('Rechercher un patient'), 'Patiente')

    // The identity is shown, and choosing the result opens a confirmation rather than creating
    // anything: §31, wrong-patient safety.
    const result = await screen.findByRole('button', { name: /Patiente Synthétique/ })
    await userEvent.click(result)
    expect(client.startVisitWithOutcome).not.toHaveBeenCalled()

    // Name **and** date of birth on the confirmation -- a name alone can be genuinely ambiguous.
    expect(await screen.findByText('Patiente Synthétique')).toBeInTheDocument()
    expect(screen.getByText(/14\/03\/1994/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Accueillir' }))

    await waitFor(() => expect(client.startVisitWithOutcome).toHaveBeenCalledTimes(1))
    const [providerId, idempotencyKey, body] = (
      client.startVisitWithOutcome as ReturnType<typeof vi.fn>
    ).mock.calls[0]
    expect(providerId).toBe(PROVIDER_ID)
    expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/)
    // §15: no reason is collected at reception, and the request carries no clinician identity.
    expect(body).toEqual({ patient_id: PATIENT_ID })
  })

  it('shows the recorded arrival time and never a waiting duration or a priority', async () => {
    renderToday(makeClient({ listWaitingArrivals: vi.fn().mockResolvedValue([arrival()]) }))

    expect(await screen.findByText('Patiente Synthétique')).toBeInTheDocument()
    expect(screen.getByText('En attente')).toBeInTheDocument()

    // A waiting time computed in a browser from two clocks is an operational signal DHealth must
    // not produce (`AGENTS.md`, 2026-09-27). Nothing here derives one, and nothing implies urgency.
    for (const invented of [
      /il y a/i,
      /minutes d'attente/i,
      /depuis \d/i,
      /priorit/i,
      /urgent/i,
      /retard/i,
      /triage/i,
    ]) {
      expect(screen.queryByText(invented)).not.toBeInTheDocument()
    }
  })

  it('offers a reception-only member no way into the clinical record', async () => {
    renderToday(makeClient({ listWaitingArrivals: vi.fn().mockResolvedValue([arrival()]) }))
    await screen.findByText('Patiente Synthétique')

    // §47: no `Ouvrir` without `visit.read`, no `Commencer` without `visit.document`. The server
    // refuses both regardless; their absence is so nobody is offered work they cannot complete.
    expect(screen.queryByRole('button', { name: 'Ouvrir' })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /Commencer la consultation/ }),
    ).not.toBeInTheDocument()

    // And no clinical content reaches the card, because the projection has no field for it.
    expect(screen.queryByText(/motif/i)).not.toBeInTheDocument()
  })

  it('offers a clinician the handoff on the same waiting card', async () => {
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue(me(CLINICIAN_CAPABILITIES, ['CLINICIAN'])),
      listWaitingArrivals: vi.fn().mockResolvedValue([arrival()]),
      listOrders: vi.fn().mockResolvedValue([]),
      takeVisit: vi.fn().mockResolvedValue({ id: VISIT_ID, status: 'IN_PROGRESS' }),
    })
    renderToday(client)
    await screen.findByText('Patiente Synthétique')

    await userEvent.click(screen.getByRole('button', { name: /Commencer la consultation/ }))
    // The **same** visit id the desk created -- no conversion, no second encounter record.
    await waitFor(() => expect(client.takeVisit).toHaveBeenCalledWith(PROVIDER_ID, VISIT_ID))
  })

  it('stops offering Annuler once the consultation has begun', async () => {
    renderToday(
      makeClient({
        listWaitingArrivals: vi.fn().mockResolvedValue([arrival({ status: 'IN_PROGRESS' })]),
      }),
    )
    await screen.findByText('Patiente Synthétique')

    // §21/§23: the desk can see the patient has been taken, and can no longer undo the arrival.
    expect(screen.getByText('En consultation')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Annuler l'arrivée/ })).not.toBeInTheDocument()
  })

  it('offers Nouveau patient only to a member who may register one', async () => {
    const withRegister = makeClient({ searchPatients: vi.fn().mockResolvedValue([]) })
    renderToday(withRegister)
    await openTheArrivalForm()
    await userEvent.type(await screen.findByLabelText('Rechercher un patient'), 'Inconnu')
    expect(await screen.findByRole('button', { name: 'Nouveau patient' })).toBeInTheDocument()
  })

  it('withholds Nouveau patient from a member who may not register one', async () => {
    renderToday(
      makeClient({
        getMe: vi
          .fn()
          .mockResolvedValue(me(['patient.directory.read', 'visit.check_in'])),
        searchPatients: vi.fn().mockResolvedValue([]),
      }),
    )
    await openTheArrivalForm()
    await userEvent.type(await screen.findByLabelText('Rechercher un patient'), 'Inconnu')

    expect(await screen.findByText('Aucun patient trouvé.')).toBeInTheDocument()
    // §8: registration is separately permissioned, so the control is absent rather than refused.
    expect(screen.queryByRole('button', { name: 'Nouveau patient' })).not.toBeInTheDocument()
  })

  it('removes the arrival control when the capability is removed, with no role change', async () => {
    // §26's UI half: the same person, the same (empty) role list, one capability fewer.
    renderToday(
      makeClient({
        getMe: vi.fn().mockResolvedValue(me(['patient.directory.read', 'patient.register'])),
      }),
    )
    // `Aujourd'hui` still exists -- they hold `patient.directory.read` -- but arrival is gone.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Accueillir un patient' })).not.toBeInTheDocument(),
    )
  })

  it('says the patient is already waiting on a 409, and refetches instead of retrying', async () => {
    const conflict = new ExchangeApiError(
      409,
      'CONFLICT',
      'this patient already has an active visit at this organization',
    )
    const listWaitingArrivals = vi.fn().mockResolvedValue([arrival()])
    const client = makeClient({
      listWaitingArrivals,
      startVisitWithOutcome: vi.fn().mockRejectedValue(conflict),
    })
    renderToday(client)
    await openTheArrivalForm()
    await userEvent.type(await screen.findByLabelText('Rechercher un patient'), 'Patiente')
    await userEvent.click(await screen.findByRole('button', { name: /Patiente Synthétique/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Accueillir' }))

    // The ordinary two-receptionists case: stated plainly, and the queue is made current.
    expect(await screen.findByText(/déjà en attente ou en consultation/)).toBeInTheDocument()
    await waitFor(() => expect(listWaitingArrivals.mock.calls.length).toBeGreaterThan(1))
  })

  it('tells a member with no waiting patients so, rather than rendering an empty card', async () => {
    renderToday(makeClient())
    expect(await screen.findByText("Personne n'attend")).toBeInTheDocument()
  })
})
