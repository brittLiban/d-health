/**
 * DH-MVP-POLISH-001 — building relay nodes from real order state.
 *
 * `WorkflowRelay` renders; this decides what is true. Keeping the two apart is
 * what makes the honesty testable: these are pure functions over the fields the
 * API actually returns, so a test can state "the API gives no timestamp for
 * acceptance, therefore the node is undated" without rendering anything.
 *
 * ── The rule these functions follow ──
 *
 * A fact is claimed only where a field carries it. The provider-side order
 * response is deliberately thin: it dates creation and lab selection, and then
 * gives a `status` with no timestamps behind it. So acceptance and analysis are
 * reported as recorded-but-undated rather than given a date borrowed from the
 * order, and never given "now".
 *
 * Nothing here names a person. `LabOrder` carries a `practitioner_reference_id`,
 * and it is deliberately not used: it identifies whoever created the order, not
 * whoever owes the next step, and rendering it beside a pending obligation would
 * assert something the data does not say.
 */

import type { LabOrder, LabOrderStatus, LabOrderSummary } from '../../api/exchangeTypes'
import type { RelayNode } from './WorkflowRelay'

/** The subset of an order the relay needs; both the summary and full shapes satisfy it. */
export type RelayOrder = Pick<
  LabOrderSummary | LabOrder,
  'status' | 'created_at' | 'lab_assignment'
>

/** One released version, as the provider-side release-version listing returns it. */
export interface RelayReleaseVersion {
  release_version_number: number
  released_at: string
  review_status: string
  reviewed_at: string | null
}

export interface ProviderRelayInput {
  order: RelayOrder
  /** The establishment reading the relay; it recorded the order itself. */
  providerOrganizationName?: string | null
  /** Released versions, newest last. Empty when nothing has been released. */
  releaseVersions?: readonly RelayReleaseVersion[]
}

const STOPPED_STATUSES: readonly LabOrderStatus[] = ['REJECTED', 'CANCELLED']
const ACCEPTED_OR_LATER: readonly LabOrderStatus[] = ['ACCEPTED', 'IN_PROGRESS', 'COMPLETED']

/**
 * The relay as a provider sees it.
 *
 * Six facts, in the order they can occur. A stopped order truncates the relay
 * rather than showing later steps as pending: an order the laboratory refused has
 * no analysis owed by anyone, and listing "Analyse en attente" beneath it would
 * tell the clinician to wait for something that will never come.
 */
export function providerRelayNodes({
  order,
  providerOrganizationName,
  releaseVersions = [],
}: ProviderRelayInput): RelayNode[] {
  const nodes: RelayNode[] = [
    {
      id: 'created',
      label: 'Ordre créé',
      organizationName: providerOrganizationName ?? null,
      at: order.created_at,
      state: 'done',
    },
  ]

  const assignment = order.lab_assignment
  nodes.push(
    assignment
      ? {
          id: 'assigned',
          // "Retenu", not "accepté": choosing a laboratory and that laboratory
          // agreeing to the work are two different facts, and only the first has
          // happened at this point.
          label: 'Laboratoire retenu',
          organizationName: assignment.lab_organization_name,
          at: assignment.selected_at,
          state: 'done',
        }
      : {
          id: 'assigned',
          label: 'Laboratoire à choisir',
          at: null,
          state: 'awaiting',
        },
  )

  if (order.status === 'REJECTED') {
    nodes.push({
      id: 'accepted',
      label: 'Ordre refusé par le laboratoire',
      organizationName: assignment?.lab_organization_name ?? null,
      at: null,
      state: 'stopped',
    })
    return nodes
  }

  if (order.status === 'CANCELLED') {
    nodes.push({
      id: 'cancelled',
      label: 'Ordre annulé',
      organizationName: providerOrganizationName ?? null,
      at: null,
      state: 'stopped',
    })
    return nodes
  }

  if (ACCEPTED_OR_LATER.includes(order.status)) {
    nodes.push({
      id: 'accepted',
      label: 'Ordre accepté par le laboratoire',
      organizationName: assignment?.lab_organization_name ?? null,
      // The provider-side response carries no acceptance timestamp. The relay says
      // so rather than reusing `selected_at`, which would date the laboratory's
      // agreement to the moment it was merely chosen.
      at: null,
      state: 'done',
    })
  } else if (assignment) {
    nodes.push({
      id: 'accepted',
      label: 'Acceptation par le laboratoire attendue',
      organizationName: assignment.lab_organization_name,
      at: null,
      state: 'awaiting',
    })
  }

  if (order.status === 'IN_PROGRESS') {
    nodes.push({
      id: 'analysis',
      label: 'Analyse en cours',
      organizationName: assignment?.lab_organization_name ?? null,
      at: null,
      state: 'current',
    })
  } else if (order.status === 'COMPLETED') {
    nodes.push({
      id: 'analysis',
      label: 'Analyse terminée',
      organizationName: assignment?.lab_organization_name ?? null,
      at: null,
      state: 'done',
    })
  } else {
    // Not under way yet. Release is always shown as owed below, so omitting this
    // step made an accepted order read "accepté ✓ → libération attendue", as though
    // the analysis itself had been skipped. An awaiting node claims nothing that has
    // not happened; it states a step the order structurally still owes, undated and
    // unattributed to any person, exactly like the release node under it.
    nodes.push({
      id: 'analysis',
      label: 'Analyse attendue',
      organizationName: assignment?.lab_organization_name ?? null,
      at: null,
      state: 'awaiting',
    })
  }

  // Newest released version last, so the relay ends on the current obligation.
  const latest = releaseVersions.length > 0 ? releaseVersions[releaseVersions.length - 1] : null

  if (!latest && order.status === 'COMPLETED') {
    // `COMPLETED` is reachable only through the release endpoint, so the order is
    // released even when this caller has not loaded the version list. Reporting
    // "Libération attendue" here would be plainly false. Without the versions
    // there is no release timestamp and no review status, so the release is stated
    // undated and no claim is made about review at all — an absent node is honest,
    // a guessed one is not.
    nodes.push({
      id: 'released',
      label: 'Résultats libérés',
      organizationName: assignment?.lab_organization_name ?? null,
      at: null,
      state: 'done',
    })
    return nodes
  }

  if (latest) {
    nodes.push({
      id: 'released',
      // An amendment is a release too; the version number carries which one, and
      // it lives here as a technical reference rather than in the label.
      label: latest.release_version_number > 1 ? 'Rectificatif libéré' : 'Résultats libérés',
      organizationName: assignment?.lab_organization_name ?? null,
      at: latest.released_at,
      state: 'done',
      detail: 'Version ' + latest.release_version_number,
    })

    nodes.push(
      latest.review_status === 'PENDING'
        ? {
            id: 'reviewed',
            // Released is not reviewed. A clinician still owes this record a
            // disposition, and the obligation belongs to the establishment.
            label: 'Examen par un médecin attendu',
            organizationName: providerOrganizationName ?? null,
            at: null,
            state: 'awaiting',
          }
        : {
            id: 'reviewed',
            label: 'Examen enregistré',
            organizationName: providerOrganizationName ?? null,
            at: latest.reviewed_at,
            state: 'done',
          },
    )
  } else if (!STOPPED_STATUSES.includes(order.status)) {
    nodes.push({
      id: 'released',
      // Entry completed is not released, so this stays owed until a release
      // version exists. It no longer says "even when the order already reads
      // COMPLETED": that was this builder's own bug, and the branch above now
      // returns early for COMPLETED because release is the only way to reach it.
      // This node is therefore only ever reached for an order that has genuinely
      // not been released.
      label: 'Libération des résultats attendue',
      organizationName: assignment?.lab_organization_name ?? null,
      at: null,
      state: 'awaiting',
    })
  }

  return nodes
}
