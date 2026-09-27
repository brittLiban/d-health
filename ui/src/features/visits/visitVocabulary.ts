/**
 * DHEALTH-CLINIC-VISIT-001 — the French words this product uses for a visit, in one place.
 *
 * Centralised for the reason `onboardingVocabulary.ts` already established: a status shown as
 * "En cours" on the worklist and "Consultation démarrée" in a confirmation is two different facts as
 * far as the person reading them is concerned. French is first-class here, not a localisation layer
 * over English strings (`AGENTS.md`), so these are the product's own words rather than translations
 * of the enum names.
 *
 * ── Why these particular words ──
 *
 * `CHECKED_IN` is **"En attente"**, not "Enregistré". The waiting list *is* this state (§12), and
 * what the clinic needs to see is that somebody is waiting — "enregistré" describes a filing action
 * and says nothing about the person sitting in the room.
 *
 * `IN_PROGRESS` is **"En consultation"** rather than "En cours", which would be true of every
 * unfinished thing on every screen in the product.
 *
 * `COMPLETED` is **"Terminée"**, deliberately not "Clôturée" or "Validée". Nothing was validated:
 * completing a visit is a clinician saying they are finished with the patient, and it carries no
 * claim about results, billing or review.
 *
 * `CANCELLED` is **"Annulée"**, and the interface only ever offers it for a visit that has not
 * started. §12 is explicit that a visit already in progress is a completed-with-note, never a
 * cancellation, so there is no wording here for cancelling a consultation that happened.
 */

import type { VisitStatus } from '../../api/exchangeTypes'
import type { StatusTone } from '../../design-system'

const VISIT_STATUS_LABELS: Record<VisitStatus, string> = {
  CHECKED_IN: 'En attente',
  IN_PROGRESS: 'En consultation',
  COMPLETED: 'Terminée',
  CANCELLED: 'Annulée',
}

export function visitStatusLabel(status: VisitStatus): string {
  return VISIT_STATUS_LABELS[status] ?? status
}

/**
 * Colour reinforces the label, never replaces it (`StatusBadge`'s own rule, §19).
 *
 * `CHECKED_IN` is a warning tone because a waiting patient is the one state that asks somebody to
 * act. `IN_PROGRESS` is neutral: it is work happening normally, and colouring it would make a busy
 * clinic look like a screen full of alerts. `CANCELLED` is `neutral` rather than `danger` — a
 * cancelled visit is an ordinary outcome, not a failure, and marking it red would read as an error
 * the clinician should fix.
 */
export function visitStatusTone(status: VisitStatus): StatusTone {
  if (status === 'CHECKED_IN') return 'warning'
  if (status === 'COMPLETED') return 'success'
  return 'neutral'
}

/** A short, exact time of day. Never a "il y a 20 minutes" relative label: a waiting time computed
 * in the browser from two clocks is a number a clinic would act on, and this product does not invent
 * derived clinical signals (`AGENTS.md`). */
export function visitTime(iso: string | null): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return '—'
  return parsed.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
}

export function visitDate(iso: string | null): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return '—'
  return parsed.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

/** The patient's name as the worklist should show it. `null` is a real possibility (the column is
 * nullable for legacy-backfill reasons), and it must never render as "null" or as an empty row. */
export function visitPatientName(displayName: string | null): string {
  const trimmed = (displayName ?? '').trim()
  return trimmed.length > 0 ? trimmed : 'Patient sans nom enregistré'
}
