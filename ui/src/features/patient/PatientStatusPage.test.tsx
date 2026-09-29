/**
 * DH-PATIENT-001 §33-§37, PU1-PU12: what the patient is told, and — at least as important — what
 * they are never told.
 *
 * Several of these assertions are negative on purpose. A future well-meaning change that softened
 * "aucun suivi supplémentaire n'a été indiqué" into "tout est normal", or that surfaced a result
 * value "now that the doctor has reviewed it", would be a clinical-interpretation change this
 * repository reserves for a human decision (AGENTS.md; `docs/MVP-1.0.md` §13) — so it fails here
 * rather than shipping.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { LabOrderStatus, PatientOrder, PatientResultStatus } from '../../api/exchangeTypes'
import { PatientStatusPage } from './PatientStatusPage'

function makeOrder(
  status: LabOrderStatus,
  resultStatus: PatientResultStatus = 'NOT_READY',
  overrides: Partial<PatientOrder> = {},
): PatientOrder {
  return {
    status,
    result_status: resultStatus,
    ordering_provider_name: 'Clinique Démo',
    requested_tests: [
      {
        catalog_code_snapshot: 'DHEALTH-DEMO-A',
        catalog_display_name_snapshot: 'Analyse de démonstration A',
      },
    ],
    selection: null,
    ...overrides,
  }
}

const ASSIGNED_LAB: PatientOrder['selection'] = {
  lab_organization_id: '2f1c9a44-0b4e-4f6d-9f6a-3f9b0d0f1c22',
  lab_organization_name: 'Laboratoire Démo DHealth',
  public_address: '12 Avenue de la République',
  latitude: null,
  longitude: null,
  public_phone: '+253 21 00 00 00',
  selected_at: '2026-09-18T09:00:00Z',
  assignment_source: 'PROVIDER',
  items: [],
  total_amount: null,
  currency: null,
}

describe('PatientStatusPage — status wording (§33)', () => {
  it('PU1: SELECTED says a laboratory was assigned, never that anything was sent', () => {
    render(<PatientStatusPage order={makeOrder('SELECTED')} />)

    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Laboratoire attribué')
    // The Exchange records "a lab was chosen"; it records no transmission/receipt fact here.
    expect(document.body.textContent).not.toMatch(/envoyé au laboratoire/i)
    expect(document.body.textContent).not.toMatch(/transmis/i)
  })

  it('PU2: ACCEPTED is truthful', () => {
    render(<PatientStatusPage order={makeOrder('ACCEPTED')} />)
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(
      'Ordre accepté par le laboratoire',
    )
  })

  it('PU3: IN_PROGRESS is truthful and plain', () => {
    render(<PatientStatusPage order={makeOrder('IN_PROGRESS')} />)
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Analyse en cours')
  })

  it('AWAITING_LAB_SELECTION explains the clinic is still organising, with no marketplace control', () => {
    render(<PatientStatusPage order={makeOrder('AWAITING_LAB_SELECTION')} />)

    expect(screen.getByRole('heading', { level: 2 }).textContent).toMatch(
      /Votre clinique prépare l’orientation/i,
    )
    // §6/AC19: no lab choice is ever offered to the patient in this launch flow.
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/choisir un laboratoire|comparer/i)
  })

  it('PU4: COMPLETED + pending review says the result exists and the doctor must still review it', () => {
    render(<PatientStatusPage order={makeOrder('COMPLETED', 'AWAITING_CLINICIAN_REVIEW')} />)

    expect(document.body.textContent).toMatch(/Résultat disponible auprès de votre clinique/i)
    expect(document.body.textContent).toMatch(/Votre médecin doit encore le revoir/i)
  })

  it('PU5: NO_FOLLOW_UP_REQUIRED never becomes "tout est normal"', () => {
    render(<PatientStatusPage order={makeOrder('COMPLETED', 'NO_FOLLOW_UP_REQUIRED')} />)

    expect(document.body.textContent).toMatch(/Votre médecin a revu le résultat/i)
    expect(document.body.textContent).toMatch(
      /Aucun suivi supplémentaire n'a été indiqué pour cet ordre/i,
    )
    // The forbidden reinterpretations, spelled out (§33/§37).
    expect(document.body.textContent).not.toMatch(/tout est normal|normal|rassurant|négatif/i)
  })

  it('PU6: FOLLOW_UP_REQUIRED says contact the clinic, with no diagnosis or urgency', () => {
    render(<PatientStatusPage order={makeOrder('COMPLETED', 'FOLLOW_UP_REQUIRED')} />)

    expect(document.body.textContent).toMatch(/un suivi est nécessaire/i)
    expect(document.body.textContent).toMatch(/Contactez votre clinique/i)
    expect(document.body.textContent).not.toMatch(
      /urgent|urgence|immédiat|grave|anormal|dangereux|traitement/i,
    )
  })

  it('PU7: REJECTED is truthful and actionable', () => {
    render(<PatientStatusPage order={makeOrder('REJECTED')} />)
    expect(document.body.textContent).toMatch(/Le laboratoire n'a pas accepté cet ordre/i)
    expect(document.body.textContent).toMatch(/Contactez votre clinique/i)
  })

  it('PU8: CANCELLED is truthful and actionable', () => {
    render(<PatientStatusPage order={makeOrder('CANCELLED')} />)
    expect(document.body.textContent).toMatch(/Cet ordre a été annulé/i)
    expect(document.body.textContent).toMatch(/Contactez votre clinique/i)
  })

  it('never shows a raw workflow enum or technical vocabulary (§53)', () => {
    for (const status of [
      'AWAITING_LAB_SELECTION',
      'SELECTED',
      'ACCEPTED',
      'IN_PROGRESS',
      'COMPLETED',
      'REJECTED',
      'CANCELLED',
    ] as LabOrderStatus[]) {
      const { unmount } = render(
        <PatientStatusPage order={makeOrder(status, 'AWAITING_CLINICIAN_REVIEW')} />,
      )
      expect(document.body.textContent).not.toContain(status)
      expect(document.body.textContent).not.toMatch(/workflow|statut ?:|UUID/i)
      unmount()
    }
  })
})

describe('PatientStatusPage — content boundaries', () => {
  it('PU9: renders the assigned laboratory’s public contact detail', () => {
    render(<PatientStatusPage order={makeOrder('ACCEPTED', 'NOT_READY', { selection: ASSIGNED_LAB })} />)

    expect(screen.getByText('Laboratoire Démo DHealth')).toBeInTheDocument()
    expect(screen.getByText('12 Avenue de la République')).toBeInTheDocument()
    expect(screen.getByText('+253 21 00 00 00')).toBeInTheDocument()
  })

  it('PU10: shows the human test name, never the catalog code', () => {
    render(<PatientStatusPage order={makeOrder('IN_PROGRESS')} />)

    expect(screen.getByText('Analyse de démonstration A')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('DHEALTH-DEMO-A')
  })

  it('PU11/AC20: renders no price even when the response carries price fields', () => {
    // A deliberately hostile fixture: the DH-EX-005 patient-marketplace receipt shape, fully
    // populated, on the provider-assigned surface. `toPatientLab` must drop all of it.
    const pricedSelection: PatientOrder['selection'] = {
      ...ASSIGNED_LAB,
      assignment_source: 'PATIENT',
      items: [
        {
          catalog_code_snapshot: 'DHEALTH-DEMO-A',
          catalog_display_name_snapshot: 'Analyse de démonstration A',
          selected_price_amount: '4500.00',
          selected_price_currency: 'DJF',
        },
      ],
      total_amount: '4500.00',
      currency: 'DJF',
    }
    render(
      <PatientStatusPage order={makeOrder('ACCEPTED', 'NOT_READY', { selection: pricedSelection })} />,
    )

    expect(document.body.textContent).not.toContain('4500')
    expect(document.body.textContent).not.toContain('DJF')
    expect(document.body.textContent).not.toMatch(/prix|tarif|montant|total/i)
  })

  it('PU12/AC16: shows no result value, range, or interpretation in any status', () => {
    for (const resultStatus of [
      'NOT_READY',
      'AWAITING_CLINICIAN_REVIEW',
      'NO_FOLLOW_UP_REQUIRED',
      'FOLLOW_UP_REQUIRED',
    ] as PatientResultStatus[]) {
      const { unmount } = render(
        <PatientStatusPage order={makeOrder('COMPLETED', resultStatus, { selection: ASSIGNED_LAB })} />,
      )
      expect(document.body.textContent).not.toMatch(
        /g\/dL|mmol|valeur|intervalle de référence|référence\b|résultat ?:|\d+[.,]\d+/i,
      )
      unmount()
    }
  })

  it('§31: exposes no clinician identity or internal identifier', () => {
    render(<PatientStatusPage order={makeOrder('COMPLETED', 'NO_FOLLOW_UP_REQUIRED', { selection: ASSIGNED_LAB })} />)

    // The lab's own UUID is present in the response and must not reach the screen.
    expect(document.body.textContent).not.toContain(ASSIGNED_LAB!.lab_organization_id)
    expect(document.body.innerHTML).not.toContain(ASSIGNED_LAB!.lab_organization_id)
    expect(document.body.textContent).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
    )
  })

  it('§52: status is conveyed by text, not colour alone', () => {
    render(<PatientStatusPage order={makeOrder('COMPLETED', 'FOLLOW_UP_REQUIRED')} />)

    // The live region carries the full sentence; the coloured marker is decorative only.
    const status = screen.getByRole('status')
    expect(status.textContent).toMatch(/un suivi est nécessaire/i)
  })
})

describe('PatientStatusPage — BLOCKED (DHEALTH-LAB-BLOCKED-001 §17)', () => {
  it('says the analysis is waiting, and never claims it is progressing', () => {
    render(<PatientStatusPage order={makeOrder('BLOCKED', 'NOT_READY', { selection: ASSIGNED_LAB })} />)

    // The honest statement. Mapping BLOCKED onto "Analyse en cours" would make this screen claim
    // progress that is not happening — the exact deception the BLOCKED state exists to end.
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(/en attente au laboratoire/i)
    expect(document.body.textContent).not.toMatch(/analyse en cours/i)
    // Not a cancellation or a refusal either: those are different, terminal facts.
    expect(document.body.textContent).not.toMatch(/annulé|refus|rejet/i)
  })

  it('leaks no laboratory technical detail — no reason key, no note, no enum, no actor', () => {
    render(<PatientStatusPage order={makeOrder('BLOCKED', 'NOT_READY', { selection: ASSIGNED_LAB })} />)

    const text = document.body.textContent ?? ''
    const html = document.body.innerHTML
    // The reason key never reaches the patient in any form: not the enum, and not the French
    // operational label the laboratory and the clinic both read.
    for (const leaked of [
      'BLOCKED',
      'SPECIMEN_UNUSABLE',
      'SPECIMEN_INSUFFICIENT',
      'PATIENT_DID_NOT_ATTEND',
      'CLARIFICATION_REQUIRED',
      'Prélèvement inutilisable',
      'Quantité insuffisante',
      'Précision nécessaire',
    ]) {
      expect(text).not.toContain(leaked)
      expect(html).not.toContain(leaked)
    }
    // No technical vocabulary at all (rule 3 of `patientStatusWording`).
    expect(text).not.toMatch(/statut|motif|bloqu|centrifug|analyseur|technicien/i)
  })

  it('gives the patient something they can actually do', () => {
    render(<PatientStatusPage order={makeOrder('BLOCKED')} />)

    // The clinic is the patient's route back in. Telling them the work stopped without telling
    // them who to ask would be worse than saying nothing.
    expect(document.body.textContent).toMatch(/contactez votre clinique/i)
  })

  it('§52: the state is carried by text, not colour alone', () => {
    render(<PatientStatusPage order={makeOrder('BLOCKED')} />)

    expect(screen.getByRole('status').textContent).toMatch(/en attente au laboratoire/i)
  })
})
