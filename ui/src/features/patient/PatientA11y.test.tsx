/**
 * DH-PATIENT-001 §52: accessibility for DHealth Patient specifically.
 *
 * This surface has the least technically-capable audience in the product and the fewest
 * affordances to get it wrong with, so the bar here is not "axe is quiet" — it is that a person
 * using a screen reader, a keyboard, or no colour perception can complete the same task.
 *
 * `jest-axe` cannot evaluate layout, contrast against a real rendering engine, or touch-target
 * size; those are reasoned about in the CSS and stated as unmeasured in the handoff rather than
 * implied here.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import type { PatientOrder } from '../../api/exchangeTypes'
import { PatientApp } from './PatientApp'
import { PatientAccessClient, PatientAccessError } from './patientAccessClient'
import { PatientStatusPage } from './PatientStatusPage'

const TOKEN = 'kR3n9Xq_Tb7LmAz2vWc4dEfGhIjKlMnOpQrStUvWxYz'

const ORDER: PatientOrder = {
  status: 'COMPLETED',
  result_status: 'FOLLOW_UP_REQUIRED',
  ordering_provider_name: 'Clinique Démo',
  requested_tests: [
    {
      catalog_code_snapshot: 'DHEALTH-DEMO-A',
      catalog_display_name_snapshot: 'Analyse de démonstration A',
    },
  ],
  selection: {
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
  },
}

function stubClient(impl: Partial<PatientAccessClient>): PatientAccessClient {
  return impl as PatientAccessClient
}

describe('DHealth Patient accessibility', () => {
  it('the status screen has no axe violations', async () => {
    const { container } = render(<PatientStatusPage order={ORDER} />)
    expect(await axe(container)).toHaveNoViolations()
  })

  it('the invalid-link screen has no axe violations', async () => {
    const { container } = render(
      <PatientApp
        client={stubClient({ getOrder: vi.fn().mockRejectedValue(new PatientAccessError('invalid_access')) })}
        initialHash={`#access=${TOKEN}`}
      />,
    )
    await screen.findByText(/Ce lien n’est plus valide/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it('the rescan screen has no axe violations', async () => {
    const { container } = render(<PatientApp client={stubClient({ getOrder: vi.fn() })} initialHash="" />)
    await screen.findByText(/scannez de nouveau votre QR/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it('uses a meaningful heading structure rather than styled text', () => {
    render(<PatientStatusPage order={ORDER} />)

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Votre analyse')
    // The status itself is a heading, so a screen-reader user can jump straight to it.
    expect(screen.getByRole('heading', { level: 2 }).textContent).toMatch(/suivi est nécessaire/i)
    expect(screen.getAllByRole('heading', { level: 3 }).length).toBeGreaterThan(0)
  })

  it('announces the status through a live region', () => {
    render(<PatientStatusPage order={ORDER} />)

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-live', 'polite')
    expect(status.textContent).toMatch(/suivi est nécessaire/i)
  })

  it('the decorative status marker is hidden from assistive technology', () => {
    const { container } = render(<PatientStatusPage order={ORDER} />)

    const marker = container.querySelector('[aria-hidden="true"]')
    expect(marker).not.toBeNull()
    // If the marker were the only carrier of meaning, hiding it would lose information -- it is
    // not: the same meaning is in the heading text.
    expect(screen.getByRole('status').textContent).toMatch(/suivi est nécessaire/i)
  })

  it('the only interactive control is keyboard reachable and has a real label', async () => {
    render(
      <PatientApp
        client={stubClient({ getOrder: vi.fn().mockRejectedValue(new PatientAccessError('unavailable')) })}
        initialHash={`#access=${TOKEN}`}
      />,
    )
    const retry = await screen.findByRole('button', { name: /Réessayer/i })

    await userEvent.tab()
    expect(retry).toHaveFocus()
    // No icon-only critical control: the accessible name is real words.
    expect(retry.textContent?.trim().length).toBeGreaterThan(3)
  })

  it('the error state is announced, not only rendered', async () => {
    render(
      <PatientApp
        client={stubClient({ getOrder: vi.fn().mockRejectedValue(new PatientAccessError('invalid_access')) })}
        initialHash={`#access=${TOKEN}`}
      />,
    )
    const region = await screen.findByRole('status')
    expect(region).toHaveAttribute('aria-live', 'polite')
    expect(region.textContent).toMatch(/Ce lien n’est plus valide/i)
  })
})
