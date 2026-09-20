/**
 * DH-MVP-POLISH-001 — RL1–RL8, the relay.
 *
 * These are about truthfulness, not layout. The relay's job is to show what was
 * recorded, dated where a date exists, attributed to the establishment that
 * recorded it — and to keep apart the pairs of facts the product must never
 * conflate.
 */

import { render, screen, within } from '@testing-library/react'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import { providerRelayNodes, type RelayReleaseVersion } from './relayNodes'
import { WorkflowRelay, formatRelayMoment, type RelayNode } from './WorkflowRelay'

const CREATED_AT = '2026-09-18T07:05:00Z'
const SELECTED_AT = '2026-09-18T07:20:00Z'
const RELEASED_AT = '2026-09-18T11:00:00Z'
const REVIEWED_AT = '2026-09-18T12:30:00Z'

function order(overrides: Partial<Parameters<typeof providerRelayNodes>[0]['order']> = {}) {
  return {
    status: 'IN_PROGRESS' as const,
    created_at: CREATED_AT,
    lab_assignment: {
      lab_organization_id: 'lab-a',
      lab_organization_name: 'Laboratoire Central',
      selected_at: SELECTED_AT,
    },
    ...overrides,
  }
}

function released(overrides: Partial<RelayReleaseVersion> = {}): RelayReleaseVersion {
  return {
    release_version_number: 1,
    released_at: RELEASED_AT,
    review_status: 'PENDING',
    reviewed_at: null,
    ...overrides,
  }
}

function nodesFor(input: Partial<Parameters<typeof providerRelayNodes>[0]> = {}) {
  return providerRelayNodes({
    order: order(),
    providerOrganizationName: 'Clinique de la Paix',
    ...input,
  })
}

function byId(nodes: RelayNode[], id: string) {
  const node = nodes.find((n) => n.id === id)
  if (!node) throw new Error('no relay node with id ' + id + ' in: ' + nodes.map((n) => n.id).join(','))
  return node
}

describe('the relay, derived from real order state', () => {
  it('RL1: dates the facts the API dates, and attributes each to an establishment', () => {
    const nodes = nodesFor()

    expect(byId(nodes, 'created')).toMatchObject({
      at: CREATED_AT,
      organizationName: 'Clinique de la Paix',
      state: 'done',
    })
    expect(byId(nodes, 'assigned')).toMatchObject({
      at: SELECTED_AT,
      organizationName: 'Laboratoire Central',
      state: 'done',
    })
  })

  it('RL2: keeps "retenu" and "accepté" as two separate facts', () => {
    // Choosing a laboratory and that laboratory agreeing to the work are
    // different events. A relay that merged them would tell a clinician the
    // laboratory had accepted when it had only been selected.
    const selectedOnly = nodesFor({ order: order({ status: 'SELECTED' }) })

    expect(byId(selectedOnly, 'assigned').state).toBe('done')
    expect(byId(selectedOnly, 'accepted').state).toBe('awaiting')
    expect(byId(selectedOnly, 'accepted').label).toMatch(/attendue/i)
  })

  it('RL3: never invents a timestamp for a fact the API does not date', () => {
    // The provider-side response dates creation and selection, then gives a bare
    // `status`. Acceptance is therefore recorded-but-undated; borrowing
    // `selected_at` would date the laboratory's agreement to the moment it was
    // merely chosen, and using "now" would be worse.
    const accepted = byId(nodesFor({ order: order({ status: 'ACCEPTED' }) }), 'accepted')

    expect(accepted.state).toBe('done')
    expect(accepted.at).toBeNull()
  })

  it('RL4: keeps "en analyse" and "libéré" apart — work under way is not a released result', () => {
    // The distinction from the clinician's side: an order the laboratory is still
    // working on has nothing released, however far along the entry may be in the
    // laboratory's own screens.
    const inProgress = nodesFor({ order: order({ status: 'IN_PROGRESS' }), releaseVersions: [] })

    expect(byId(inProgress, 'analysis').state).toBe('current')
    expect(byId(inProgress, 'released').state).toBe('awaiting')
    expect(byId(inProgress, 'released').label).toMatch(/Libération .* attendue/i)
  })

  it('RL4b: treats COMPLETED as released, because release is the only way to reach it', () => {
    // `release_lab_order_results` is the sole supported transition from
    // IN_PROGRESS to COMPLETED, so claiming release was still awaited here would be
    // false. With no version list loaded there is no timestamp and no review
    // status, so the release is stated undated and review is not claimed at all —
    // an absent node is honest where a guessed one would not be.
    const completed = nodesFor({ order: order({ status: 'COMPLETED' }), releaseVersions: [] })

    expect(byId(completed, 'released')).toMatchObject({ state: 'done', at: null })
    expect(completed.map((n) => n.id)).not.toContain('reviewed')
  })

  it('RL5: keeps "libéré" and "revu" apart, and leaves the review owed', () => {
    const nodes = nodesFor({
      order: order({ status: 'COMPLETED' }),
      releaseVersions: [released({ review_status: 'PENDING' })],
    })

    expect(byId(nodes, 'released')).toMatchObject({ at: RELEASED_AT, state: 'done' })
    expect(byId(nodes, 'reviewed').state).toBe('awaiting')

    const reviewed = nodesFor({
      order: order({ status: 'COMPLETED' }),
      releaseVersions: [released({ review_status: 'NO_FOLLOW_UP_REQUIRED', reviewed_at: REVIEWED_AT })],
    })
    expect(byId(reviewed, 'reviewed')).toMatchObject({ at: REVIEWED_AT, state: 'done' })
  })

  it('RL6: a stopped order shows no later step as pending', () => {
    // An order the laboratory refused has no analysis owed by anyone. Listing
    // "Analyse en attente" beneath it would tell the clinician to wait for
    // something that is never coming.
    const rejected = nodesFor({ order: order({ status: 'REJECTED' }) })

    expect(byId(rejected, 'accepted').state).toBe('stopped')
    expect(rejected.map((n) => n.id)).not.toContain('analysis')
    expect(rejected.map((n) => n.id)).not.toContain('released')

    const cancelled = nodesFor({ order: order({ status: 'CANCELLED' }) })
    expect(byId(cancelled, 'cancelled').state).toBe('stopped')
    expect(cancelled.map((n) => n.id)).not.toContain('released')
  })

  it('RL7: never names a person, anywhere, for any status', () => {
    // §14/AC20. The backend models release and review authority as organization
    // roles; "Ahmed holds the release" would be an invention. The node type has no
    // field for a person at all, and this asserts the built nodes match.
    const statuses = [
      'AWAITING_LAB_SELECTION',
      'SELECTED',
      'ACCEPTED',
      'IN_PROGRESS',
      'COMPLETED',
      'REJECTED',
      'CANCELLED',
    ] as const

    for (const status of statuses) {
      for (const versions of [[], [released()], [released({ review_status: 'FOLLOW_UP_REQUIRED', reviewed_at: REVIEWED_AT })]]) {
        const nodes = nodesFor({
          order: order({ status, lab_assignment: status === 'AWAITING_LAB_SELECTION' ? null : order().lab_assignment }),
          releaseVersions: versions,
        })
        for (const node of nodes) {
          expect(Object.keys(node)).not.toContain('personName')
          // Every attribution is an establishment from the order, or nothing.
          if (node.organizationName) {
            expect(['Clinique de la Paix', 'Laboratoire Central']).toContain(node.organizationName)
          }
        }
      }
    }
  })

  it('RL8: an amendment is a release, with the version as a technical reference', () => {
    // The clinician-facing word is "Rectificatif"; the version identity is kept,
    // but as a precise reference rather than in the headline.
    const nodes = nodesFor({
      order: order({ status: 'COMPLETED' }),
      releaseVersions: [released(), released({ release_version_number: 2, review_status: 'PENDING' })],
    })

    const release = byId(nodes, 'released')
    expect(release.label).toBe('Rectificatif libéré')
    expect(release.detail).toBe('Version 2')
    // And the new release reopens the review obligation.
    expect(byId(nodes, 'reviewed').state).toBe('awaiting')
  })

  it('reports no laboratory chosen yet without inventing one', () => {
    const nodes = nodesFor({ order: order({ status: 'AWAITING_LAB_SELECTION', lab_assignment: null }) })

    expect(byId(nodes, 'assigned')).toMatchObject({ state: 'awaiting', at: null })
    expect(byId(nodes, 'assigned').organizationName ?? null).toBeNull()
  })
})

describe('the relay, rendered', () => {
  it('renders each fact as a distinct list item with its state as a word', () => {
    render(
      <WorkflowRelay
        label="Étapes de l'ordre"
        nodes={nodesFor({
          order: order({ status: 'COMPLETED' }),
          releaseVersions: [released()],
        })}
      />,
    )

    const relay = screen.getByRole('list', { name: "Étapes de l'ordre" })
    const items = within(relay).getAllByRole('listitem')
    expect(items.length).toBeGreaterThanOrEqual(5)

    // State is never carried by colour alone.
    expect(within(relay).getAllByText('Effectué').length).toBeGreaterThan(0)
    expect(within(relay).getByText('En attente')).toBeInTheDocument()
  })

  it('says a step is undated rather than showing a borrowed or invented time', () => {
    render(
      <WorkflowRelay
        label="Étapes"
        nodes={nodesFor({ order: order({ status: 'ACCEPTED' }) })}
      />,
    )

    expect(screen.getByText('Date non enregistrée pour cette étape')).toBeInTheDocument()
  })

  it('renders a machine-readable time for every dated step', () => {
    render(<WorkflowRelay label="Étapes" nodes={nodesFor()} />)

    const times = document.querySelectorAll('time[datetime]')
    expect(times.length).toBeGreaterThanOrEqual(2)
    expect(times[0].getAttribute('datetime')).toBe(CREATED_AT)
  })

  it('renders nothing at all when there are no facts to show', () => {
    const { container } = render(<WorkflowRelay label="Étapes" nodes={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('has no accessibility violations', async () => {
    const { container } = render(
      <WorkflowRelay
        label="Étapes de l'ordre"
        nodes={nodesFor({ order: order({ status: 'COMPLETED' }), releaseVersions: [released()] })}
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe('formatRelayMoment', () => {
  it('returns null rather than a placeholder for a missing or unparseable value', () => {
    // A relay node with no date must render no date. "Invalid Date" on a clinical
    // timeline is worse than an honest absence.
    expect(formatRelayMoment(null)).toBeNull()
    expect(formatRelayMoment(undefined)).toBeNull()
    expect(formatRelayMoment('')).toBeNull()
    expect(formatRelayMoment('not a date')).toBeNull()
  })

  it('formats a real timestamp with both date and time', () => {
    const formatted = formatRelayMoment(CREATED_AT)
    expect(formatted).not.toBeNull()
    expect(formatted).toMatch(/2026/)
    expect(formatted).toMatch(/\d{2}:\d{2}/)
  })
})
