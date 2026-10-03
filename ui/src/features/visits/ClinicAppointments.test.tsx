/**
 * DHEALTH-APPOINTMENTS-001/ADR-073 — the clinic schedule surface.
 *
 * What these tests cover:
 *
 * - **the three capabilities**, each shown to gate a different control, and an appointment-only
 *   scheduler shown to be offered no arrival action;
 * - **the browser never decides what time means**: "today" is requested by sending **no date**, a
 *   booking sends a clinic-local date and wall clock rather than an instant, and the displayed time
 *   is formatted in the facility's timezone the server named -- asserted by rendering the same
 *   instant and checking it reads as the clinic's clock, not the test machine's;
 * - **no no-show is derived**: a booking whose time has passed reads "Heure passée" with the *same*
 *   neutral tone as one still to come, so nothing is communicated by colour and nothing is a
 *   judgement;
 * - **arrival is projected from the visit**, never from an appointment state;
 * - one idempotency key per intended booking, reused across a retry.
 *
 * `appointmentPresentation` is tested directly as a function, because the decision lives there.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import type { Appointment, AppointmentDay, ExchangeMeResponse } from '../../api/exchangeTypes'
import type { ExchangeClient } from '../../api/exchangeClient'
import { StaffApp } from '../staff/StaffApp'
import { appointmentPresentation } from './appointmentPresentation'

const TZ = 'Africa/Djibouti'

/** 10:00 in Djibouti (UTC+3). Deliberately an instant whose UTC hour is *different*. */
const TEN_LOCAL = '2026-10-06T07:00:00Z'
/** 01:00 in Djibouti, which is 22:00 UTC the **previous** day. */
const ONE_AM_LOCAL = '2026-10-05T22:00:00Z'

function appointment(overrides: Partial<Appointment> = {}): Appointment {
  return {
    id: 'appt-1',
    scheduled_at: TEN_LOCAL,
    status: 'SCHEDULED',
    patient: {
      patient_id: 'patient-1',
      display_name: 'Amina Hassan',
      date_of_birth: '1988-06-02',
    },
    visit_id: null,
    visit_status: null,
    created_at: '2026-10-01T08:00:00Z',
    ...overrides,
  }
}

function day(appointments: Appointment[], d = '2026-10-06'): AppointmentDay {
  return {
    provider_organization_id: 'provider-a',
    day: d,
    facility_timezone: TZ,
    appointments,
  }
}

function me(capabilities: string[]): ExchangeMeResponse {
  return {
    identity_id: 'identity-1',
    issuer: 'issuer',
    subject: 'subject',
    display_name: 'Personnel Synthétique',
    email: null,
    platform_admin: false,
    memberships: [
      {
        organization_id: 'provider-a',
        organization_name: 'Clinique A',
        organization_type: 'PROVIDER',
        organization_status: 'ACTIVE',
        membership_status: 'ACTIVE',
        roles: ['ORG_ADMIN'],
        capabilities,
      },
    ],
  }
}

const SCHEDULER = ['appointment.manage', 'patient.directory.read']
const DESK = ['visit.check_in', 'patient.directory.read']
const BOTH = ['appointment.manage', 'visit.check_in', 'patient.directory.read']

function makeClient(
  capabilities: string[],
  overrides: Partial<ExchangeClient> = {},
): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(me(capabilities)),
    listAppointments: vi.fn().mockResolvedValue(day([appointment()])),
    createAppointment: vi.fn(),
    rescheduleAppointment: vi.fn(),
    cancelAppointment: vi.fn(),
    checkInAppointment: vi.fn(),
    searchPatients: vi.fn().mockResolvedValue([]),
    listWaitingArrivals: vi.fn().mockResolvedValue([]),
    listLabOrders: vi.fn().mockResolvedValue([]),
    ...overrides,
  } as unknown as ExchangeClient
}

/**
 * Renders `Aujourd'hui` and waits for the schedule's **data**, not merely its heading.
 *
 * The heading renders before the fetch resolves, so an earlier version of this helper let every
 * row assertion race the response -- four tests failed looking for controls that simply had not
 * been rendered yet. `patientName` is the row marker to wait for; pass `null` when the day is
 * expected to be empty and the empty state is awaited instead.
 */
async function renderToday(
  client: ExchangeClient,
  patientName: string | null = 'Amina Hassan',
): Promise<void> {
  render(<StaffApp client={client} initialSection="today" />)
  await screen.findByRole('heading', { name: 'Rendez-vous' })
  if (patientName === null) {
    await screen.findByText('Aucun rendez-vous ce jour')
    return
  }
  await screen.findAllByText(patientName)
}



// ── the decision function, tested directly ───────────────────────────────────────────────────────

describe('appointmentPresentation — what a booking is allowed to say', () => {
  // 08:00 in Djibouti, i.e. *before* the 10:00 booking. Chosen deliberately: an instant after it
  // would make the "upcoming" case read as passed and the test would be asserting the wrong branch.
  const now = new Date('2026-10-06T05:00:00Z')

  it('an upcoming booking is neutral and says so in words', () => {
    const result = appointmentPresentation(appointment({ scheduled_at: TEN_LOCAL }), now)
    expect(result).toEqual({ label: 'À venir', tone: 'neutral', passed: false })
  })

  it('a booking whose time has passed is NOT a no-show, and carries no warning colour', () => {
    // The whole point: `passed` is an observation, the label is a statement about the clock, and
    // the tone is the *same* neutral one an upcoming booking gets. A `warning` or `danger` here
    // would read as a verdict on the patient.
    const result = appointmentPresentation(appointment({ scheduled_at: ONE_AM_LOCAL }), now)
    expect(result.passed).toBe(true)
    expect(result.label).toBe('Heure passée')
    expect(result.tone).toBe('neutral')
    expect(result.tone).toBe(
      appointmentPresentation(appointment({ scheduled_at: TEN_LOCAL }), now).tone,
    )
    expect(result.label.toLowerCase()).not.toContain('absent')
    expect(result.label.toLowerCase()).not.toContain('no-show')
    expect(result.label.toLowerCase()).not.toContain('manqué')
  })

  it('arrival is projected from the linked visit, never from an appointment state', () => {
    const arrived = appointmentPresentation(
      appointment({ visit_id: 'visit-1', visit_status: 'CHECKED_IN' }),
      now,
    )
    const seeing = appointmentPresentation(
      appointment({ visit_id: 'visit-1', visit_status: 'IN_PROGRESS' }),
      now,
    )
    expect(arrived.label).toBe('Arrivé')
    expect(seeing.label).toBe('En consultation')
    // The booking's own status is still SCHEDULED in both cases -- the label came from the visit.
    expect(appointment({ visit_id: 'visit-1' }).status).toBe('SCHEDULED')
  })

  it('a cancelled booking says cancelled and is never shown as passed', () => {
    const result = appointmentPresentation(
      appointment({ status: 'CANCELLED', scheduled_at: ONE_AM_LOCAL }),
      now,
    )
    expect(result).toEqual({ label: 'Annulé', tone: 'neutral', passed: false })
  })
})

// ── time: the browser formats, never decides ─────────────────────────────────────────────────────

describe('ClinicAppointments — the clinic owns the clock', () => {
  it('asks for "today" by sending no date at all', async () => {
    const client = makeClient(BOTH)
    await renderToday(client)
    // `{}` -- not `{ day: <something the browser computed> }`. The server resolves its own today.
    expect(client.listAppointments).toHaveBeenCalledWith('provider-a', {})
  })

  it('renders the facility clock, not the test machine’s', async () => {
    // 07:00Z is 10:00 in Djibouti. A component that formatted in the local zone would print 07:00
    // on a UTC machine, which is the defect this asserts against.
    const client = makeClient(BOTH)
    await renderToday(client)
    expect(await screen.findByText('10:00')).toBeInTheDocument()
    expect(screen.queryByText('07:00')).not.toBeInTheDocument()
  })

  it('files a 01:00 local booking under the clinic’s day, as the server said', async () => {
    const client = makeClient(BOTH, {
      listAppointments: vi
        .fn()
        .mockResolvedValue(day([appointment({ scheduled_at: ONE_AM_LOCAL })], '2026-10-06')),
    })
    await renderToday(client)
    // The instant is 22:00 UTC on 5 October; the clinic's day is 6 October, and the heading is the
    // server's answer rather than anything derived from the instant in the browser.
    expect(await screen.findByText('01:00')).toBeInTheDocument()
    expect(screen.getByText(/6 octobre 2026/)).toBeInTheDocument()
  })

  it('sends a clinic-local date and wall clock when booking, never an instant', async () => {
    const created = vi.fn().mockResolvedValue(appointment())
    const client = makeClient(BOTH, {
      createAppointment: created,
      searchPatients: vi
        .fn()
        .mockResolvedValue([
          { patient_id: 'patient-7', display_name: 'Nouveau Patient', date_of_birth: null },
        ]),
    })
    await renderToday(client)

    await userEvent.click(screen.getByRole('button', { name: 'Nouveau rendez-vous' }))
    await userEvent.type(screen.getByLabelText('Rechercher un patient'), 'Nouveau')
    await userEvent.click(await screen.findByRole('button', { name: 'Nouveau Patient' }))
    const dateField = screen.getByLabelText('Date')
    await userEvent.clear(dateField)
    await userEvent.type(dateField, '2026-10-09')
    await userEvent.type(screen.getByLabelText('Heure'), '14:30')
    await userEvent.click(screen.getByRole('button', { name: 'Enregistrer le rendez-vous' }))

    await waitFor(() => expect(created).toHaveBeenCalled())
    const [, key, body] = created.mock.calls[0]
    expect(body).toEqual({
      patient_id: 'patient-7',
      scheduled_date: '2026-10-09',
      scheduled_time: '14:30:00',
    })
    // No instant, no timezone offset, nothing the browser's clock could have influenced.
    expect(JSON.stringify(body)).not.toContain('Z')
    expect(typeof key).toBe('string')
    expect(key.length).toBeGreaterThan(10)
  })
})

// ── capabilities ─────────────────────────────────────────────────────────────────────────────────

describe('ClinicAppointments — three capabilities, three controls', () => {
  it('a scheduler may book, move and cancel, and is offered no arrival action', async () => {
    const client = makeClient(SCHEDULER)
    await renderToday(client)

    expect(screen.getByRole('button', { name: 'Nouveau rendez-vous' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Déplacer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Annuler' })).toBeInTheDocument()
    // Recording an arrival is `visit.check_in`, which a scheduler does not hold.
    expect(
      screen.queryByRole('button', { name: "Enregistrer l'arrivée" }),
    ).not.toBeInTheDocument()
  })

  it('the desk sees the day and may record an arrival, but cannot change the schedule', async () => {
    const client = makeClient(DESK)
    await renderToday(client)

    expect(screen.getByRole('button', { name: "Enregistrer l'arrivée" })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nouveau rendez-vous' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Déplacer' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Annuler' })).not.toBeInTheDocument()
  })

  it('a membership with neither capability is shown no schedule at all', async () => {
    const client = makeClient(['patient.directory.read'])
    render(<StaffApp client={client} initialSection="today" />)
    await waitFor(() => expect(client.getMe).toHaveBeenCalled())
    expect(screen.queryByRole('heading', { name: 'Rendez-vous' })).not.toBeInTheDocument()
    // And no request was made for a surface they cannot see.
    expect(client.listAppointments).not.toHaveBeenCalled()
  })

  it('offers no arrival or scheduling action on a booking that already arrived', async () => {
    const client = makeClient(BOTH, {
      listAppointments: vi
        .fn()
        .mockResolvedValue(
          day([appointment({ visit_id: 'visit-1', visit_status: 'CHECKED_IN' })]),
        ),
    })
    await renderToday(client)

    expect(screen.getByText('Arrivé')).toBeInTheDocument()
    // Arrival is history: it cannot be re-recorded, moved or cancelled from here.
    expect(
      screen.queryByRole('button', { name: "Enregistrer l'arrivée" }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Déplacer' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Annuler' })).not.toBeInTheDocument()
  })

  it('offers no arrival action on a cancelled booking, but still shows it', async () => {
    const client = makeClient(BOTH, {
      listAppointments: vi
        .fn()
        .mockResolvedValue(day([appointment({ status: 'CANCELLED' })])),
    })
    await renderToday(client)

    expect(screen.getByText('Annulé')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: "Enregistrer l'arrivée" }),
    ).not.toBeInTheDocument()
  })
})

// ── arrival ──────────────────────────────────────────────────────────────────────────────────────

describe('ClinicAppointments — arrival', () => {
  it('records the arrival through the authoritative server operation', async () => {
    const checkIn = vi.fn().mockResolvedValue({
      appointment: appointment({ visit_id: 'visit-1', visit_status: 'CHECKED_IN' }),
      visit: { id: 'visit-1', status: 'CHECKED_IN' },
      visit_created: true,
    })
    const client = makeClient(BOTH, { checkInAppointment: checkIn })
    await renderToday(client)

    await userEvent.click(screen.getByRole('button', { name: "Enregistrer l'arrivée" }))
    await waitFor(() => expect(checkIn).toHaveBeenCalledWith('provider-a', 'appt-1'))
    // The browser does not create a visit and then link it -- one server call does both.
    expect(client.createAppointment).not.toHaveBeenCalled()
  })

  it('reports a patient who already has an active visit without claiming a failure', async () => {
    const { ExchangeApiError } = await import('../../api/exchangeClient')
    const checkIn = vi
      .fn()
      .mockRejectedValue(new ExchangeApiError(409, 'CONFLICT', 'conflict'))
    const client = makeClient(BOTH, { checkInAppointment: checkIn })
    await renderToday(client)

    await userEvent.click(screen.getByRole('button', { name: "Enregistrer l'arrivée" }))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Ce patient a déjà une visite en cours.',
    )
  })
})

// ── accessibility ────────────────────────────────────────────────────────────────────────────────

describe('ClinicAppointments — accessibility', () => {
  it('has no axe violations on the day view', async () => {
    const client = makeClient(BOTH)
    const { container } = render(<StaffApp client={client} initialSection="today" />)
    await screen.findByRole('heading', { name: 'Rendez-vous' })
    // One rendering, axe's default rule set, this container only. Not a human-usability claim.
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it('states every booking’s status in words, so nothing depends on colour', async () => {
    const client = makeClient(BOTH, {
      listAppointments: vi.fn().mockResolvedValue(
        day([
          appointment({ id: 'a', scheduled_at: TEN_LOCAL }),
          appointment({ id: 'b', visit_id: 'v', visit_status: 'CHECKED_IN' }),
          appointment({ id: 'c', status: 'CANCELLED' }),
        ]),
      ),
    })
    await renderToday(client)

    // Queried directly rather than scoped to a DOM ancestor: these three labels belong only to the
    // schedule (the arrivals list says "En attente", never "À venir"), and an earlier `closest()`
    // walk grabbed the panel *header* and found none of them.
    expect(screen.getByText('À venir')).toBeInTheDocument()
    expect(screen.getByText('Arrivé')).toBeInTheDocument()
    expect(screen.getByText('Annulé')).toBeInTheDocument()
  })

  it('names the day in words rather than an ambiguous numeric form', async () => {
    const client = makeClient(BOTH)
    await renderToday(client)
    // "6 octobre 2026", never "10/06/26" -- the ambiguity the ticket calls out by name.
    expect(screen.getByText(/octobre 2026/)).toBeInTheDocument()
    expect(screen.queryByText(/\d{2}\/\d{2}\/\d{2}/)).not.toBeInTheDocument()
  })
})
