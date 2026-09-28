/**
 * DHEALTH-CLINIC-VISIT-001 — Visites, tested for the things that would make it dangerous rather
 * than merely broken.
 *
 * The properties under test are mostly about *truthfulness and refusal*:
 *
 * - A visit already in consultation offers no "Annuler". §12 records that as a
 *   completed-with-a-note, because contact with the patient happened, and a status must not be able
 *   to erase it. Offering the control and letting the server refuse would be worse than not
 *   offering it.
 * - The note editor is empty when it opens. Pre-filling it with the current text would make an
 *   append look like an edit, and a clinician adding one line would silently re-author the whole
 *   note under their own name.
 * - Every revision stays on screen. A corrected note's history is part of the record.
 * - No note field before the visit is taken: a waiting visit has no clinician to attribute it to.
 * - A completed visit says its note is closed rather than offering a field the server refuses.
 * - Ordering hands off to `Patients` with the patient *and the visit*, and the intent created there
 *   carries `visit_id`. That is the whole linkage, and it is the one thing that silently produces a
 *   wrong clinical record if it breaks.
 * - Nothing computes a waiting time, a priority or a triage order.
 *
 * Every patient, name and identifier below is synthetic.
 */

import { StrictMode } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ExchangeApiError, type ExchangeClient } from '../../api/exchangeClient'
import type {
  ExchangeMeResponse,
  PatientDirectoryEntry,
  Visit,
  VisitDetail,
} from '../../api/exchangeTypes'
import { StaffApp } from '../staff/StaffApp'

const PROVIDER_ID = 'prov-1'
const CLINICIAN_ID = 'identity-clinician'
const OTHER_CLINICIAN_ID = 'identity-other'
const PATIENT_ID = '11111111-1111-4111-8111-111111111111'
const PATIENT_REF_ID = '22222222-2222-4222-8222-222222222222'
const VISIT_ID = '33333333-3333-4333-8333-333333333333'

const ME: ExchangeMeResponse = {
  identity_id: CLINICIAN_ID,
  issuer: 'https://keycloak.test/realms/dhealth-exchange',
  subject: 'subject-clinician',
  display_name: 'Dr Halima Farah',
  email: 'halima@clinique.test',
  platform_admin: false,
  memberships: [
    {
      organization_id: PROVIDER_ID,
      organization_name: 'Clinique de la Paix',
      organization_type: 'PROVIDER',
      organization_status: 'ACTIVE',
      membership_status: 'ACTIVE',
      roles: ['CLINICIAN'],
      capabilities: ['order.manage', 'patient.directory.read', 'patient.register', 'result.review', 'visit.document', 'visit.read'],
    },
  ],
}

const PATIENT: PatientDirectoryEntry = {
  patient_id: PATIENT_ID,
  provider_organization_id: PROVIDER_ID,
  display_name: 'Patiente Synthétique',
  date_of_birth: '1990-04-17',
  contact_channel_type: null,
  contact_value: null,
  primary_patient_reference_id: PATIENT_REF_ID,
  created_at: '2026-09-27T07:00:00Z',
}

function visit(overrides: Partial<Visit> = {}): Visit {
  return {
    id: VISIT_ID,
    provider_organization_id: PROVIDER_ID,
    patient_id: PATIENT_ID,
    patient_display_name: PATIENT.display_name,
    patient_date_of_birth: PATIENT.date_of_birth,
    clinician_identity_id: null,
    status: 'CHECKED_IN',
    reason_for_visit: 'Fièvre depuis deux jours',
    created_at: '2026-09-27T08:12:00Z',
    started_at: null,
    completed_at: null,
    cancelled_at: null,
    ...overrides,
  }
}

function detail(overrides: Partial<VisitDetail> = {}): VisitDetail {
  return { visit: visit(), notes: [], orders: [], ...overrides }
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(ME),
    listVisits: vi.fn().mockResolvedValue([visit()]),
    getVisit: vi.fn().mockResolvedValue(detail()),
    listPatients: vi.fn().mockResolvedValue([PATIENT]),
    searchPatients: vi.fn().mockResolvedValue([PATIENT]),
    getPatient: vi.fn().mockResolvedValue(PATIENT),
    listPatientOrders: vi.fn().mockResolvedValue([]),
    listCatalogTests: vi.fn().mockResolvedValue([]),
    getEligibleLabs: vi.fn().mockResolvedValue([]),
    ...overrides,
  } as unknown as ExchangeClient
}

function renderVisits(client: ExchangeClient) {
  return Object.assign(
    render(
      <StrictMode>
        <StaffApp client={client} initialSection="visits" />
      </StrictMode>,
    ),
    { client },
  )
}

async function openTheVisit() {
  const row = await screen.findByRole('button', { name: /Patiente Synthétique/ })
  await userEvent.click(row)
}

describe('Visites — the worklist', () => {
  it('shows who is waiting, with the exact recorded times and no derived waiting time', async () => {
    renderVisits(makeClient())

    expect(await screen.findByRole('heading', { name: 'Visites' })).toBeInTheDocument()
    expect(await screen.findByText('En attente')).toBeInTheDocument()
    expect(screen.getByText('Fièvre depuis deux jours')).toBeInTheDocument()

    // Nothing anywhere claims how long somebody has been waiting, or how urgent they are. A waiting
    // time computed in a browser from two clocks is a number a clinic would act on, and DHealth
    // does not produce operational or clinical signals of its own.
    for (const invented of [/il y a/i, /minutes d'attente/i, /priorit/i, /urgent/i, /retard/i]) {
      expect(screen.queryByText(invented)).not.toBeInTheDocument()
    }
  })

  it('asks the server for only the live states by default', async () => {
    const client = makeClient()
    renderVisits(client)
    await screen.findByText('En attente')

    expect(client.listVisits).toHaveBeenCalledWith(
      PROVIDER_ID,
      expect.objectContaining({ statuses: ['CHECKED_IN', 'IN_PROGRESS'] }),
    )
  })

  it('offers no filter that would mix finished visits into the live list', async () => {
    renderVisits(makeClient())
    await screen.findByText('En attente')
    // A "Tous" view grows without bound and buries the handful of people actually waiting.
    expect(screen.queryByRole('button', { name: /^Tous/ })).not.toBeInTheDocument()
  })
})

describe('Visites — the lifecycle', () => {
  it('offers Annuler while waiting, and never once the visit is in consultation', async () => {
    const inProgress = visit({
      status: 'IN_PROGRESS',
      clinician_identity_id: CLINICIAN_ID,
      started_at: '2026-09-27T08:30:00Z',
    })
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([visit()]),
      getVisit: vi.fn().mockResolvedValue(detail()),
      takeVisit: vi.fn().mockResolvedValue(inProgress),
    })
    renderVisits(client)
    await openTheVisit()

    expect(await screen.findByRole('button', { name: /Annuler la visite/ })).toBeInTheDocument()

    // Taking it removes cancellation from the product, permanently for this visit.
    ;(client.getVisit as ReturnType<typeof vi.fn>).mockResolvedValue(
      detail({ visit: inProgress }),
    )
    await userEvent.click(await screen.findByRole('button', { name: /Prendre en charge/ }))

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /Annuler la visite/ })).not.toBeInTheDocument()
    })
    expect(await screen.findByRole('button', { name: /Terminer la visite/ })).toBeInTheDocument()
  })

  it('names the patient and the consequence before cancelling', async () => {
    renderVisits(makeClient())
    await openTheVisit()

    await userEvent.click(await screen.findByRole('button', { name: /Annuler la visite/ }))

    // Never "Êtes-vous sûr ?". The restatement names the person and says what survives. Matched as
    // one sentence rather than by the patient's name alone, which also appears in the detail
    // heading and in the worklist row behind it.
    const restatement = await screen.findByText(/Annuler la visite de/)
    expect(restatement).toHaveTextContent(/Patiente Synthétique/)
    expect(restatement).toHaveTextContent(/restera visible dans l'historique/)
    expect(restatement).toHaveTextContent(/déjà prise en charge ne peut plus être annulée/)
  })

  it('re-reads the visit when the server refuses a stale transition', async () => {
    // A colleague completed it first. The screen must show what actually happened rather than let
    // the clinician argue with a button.
    const inProgress = visit({
      status: 'IN_PROGRESS',
      clinician_identity_id: CLINICIAN_ID,
      started_at: '2026-09-27T08:30:00Z',
    })
    const getVisit = vi.fn().mockResolvedValue(detail({ visit: inProgress }))
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([inProgress]),
      getVisit,
      completeVisit: vi
        .fn()
        .mockRejectedValue(new ExchangeApiError(409, 'CONFLICT', 'already completed')),
    })
    renderVisits(client)
    await openTheVisit()

    const callsBefore = getVisit.mock.calls.length
    await userEvent.click(await screen.findByRole('button', { name: /Terminer la visite/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/état de la visite a changé/i)
    await waitFor(() => {
      expect(getVisit.mock.calls.length).toBeGreaterThan(callsBefore)
    })
  })
})

describe('Visites — the clinical note', () => {
  const inProgress = visit({
    status: 'IN_PROGRESS',
    clinician_identity_id: CLINICIAN_ID,
    started_at: '2026-09-27T08:30:00Z',
  })

  it('refuses to offer a note field before the visit is taken, and says why', async () => {
    renderVisits(makeClient())
    await openTheVisit()

    expect(
      await screen.findByText(/Prenez la visite en charge pour écrire une note/),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText(/Chaque enregistrement ajoute une révision/)).toBeNull()
  })

  it('opens the editor empty, so an append cannot be mistaken for an edit', async () => {
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([inProgress]),
      getVisit: vi.fn().mockResolvedValue(
        detail({
          visit: inProgress,
          notes: [
            {
              id: 'note-1',
              revision_number: 1,
              author_identity_id: CLINICIAN_ID,
              body: 'Texte déjà consigné.',
              created_at: '2026-09-27T08:35:00Z',
            },
          ],
        }),
      ),
    })
    renderVisits(client)
    await openTheVisit()

    const editor = await screen.findByLabelText(/Chaque enregistrement ajoute une révision/)
    expect(editor).toHaveValue('')
    // The existing text is visible as a revision, not loaded into the editor.
    expect(screen.getByText('Texte déjà consigné.')).toBeInTheDocument()
  })

  it('keeps every revision on screen and marks which one is current', async () => {
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([inProgress]),
      getVisit: vi.fn().mockResolvedValue(
        detail({
          visit: inProgress,
          notes: [
            {
              id: 'note-1',
              revision_number: 1,
              author_identity_id: CLINICIAN_ID,
              body: 'Première rédaction.',
              created_at: '2026-09-27T08:35:00Z',
            },
            {
              id: 'note-2',
              revision_number: 2,
              author_identity_id: OTHER_CLINICIAN_ID,
              body: 'Correction par le médecin couvrant.',
              created_at: '2026-09-27T08:50:00Z',
            },
          ],
        }),
      ),
    })
    renderVisits(client)
    await openTheVisit()

    expect(await screen.findByText('Première rédaction.')).toBeInTheDocument()
    expect(screen.getByText('Correction par le médecin couvrant.')).toBeInTheDocument()
    expect(screen.getByText(/Remplacée par une révision ultérieure/)).toBeInTheDocument()
    expect(screen.getByText('Actuelle')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /révision 2/i })).toBeInTheDocument()
  })

  it('sends the note and re-reads rather than appending it locally', async () => {
    const getVisit = vi.fn().mockResolvedValue(detail({ visit: inProgress }))
    const appendVisitNote = vi.fn().mockResolvedValue({
      id: 'note-1',
      revision_number: 1,
      author_identity_id: CLINICIAN_ID,
      body: 'Examen consigné.',
      created_at: '2026-09-27T08:40:00Z',
    })
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([inProgress]),
      getVisit,
      appendVisitNote,
    })
    renderVisits(client)
    await openTheVisit()

    const editor = await screen.findByLabelText(/Chaque enregistrement ajoute une révision/)
    await userEvent.type(editor, 'Examen consigné.')
    const callsBefore = getVisit.mock.calls.length
    await userEvent.click(screen.getByRole('button', { name: /Enregistrer la note/ }))

    await waitFor(() => {
      expect(appendVisitNote).toHaveBeenCalledWith(PROVIDER_ID, VISIT_ID, {
        body: 'Examen consigné.',
      })
    })
    // The list of revisions is the server's answer, never the browser's guess about what it wrote.
    await waitFor(() => expect(getVisit.mock.calls.length).toBeGreaterThan(callsBefore))
    expect(editor).toHaveValue('')
  })

  it('states plainly that a completed visit’s note is closed, without offering a field', async () => {
    const completed = visit({
      status: 'COMPLETED',
      clinician_identity_id: CLINICIAN_ID,
      started_at: '2026-09-27T08:30:00Z',
      completed_at: '2026-09-27T09:00:00Z',
    })
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([completed]),
      getVisit: vi.fn().mockResolvedValue(detail({ visit: completed })),
    })
    renderVisits(client)
    await openTheVisit()

    expect(await screen.findByText(/la note n'est plus modifiable ici/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Chaque enregistrement ajoute une révision/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Corriger le motif/ })).toBeNull()
  })
})

describe('Visites — ordering during a visit', () => {
  const inProgress = visit({
    status: 'IN_PROGRESS',
    clinician_identity_id: CLINICIAN_ID,
    started_at: '2026-09-27T08:30:00Z',
  })

  it('offers Demander des analyses only while the visit is in consultation', async () => {
    const client = makeClient()
    renderVisits(client)
    await openTheVisit()

    // Waiting: no ordering. There is no clinician of record yet, and the server refuses an intent
    // naming a visit that has not started.
    expect(screen.queryByRole('button', { name: /Demander des analyses/ })).toBeNull()
  })

  it('hands the patient and the visit to Patients, and the intent carries visit_id', async () => {
    // The linkage assertion. If this breaks, orders silently detach from the consultation that
    // produced them -- or worse, attach to the wrong one.
    const catalogEntry = {
      id: '44444444-4444-4444-8444-444444444444',
      code: 'SYNTH-CBC',
      display_name: 'Numération Formule Sanguine (NFS)',
      version: 1,
      active: true,
      is_synthetic: true,
    }
    const createOrderIntentWithOutcome = vi.fn().mockImplementation((_p, key, body) =>
      Promise.resolve({
        intent: {
          id: `intent-${key}`,
          idempotency_key: key,
          provider_organization_id: PROVIDER_ID,
          patient_reference_id: body.patient_reference_id,
          practitioner_reference_id: 'prac-1',
          committed_lab_order_id: null,
          created_at: '2026-09-27T08:45:00Z',
          items: [],
          visit_id: body.visit_id ?? null,
        },
        created: true,
      }),
    )
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([inProgress]),
      getVisit: vi.fn().mockResolvedValue(detail({ visit: inProgress })),
      listCatalogTests: vi.fn().mockResolvedValue([catalogEntry]),
      listPatientOrders: vi.fn().mockResolvedValue([]),
      createOrderIntentWithOutcome,
      commitOrderIntentWithOutcome: vi.fn().mockResolvedValue({
        order: {
          id: 'order-1',
          provider_organization_id: PROVIDER_ID,
          patient_reference_id: PATIENT_REF_ID,
          practitioner_reference_id: 'prac-1',
          patient: {
            patient_id: PATIENT_ID,
            display_name: PATIENT.display_name,
            date_of_birth: PATIENT.date_of_birth,
            contact_channel_type: null,
            contact_value: null,
          },
          status: 'AWAITING_LAB_SELECTION',
          created_at: '2026-09-27T08:45:00Z',
          items: [],
          visit_id: VISIT_ID,
          lab_assignment: null,
        },
        created: true,
      }),
      getEligibleLabs: vi.fn().mockResolvedValue([]),
    })
    renderVisits(client)
    await openTheVisit()

    await userEvent.click(await screen.findByRole('button', { name: /Demander des analyses/ }))

    // The clinician is now in Patients, on this patient, without having searched for them.
    await waitFor(() => expect(client.getPatient).toHaveBeenCalledWith(PROVIDER_ID, PATIENT_ID))
    await userEvent.click(
      await screen.findByRole('button', { name: /Nouvel ordre de laboratoire/ }),
    )
    await userEvent.click(await screen.findByRole('checkbox'))
    await userEvent.click(await screen.findByRole('button', { name: /Suivant : Réviser l’ordre/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Confirmer et prescrire/ }))

    await waitFor(() => {
      expect(createOrderIntentWithOutcome).toHaveBeenCalledWith(
        PROVIDER_ID,
        expect.any(String),
        expect.objectContaining({ visit_id: VISIT_ID }),
      )
    })
  })

  it('lists the visit’s orders by status and never a result value', async () => {
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([inProgress]),
      getVisit: vi.fn().mockResolvedValue(
        detail({
          visit: inProgress,
          orders: [
            {
              id: 'order-1',
              provider_organization_id: PROVIDER_ID,
              patient_reference_id: PATIENT_REF_ID,
              practitioner_reference_id: 'prac-1',
              patient: {
                patient_id: PATIENT_ID,
                display_name: PATIENT.display_name,
                date_of_birth: PATIENT.date_of_birth,
                contact_channel_type: null,
                contact_value: null,
              },
              status: 'AWAITING_LAB_SELECTION',
              created_at: '2026-09-27T08:45:00Z',
              item_count: 2,
              visit_id: VISIT_ID,
              lab_assignment: null,
            },
          ],
        }),
      ),
    })
    renderVisits(client)
    await openTheVisit()

    expect(await screen.findByText(/2 analyses/)).toBeInTheDocument()
    // The French label, never the raw enum. The labeller is shared with the patient chart
    // (`clinic/orderStatusVocabulary`), so the two screens cannot describe one status differently.
    expect(screen.getByText('En attente de choix labo')).toBeInTheDocument()
    // The visit never shows values. Results are opened from the order, through the read paths that
    // already apply ADR-044's entry-is-not-release filtering.
    expect(screen.getByText(/Les résultats se consultent depuis l'ordre/)).toBeInTheDocument()
  })

  it('says completing the visit does not wait for the laboratory', async () => {
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([inProgress]),
      getVisit: vi.fn().mockResolvedValue(detail({ visit: inProgress })),
    })
    renderVisits(client)
    await openTheVisit()

    expect(await screen.findByText(/Terminer la visite n'attend pas le laboratoire/)).toBeInTheDocument()
  })
})

describe('Visites — registering an arrival', () => {
  it('reuses one idempotency key across a retry, so a double attempt cannot create two visits', async () => {
    const startVisitWithOutcome = vi
      .fn()
      .mockRejectedValueOnce(new ExchangeApiError(0, 'NETWORK_ERROR', 'offline'))
      .mockResolvedValueOnce({ visit: visit(), created: true })
    const client = makeClient({ listVisits: vi.fn().mockResolvedValue([]), startVisitWithOutcome })
    renderVisits(client)

    await userEvent.click(await screen.findByRole('button', { name: /Enregistrer une arrivée/ }))
    await userEvent.click(await screen.findByRole('button', { name: /^Rechercher$/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Patiente Synthétique/ }))
    await userEvent.type(
      await screen.findByLabelText(/Motif de la visite, tel qu'énoncé/),
      'Fièvre depuis deux jours',
    )
    await userEvent.click(screen.getByRole('button', { name: /Enregistrer l'arrivée/ }))

    // The failure is reported as uncertain, and the retry is offered rather than a fresh attempt.
    expect(await screen.findByRole('alert')).toHaveTextContent(/sans créer de doublon/)
    await userEvent.click(screen.getByRole('button', { name: /^Réessayer$/ }))

    await waitFor(() => expect(startVisitWithOutcome).toHaveBeenCalledTimes(2))
    const firstKey = startVisitWithOutcome.mock.calls[0][1]
    const secondKey = startVisitWithOutcome.mock.calls[1][1]
    expect(secondKey).toBe(firstKey)
  })

  it('refuses to guess when a key was already used for another patient or reason', async () => {
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([]),
      startVisitWithOutcome: vi
        .fn()
        .mockRejectedValue(new ExchangeApiError(409, 'CONFLICT', 'key reused')),
    })
    renderVisits(client)

    await userEvent.click(await screen.findByRole('button', { name: /Enregistrer une arrivée/ }))
    await userEvent.click(await screen.findByRole('button', { name: /^Rechercher$/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Patiente Synthétique/ }))
    await userEvent.type(
      await screen.findByLabelText(/Motif de la visite, tel qu'énoncé/),
      'Motif différent',
    )
    await userEvent.click(screen.getByRole('button', { name: /Enregistrer l'arrivée/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /déjà été utilisée pour un autre patient ou un autre motif/,
    )
  })

  it('requires a reason before it will send anything', async () => {
    const startVisitWithOutcome = vi.fn()
    const client = makeClient({ listVisits: vi.fn().mockResolvedValue([]), startVisitWithOutcome })
    renderVisits(client)

    await userEvent.click(await screen.findByRole('button', { name: /Enregistrer une arrivée/ }))
    await userEvent.click(await screen.findByRole('button', { name: /^Rechercher$/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Patiente Synthétique/ }))
    await userEvent.click(screen.getByRole('button', { name: /Enregistrer l'arrivée/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Indiquez le motif/)
    expect(startVisitWithOutcome).not.toHaveBeenCalled()
  })
})

describe('Visites — what it never fetches', () => {
  it('does not read the patient directory or the catalogue just to show the worklist', async () => {
    const client = makeClient()
    renderVisits(client)
    await screen.findByText('En attente')

    // The worklist carries the patient's name and date of birth itself, precisely so that rendering
    // it is one request. A per-row patient read would be an N+1 against a clinical endpoint.
    expect(client.getPatient).not.toHaveBeenCalled()
    expect(client.listPatients).not.toHaveBeenCalled()
    expect(client.listCatalogTests).not.toHaveBeenCalled()
  })
})

describe('Visites — a patient with no recorded birthday', () => {
  it('renders the row without inventing a placeholder date', async () => {
    const anonymous = visit({ patient_display_name: null, patient_date_of_birth: null })
    const client = makeClient({
      listVisits: vi.fn().mockResolvedValue([anonymous]),
      getVisit: vi.fn().mockResolvedValue(detail({ visit: anonymous })),
    })
    renderVisits(client)

    const row = await screen.findByRole('button', { name: /Patient sans nom enregistré/ })
    expect(row).toBeInTheDocument()
    expect(within(row).queryByText(/1970|0000|N\/A|null/)).toBeNull()
  })
})
