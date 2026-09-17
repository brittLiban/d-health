import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { PatientIdentitySummary } from '../../../api/exchangeTypes'
import { PatientIdentity } from './PatientIdentity'
import { formatContactLabel, formatDateOfBirth } from './patientIdentityFormat'

function makePatient(overrides: Partial<PatientIdentitySummary> = {}): PatientIdentitySummary {
  return {
    patient_id: '11111111-1111-4111-8111-111111111111',
    display_name: 'Mohamed Ali',
    date_of_birth: '1994-03-14',
    contact_channel_type: 'PHONE',
    contact_value: '+25377123456',
    ...overrides,
  }
}

describe('formatDateOfBirth', () => {
  it('PI3: renders the same calendar day regardless of any timezone -- pure string transform, no Date object', () => {
    // A date near the UTC boundary is exactly the case `new Date('1990-01-01')` gets wrong in a
    // timezone west of UTC (it would render 31 December 1989). This function never constructs a
    // Date at all, so there is no timezone surface for it to drift across.
    expect(formatDateOfBirth('1990-01-01')).toBe('1 janvier 1990')
    expect(formatDateOfBirth('1994-03-14')).toBe('14 mars 1994')
    expect(formatDateOfBirth('2000-12-31')).toBe('31 décembre 2000')
  })

  it('returns null for a missing or malformed value, never a fabricated date', () => {
    expect(formatDateOfBirth(null)).toBeNull()
    expect(formatDateOfBirth(undefined)).toBeNull()
    expect(formatDateOfBirth('')).toBeNull()
    expect(formatDateOfBirth('not-a-date')).toBeNull()
  })
})

describe('formatContactLabel', () => {
  it('labels a known channel type honestly', () => {
    expect(formatContactLabel('PHONE')).toBe('Téléphone')
    expect(formatContactLabel('WHATSAPP')).toBe('WhatsApp')
  })

  it('never assumes PHONE for an unrecognized or missing channel type', () => {
    expect(formatContactLabel('CARRIER_PIGEON')).toBe('CARRIER_PIGEON')
    expect(formatContactLabel(null)).toBe('Contact')
  })
})

describe('PatientIdentity', () => {
  it('PI1: renders the name as the most prominent element', () => {
    render(<PatientIdentity patient={makePatient()} />)
    expect(screen.getByText('Mohamed Ali')).toBeInTheDocument()
  })

  it('PI2/PI4: renders DOB and contact when known', () => {
    render(<PatientIdentity patient={makePatient()} variant="full" />)
    expect(screen.getByText('Né(e) le 14 mars 1994')).toBeInTheDocument()
    expect(screen.getByText('Téléphone : +25377123456')).toBeInTheDocument()
  })

  it('PI5: does not fabricate a DOB when none is known', () => {
    render(<PatientIdentity patient={makePatient({ date_of_birth: null })} />)
    expect(screen.queryByText(/^Né\(e\) le/)).not.toBeInTheDocument()
    expect(screen.queryByText(/janvier/)).not.toBeInTheDocument()
  })

  it('PI6: does not fabricate a contact when none is known', () => {
    render(
      <PatientIdentity
        patient={makePatient({ contact_channel_type: null, contact_value: null })}
      />,
    )
    expect(screen.queryByText(/Téléphone/)).not.toBeInTheDocument()
  })

  it('PI7: a name-only patient gets a truthful, non-alarming limited-identity notice', () => {
    render(
      <PatientIdentity
        patient={makePatient({
          date_of_birth: null,
          contact_channel_type: null,
          contact_value: null,
        })}
      />,
    )
    expect(
      screen.getByText('Identité limitée — vérifiez le patient avant de continuer.'),
    ).toBeInTheDocument()
  })

  it('does not show the limited-identity notice once at least one of DOB/contact is known', () => {
    render(<PatientIdentity patient={makePatient({ contact_channel_type: null, contact_value: null })} />)
    expect(
      screen.queryByText('Identité limitée — vérifiez le patient avant de continuer.'),
    ).not.toBeInTheDocument()
  })

  it('PI8/PI9: the full variant shows the complete, non-truncated technical id, after the name', () => {
    const { container } = render(<PatientIdentity patient={makePatient()} variant="full" />)
    expect(screen.getByText('11111111-1111-4111-8111-111111111111')).toBeInTheDocument()
    const texts = Array.from(container.querySelectorAll('p')).map((el) => el.textContent)
    const nameIndex = texts.findIndex((t) => t === 'Mohamed Ali')
    const idIndex = texts.findIndex((t) => t?.includes('Réf. DHealth'))
    expect(nameIndex).toBeGreaterThanOrEqual(0)
    expect(idIndex).toBeGreaterThan(nameIndex)
  })

  it('the compact variant never renders a technical identifier at all', () => {
    render(<PatientIdentity patient={makePatient()} variant="compact" />)
    expect(screen.queryByText(/Réf\. DHealth/)).not.toBeInTheDocument()
  })

  it('PI10: two patients with byte-identical demographics remain two distinct rendered rows', () => {
    const patientC = makePatient({ patient_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' })
    const patientD = makePatient({ patient_id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd' })
    const { container } = render(
      <>
        <PatientIdentity patient={patientC} />
        <PatientIdentity patient={patientD} />
      </>,
    )
    // Never deduplicated -- two DOM nodes, two distinct patient_id identities, even though every
    // visible human field (name/DOB/contact) is identical (ADR-051's no-fuzzy-matching law).
    const rows = container.querySelectorAll('[data-patient-id]')
    expect(rows).toHaveLength(2)
    expect(rows[0].getAttribute('data-patient-id')).toBe(patientC.patient_id)
    expect(rows[1].getAttribute('data-patient-id')).toBe(patientD.patient_id)
    expect(rows[0].getAttribute('data-patient-id')).not.toBe(rows[1].getAttribute('data-patient-id'))
    expect(screen.getAllByText('Mohamed Ali')).toHaveLength(2)
  })

  it('falls back to a truthful placeholder, never blank space, when the name is unknown', () => {
    render(<PatientIdentity patient={makePatient({ display_name: null })} />)
    expect(screen.getByText('Nom non renseigné')).toBeInTheDocument()
  })
})
