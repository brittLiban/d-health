/**
 * DH-MVP-POLISH-001 — OS1–OS6, changing establishment safely.
 *
 * The hazard: every page here holds organization-scoped state and
 * organization-scoped requests in flight. If a person switches establishment
 * while one is outstanding, the answer to the old question must never render as
 * though it answered the new one. A list of Clinique A's orders displayed under
 * Clinique B's name is a clinical misstatement.
 *
 * ── Why OS1 asserts DOM node identity ──
 *
 * The pages already carry their own `isMounted` and generation guards, earned
 * over earlier tickets. That makes the obvious test useless: delete the `key`
 * from `StaffWorkspace` and a "stale rows must not render" assertion still
 * passes, because the page's own guard catches it. Such a test would prove the
 * guards work and say nothing about the mechanism this ticket added.
 *
 * So OS1 asserts the mechanism directly. With the key, switching establishment
 * unmounts the subtree, so a node captured beforehand is detached from the
 * document. Without the key, React reconciles the same tree in place and that
 * node stays attached. It fails if the key is removed, which is the point.
 */

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../api/exchangeClient'
import {
  labMembership,
  meWith,
  providerMembership,
  workspaceClient,
} from '../../test/renderWithWorkspace'
import { WorkspaceProvider } from '../workspace/WorkspaceProvider'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { StaffWorkspace } from './StaffWorkspace'

const LAB_A = labMembership('lab-a', 'Laboratoire Central')
const LAB_B = labMembership('lab-b', 'Laboratoire Nord')

function labOrder(orderId: string, patientName: string) {
  return {
    order_id: orderId,
    status: 'SELECTED' as const,
    selected_at: '2026-09-18T08:00:00Z',
    updated_at: '2026-09-18T08:00:00Z',
    patient_display_name: patientName,
    patient: { patient_id: 'p-' + orderId, display_name: patientName, date_of_birth: '1990-04-12' },
    ordering_provider_name: 'Clinique de la Paix',
    requested_tests: [
      {
        catalog_code_snapshot: 'GLU',
        catalog_display_name_snapshot: 'Glycémie',
        selected_price_amount: null,
        selected_price_currency: null,
      },
    ],
    collection_recorded_at: null,
  }
}

/** Reports the workspace's switch counter so a test can assert it directly. */
function GenerationProbe() {
  const { generation } = useWorkspace()
  return <span data-testid="generation">{String(generation)}</span>
}

function renderTwoLabs(client: ExchangeClient) {
  return render(
    <WorkspaceProvider client={client}>
      <GenerationProbe />
      <StaffWorkspace initialSection="orders" />
    </WorkspaceProvider>,
  )
}

async function switchTo(name: string) {
  const select = await screen.findByLabelText('Établissement actif')
  await userEvent.setup().selectOptions(select, name)
}

describe('changing establishment', () => {
  it('OS1: unmounts the previous content subtree rather than reconciling it in place', async () => {
    const client = workspaceClient(meWith([LAB_A, LAB_B]), {
      listLabOrders: vi.fn().mockResolvedValue([labOrder('order-a', 'Amina Hassan')]),
    })
    renderTwoLabs(client)

    // The probe is the page *heading*, not a data row, and that choice is the
    // whole point. A row disappears on a switch either way, because the page's own
    // effect clears its rows when the active laboratory changes — asserting on one
    // would prove the page's guard works and say nothing about the remount. The
    // heading is rendered identically before and after, so React reconciles that
    // same DOM node in place unless the subtree is genuinely thrown away.
    const headingBefore = await screen.findByRole('heading', { name: 'Ordres de laboratoire' })
    await screen.findByText('Amina Hassan')

    await switchTo('lab-b')

    // Detached: the subtree was discarded, which is what runs every page's
    // existing cleanup and drops all of its scoped state.
    await waitFor(() => expect(headingBefore).not.toBeInTheDocument())

    // And a fresh instance stands in its place, rather than the page simply being gone.
    const headingAfter = screen.getByRole('heading', { name: 'Ordres de laboratoire' })
    expect(headingAfter).not.toBe(headingBefore)
  })

  it('OS2: a response from the previous establishment never renders under the new one', async () => {
    let resolveFirst: ((value: unknown) => void) | undefined
    const listLabOrders = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve
          }),
      )
      .mockResolvedValue([labOrder('order-b', 'Warsame Ali')])

    const client = workspaceClient(meWith([LAB_A, LAB_B]), { listLabOrders })
    renderTwoLabs(client)

    await screen.findByLabelText('Établissement actif')
    await switchTo('lab-b')

    // Laboratoire Central's request now comes back, after the switch.
    await act(async () => {
      resolveFirst?.([labOrder('order-a', 'Amina Hassan')])
      await Promise.resolve()
    })

    expect(await screen.findByText('Warsame Ali')).toBeInTheDocument()
    expect(screen.queryByText('Amina Hassan')).not.toBeInTheDocument()
  })

  it('OS3: names the new establishment immediately, not once its data arrives', async () => {
    // The identity band must never lag behind the switch: a person reading
    // "Laboratoire Central" while looking at Laboratoire Nord's queue is exactly
    // the confusion this design is meant to prevent.
    let resolveSecond: ((value: unknown) => void) | undefined
    const listLabOrders = vi
      .fn()
      .mockResolvedValueOnce([labOrder('order-a', 'Amina Hassan')])
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecond = resolve
          }),
      )

    const client = workspaceClient(meWith([LAB_A, LAB_B]), { listLabOrders })
    renderTwoLabs(client)

    await screen.findByText('Amina Hassan')
    await switchTo('lab-b')

    // `selector` excludes the switcher's own <option> elements, which live in the
    // masthead too and legitimately name every establishment.
    const masthead = within(screen.getByRole('banner', { name: 'Session et établissement actif' }))
    expect(masthead.getByText('Laboratoire Nord', { selector: 'span' })).toBeInTheDocument()
    expect(
      masthead.queryByText('Laboratoire Central', { selector: 'span' }),
    ).not.toBeInTheDocument()

    await act(async () => {
      resolveSecond?.([])
      await Promise.resolve()
    })
  })

  it('OS4: counts each switch exactly once', async () => {
    // React may call a state updater more than once for the same change, so the
    // counter is bumped outside the updater. A double count here would mean any
    // future consumer of `generation` invalidates twice per switch.
    const client = workspaceClient(meWith([LAB_A, LAB_B]), {
      listLabOrders: vi.fn().mockResolvedValue([]),
    })
    renderTwoLabs(client)

    await screen.findByLabelText('Établissement actif')
    expect(screen.getByTestId('generation')).toHaveTextContent('0')

    await switchTo('lab-b')
    await waitFor(() => expect(screen.getByTestId('generation')).toHaveTextContent('1'))

    await switchTo('lab-a')
    await waitFor(() => expect(screen.getByTestId('generation')).toHaveTextContent('2'))
  })

  it('OS5: re-selecting the establishment already active changes nothing', async () => {
    const client = workspaceClient(meWith([LAB_A, LAB_B]), {
      listLabOrders: vi.fn().mockResolvedValue([]),
    })
    renderTwoLabs(client)

    await screen.findByLabelText('Établissement actif')
    await switchTo('lab-a')

    // No remount, no invalidation, no counted switch: nothing changed.
    expect(screen.getByTestId('generation')).toHaveTextContent('0')
  })

  it('OS6: a section the new establishment does not have is left behind, not rendered against it', async () => {
    // A clinician standing in Patients switches to a laboratory, which has no
    // Patients section. The old section id must not survive, or a provider page
    // would render against a laboratory organization.
    const client = workspaceClient(
      meWith([providerMembership('prov-a', 'Clinique de la Paix'), LAB_A]),
      { listLabOrders: vi.fn().mockResolvedValue([]), listPatients: vi.fn().mockResolvedValue([]) },
    )
    render(
      <WorkspaceProvider client={client}>
        <StaffWorkspace initialSection="patients" />
      </WorkspaceProvider>,
    )

    const nav = within(await screen.findByRole('navigation', { name: 'Navigation principale' }))
    expect(nav.getByRole('button', { name: 'Patients' })).toHaveAttribute('aria-current', 'page')

    await switchTo('lab-a')

    const navAfter = within(screen.getByRole('navigation', { name: 'Navigation principale' }))
    expect(navAfter.queryByRole('button', { name: 'Patients' })).not.toBeInTheDocument()
    await waitFor(() =>
      expect(navAfter.getByRole('button', { name: "Aujourd'hui" })).toHaveAttribute(
        'aria-current',
        'page',
      ),
    )
    // And the provider surface is not on screen under the laboratory.
    expect(screen.queryByLabelText('Rechercher un patient')).not.toBeInTheDocument()
  })
})
