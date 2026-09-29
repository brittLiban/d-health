/**
 * The provider-side French wording for a diagnostic order's status, in one place.
 *
 * Moved here verbatim from `ClinicPatientsPage.tsx` by DHEALTH-CLINIC-VISIT-001, which needed the
 * same labels on the visit workspace and would otherwise have rendered the raw enum
 * (`AWAITING_LAB_SELECTION`) on a French-first screen. Not a rewrite: the labels, tones and the
 * DH-CLINIC-006 §35 note below are unchanged, so nothing a clinician already reads has moved.
 *
 * ── Known remaining duplication, stated rather than quietly left ──
 *
 * `ClinicOrdersPage.tsx` and `LabOrdersPage.tsx` each still carry their own
 * `formatOrderStatusLabel`. Both predate this module and neither was touched: they are label-only
 * (no tone), the laboratory's wording is deliberately not the provider's for some of the same
 * statuses, and rewiring two qualified pages to prove a point about duplication is not this ticket's
 * scope. If a future ticket consolidates all three, this is where they belong, and the laboratory's
 * different wording is a real requirement to preserve rather than a drift to flatten.
 */

import type { StatusTone } from '../../design-system'

export function formatOrderStatus(status: string): { label: string; tone: StatusTone } {
  switch (status) {
    case 'AWAITING_LAB_SELECTION':
      return { label: 'En attente de choix labo', tone: 'neutral' }
    case 'SELECTED':
      // DH-CLINIC-006 §35: unified with ClinicOrdersPage.tsx's own label -- distinct from
      // ACCEPTED's own meaning (the lab explicitly accepting), never implies acceptance/collection.
      return { label: 'Laboratoire destinataire sélectionné', tone: 'neutral' }
    case 'ACCEPTED':
      return { label: 'Accepté', tone: 'neutral' }
    case 'IN_PROGRESS':
      return { label: "En cours d'analyse", tone: 'neutral' }
    case 'COMPLETED':
      return { label: 'Résultats disponibles', tone: 'success' }
    case 'REJECTED':
      return { label: 'Rejeté', tone: 'danger' }
    case 'CANCELLED':
      return { label: 'Annulé', tone: 'neutral' }
    case 'BLOCKED':
      // DHEALTH-LAB-BLOCKED-001 §29. `warning`, not `danger`: the laboratory has stopped, which a
      // clinician needs to notice, but the order is not refused and not lost -- `danger` is what
      // `REJECTED` means here and the two must stay distinguishable at a glance. The reason
      // itself is rendered separately from `LabOrder.block.reason` (see `blockVocabulary.ts`), so
      // this label deliberately does not try to carry it.
      return { label: 'Bloqué au laboratoire', tone: 'warning' }
    default:
      // An unknown status shows its own raw value rather than a guess. A wrong French label would
      // be worse than an obviously untranslated one.
      return { label: status, tone: 'neutral' }
  }
}
