/**
 * DHEALTH-LAB-REPORT-001/ADR-072 — the printable released report document.
 *
 * What is provable here and what is not: jsdom does not apply print media rules, evaluate the
 * cascade or paginate, so no assertion in this file says anything about what a printer emits. The
 * print *mechanism* is guarded structurally in `releasedReportPrintStyles.test.ts`, and the print
 * *rendering* is qualified in a real Chromium in `ui/e2e/native/16-lab-report.spec.ts`. What these
 * tests cover is the document's content and behaviour: that version identity is unmissable, that the
 * currentness statement is derived and never alarming, that no forbidden field or invented clinical
 * meaning reaches the page, and that the body class the print rules depend on is added and removed.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import type { ReleasedReport } from '../../api/exchangeTypes'
import { ReleasedReportDocument } from './ReleasedReportDocument'

const BASE_REPORT: ReleasedReport = {
  order_id: '11111111-1111-4111-8111-111111111111',
  release_version_id: '22222222-2222-4222-8222-222222222222',
  release_version_number: 1,
  is_amendment: false,
  released_at: '2026-09-23T14:00:00Z',
  is_current_version: true,
  current_release_version_number: 1,
  laboratory: {
    organization_id: '33333333-3333-4333-8333-333333333333',
    name: 'Laboratoire de démonstration',
  },
  ordering_organization: {
    organization_id: '44444444-4444-4444-8444-444444444444',
    name: 'Clinique de démonstration',
  },
  patient: {
    patient_id: '55555555-5555-4555-8555-555555555555',
    display_name: 'Patient Synthétique A',
    date_of_birth: '1990-04-17',
  },
  results: [
    {
      item_id: '66666666-6666-4666-8666-666666666666',
      catalog_code_snapshot: 'DEMO-A',
      catalog_display_name_snapshot: 'Analyse de démonstration A',
      observations: [
        {
          observation_id: '77777777-7777-4777-8777-777777777777',
          display_label_snapshot: 'Mesure numérique A',
          result_type: 'NUMERIC',
          numeric_value: '4.2',
          numeric_unit: 'mg/dL',
          reference_range_low: '3.5',
          reference_range_high: '5.5',
          text_value: null,
        },
        {
          observation_id: '88888888-8888-4888-8888-888888888888',
          display_label_snapshot: 'Mesure textuelle B',
          result_type: 'TEXT',
          numeric_value: null,
          numeric_unit: null,
          reference_range_low: null,
          reference_range_high: null,
          text_value: 'Aspect limpide',
        },
      ],
    },
  ],
}

function report(overrides: Partial<ReleasedReport> = {}): ReleasedReport {
  return { ...BASE_REPORT, ...overrides }
}

describe('ReleasedReportDocument — identity and content', () => {
  it('states the laboratory, the document kind and the version before anything else', () => {
    render(<ReleasedReportDocument report={report()} onClose={() => {}} />)

    expect(
      screen.getByRole('heading', { level: 1, name: 'Laboratoire de démonstration' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Compte rendu d’analyses')).toBeInTheDocument()
    expect(screen.getByText('Version 1')).toBeInTheDocument()
  })

  it('renders the patient, the ordering clinic and the order reference', () => {
    render(<ReleasedReportDocument report={report()} onClose={() => {}} />)

    expect(screen.getByText('Patient Synthétique A')).toBeInTheDocument()
    expect(screen.getByText('Clinique de démonstration')).toBeInTheDocument()
    expect(screen.getByText(BASE_REPORT.order_id)).toBeInTheDocument()
  })

  it('renders every released observation, its unit and the range the laboratory released', () => {
    render(<ReleasedReportDocument report={report()} onClose={() => {}} />)

    const numericRow = screen.getByRole('row', { name: /Mesure numérique A/ })
    expect(within(numericRow).getByText('4.2')).toBeInTheDocument()
    expect(within(numericRow).getByText('mg/dL')).toBeInTheDocument()
    expect(within(numericRow).getByText('3.5 – 5.5')).toBeInTheDocument()

    const textRow = screen.getByRole('row', { name: /Mesure textuelle B/ })
    expect(within(textRow).getByText('Aspect limpide')).toBeInTheDocument()
  })

  it('derives no clinical meaning from a value, in or out of its range', () => {
    // 9.9 is outside the released 3.5–5.5 range. A product that interpreted results would say
    // something about that here. This one must not.
    const out = report({
      results: [
        {
          ...BASE_REPORT.results[0],
          observations: [{ ...BASE_REPORT.results[0].observations[0], numeric_value: '9.9' }],
        },
      ],
    })
    render(<ReleasedReportDocument report={out} onClose={() => {}} />)

    // Nowhere in the document: a word that could only be a verdict about the value.
    const whole = (document.body.textContent ?? '').toLowerCase()
    for (const word of ['anormal', 'élevé', 'critique', 'urgent', 'recommand', 'diagnostic']) {
      expect(whole).not.toContain(word)
    }

    // Scoped to the results themselves for 'normal' and 'interprét'. The footer carries a standing
    // disclaimer that DHealth produces no clinical interpretation -- which is the opposite of
    // interpreting, and must not be what makes this test pass or fail.
    const table = (screen.getByRole('table').textContent ?? '').toLowerCase()
    for (const word of ['normal', 'interprét', 'référence :']) {
      expect(table).not.toContain(word)
    }
    expect(
      screen.getByText(/aucune interprétation clinique n’est produite/i),
    ).toBeInTheDocument()

    // And the value and its range are rendered, so the assertions above are not passing because
    // the document is empty.
    expect(screen.getByText('9.9')).toBeInTheDocument()
    expect(screen.getByText('3.5 – 5.5')).toBeInTheDocument()
  })

  it('claims no signature, seal, certification or accreditation', () => {
    const { container } = render(<ReleasedReportDocument report={report()} onClose={() => {}} />)
    const text = (container.ownerDocument.body.textContent ?? '').toLowerCase()

    for (const word of ['signature', 'signé', 'certifi', 'accrédit', 'cachet', 'agréé', 'visa']) {
      expect(text).not.toContain(word)
    }
    // What it does say instead: who released it, which is the laboratory.
    expect(screen.getByText('Publié par')).toBeInTheDocument()
  })
})

describe('ReleasedReportDocument — version currentness is derived, never alarming', () => {
  it('names the current released version as the current result', () => {
    render(<ReleasedReportDocument report={report()} onClose={() => {}} />)
    expect(screen.getByText('Résultat actuel')).toBeInTheDocument()
  })

  it('marks a historical version as replaced and names the version that replaced it', () => {
    render(
      <ReleasedReportDocument
        report={report({ is_current_version: false, current_release_version_number: 2 })}
        onClose={() => {}}
      />,
    )
    expect(
      screen.getByText('Version remplacée — résultat actuel : version 2'),
    ).toBeInTheDocument()
    // Still Version 1's own identity, and still Version 1's values.
    expect(screen.getByText('Version 1')).toBeInTheDocument()
    expect(screen.getByText('4.2')).toBeInTheDocument()
  })

  it('never calls a superseded version invalid, cancelled or revoked', () => {
    const { container } = render(
      <ReleasedReportDocument
        report={report({ is_current_version: false, current_release_version_number: 2 })}
        onClose={() => {}}
      />,
    )
    const text = (container.ownerDocument.body.textContent ?? '').toLowerCase()
    for (const word of ['invalide', 'annulé', 'révoqué', 'erreur', 'périmé']) {
      expect(text).not.toContain(word)
    }
  })

  it('qualifies an amendment version with the clinician-side word, keeping the number', () => {
    render(
      <ReleasedReportDocument
        report={report({
          release_version_number: 2,
          is_amendment: true,
          current_release_version_number: 2,
        })}
        onClose={() => {}}
      />,
    )
    expect(screen.getAllByText('Version 2 — rectificatif').length).toBeGreaterThan(0)
  })
})

describe('ReleasedReportDocument — print and navigation', () => {
  it('prints on request, and the action is a reachable button with a clear name', async () => {
    const onPrint = vi.fn()
    render(<ReleasedReportDocument report={report()} onClose={() => {}} onPrint={onPrint} />)

    const print = screen.getByRole('button', { name: 'Imprimer le rapport' })
    await userEvent.click(print)
    expect(onPrint).toHaveBeenCalledTimes(1)
  })

  it('offers a way back, and Escape does the same thing', async () => {
    const onClose = vi.fn()
    render(<ReleasedReportDocument report={report()} onClose={onClose} />)

    await userEvent.click(screen.getByRole('button', { name: '← Retour' }))
    expect(onClose).toHaveBeenCalledTimes(1)

    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('focuses the way out, so a keyboard user is not stranded on a full-surface view', () => {
    render(<ReleasedReportDocument report={report()} onClose={() => {}} />)
    expect(screen.getByRole('button', { name: '← Retour' })).toHaveFocus()
  })

  it('marks the action bar so the print rules can remove it', () => {
    render(<ReleasedReportDocument report={report()} onClose={() => {}} />)
    const bar = document.querySelector('[data-print-hide]')
    expect(bar).not.toBeNull()
    expect(within(bar as HTMLElement).getByRole('button', { name: '← Retour' })).toBeInTheDocument()
    expect(
      within(bar as HTMLElement).getByRole('button', { name: 'Imprimer le rapport' }),
    ).toBeInTheDocument()
  })
})

describe('ReleasedReportDocument — the print mechanism it owns', () => {
  it('renders outside #root, which is what makes printing exclude the application', () => {
    // The portal target is `document.body`. A render inside `#root` would be printed *with* the
    // staff shell, which is the one thing the print rules cannot then undo.
    const root = document.createElement('div')
    root.id = 'root'
    document.body.appendChild(root)
    try {
      render(<ReleasedReportDocument report={report()} onClose={() => {}} />, { container: root })
      const article = document.querySelector('[data-released-report]')
      expect(article).not.toBeNull()
      expect(root.contains(article)).toBe(false)
      expect(document.body.contains(article)).toBe(true)
    } finally {
      root.remove()
    }
  })

  it('adds the body class the print rules key on, and removes it again on unmount', () => {
    expect(document.body.classList.contains('dh-report-open')).toBe(false)
    const view = render(<ReleasedReportDocument report={report()} onClose={() => {}} />)
    expect(document.body.classList.contains('dh-report-open')).toBe(true)
    view.unmount()
    // Not merely tidiness: leaving the class set would make every later ordinary print in the
    // application emit a blank page.
    expect(document.body.classList.contains('dh-report-open')).toBe(false)
  })
})

describe('ReleasedReportDocument — accessibility', () => {
  it('has no axe violations in its default rendering', async () => {
    const { container } = render(<ReleasedReportDocument report={report()} onClose={() => {}} />)
    // The document is portalled to `document.body`, so the scope is the body, not `container`.
    // Exact scope, one rendering, axe's default rule set — not a claim about human usability, which
    // remains UNQUALIFIED.
    const results = await axe(container.ownerDocument.body)
    expect(results).toHaveNoViolations()
  })

  it('represents the results as a table with row and column headers', () => {
    render(<ReleasedReportDocument report={report()} onClose={() => {}} />)
    const table = screen.getByRole('table')
    expect(
      within(table).getByRole('columnheader', { name: 'Intervalle de référence' }),
    ).toBeInTheDocument()
    expect(
      within(table).getByRole('rowheader', { name: 'Mesure numérique A' }),
    ).toBeInTheDocument()
  })

  it('carries a long report without a fixed-height container that could hide rows', () => {
    const many = report({
      results: [
        {
          ...BASE_REPORT.results[0],
          observations: Array.from({ length: 40 }, (_, index) => ({
            observation_id: `obs-${index}`,
            display_label_snapshot: `Mesure ${index + 1}`,
            result_type: 'NUMERIC' as const,
            numeric_value: String(index + 1),
            numeric_unit: 'mg/dL',
            reference_range_low: null,
            reference_range_high: null,
            text_value: null,
          })),
        },
      ],
    })
    render(<ReleasedReportDocument report={many} onClose={() => {}} />)

    // Every row is in the document -- no virtualization, no slice, no scroll container.
    expect(screen.getByRole('rowheader', { name: 'Mesure 1' })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'Mesure 40' })).toBeInTheDocument()
    expect(screen.getAllByRole('rowheader')).toHaveLength(40)
  })
})
