/**
 * DH-PATIENT-001 §33/§37/§53: the patient-facing wording, in one place, so every sentence a
 * patient can ever read is reviewable as a set rather than scattered across JSX.
 *
 * Three rules govern every string below, and none of them is a style preference:
 *
 * 1. **Say only what actually happened.** `SELECTED` means a destination laboratory has been
 *    chosen -- not that anything was transmitted to or received by it, which is a distinct fact
 *    the Exchange records separately (`ACCEPTED`). "Envoyé au laboratoire" would be a claim this
 *    system cannot support, so it does not appear here.
 * 2. **Never interpret.** `NO_FOLLOW_UP_REQUIRED` is rendered as "aucun suivi supplémentaire n'a
 *    été indiqué pour cet ordre", never as "tout est normal" (§33) -- a clinician recording that
 *    no follow-up is needed for this order is not the same statement as a result being normal,
 *    and DHealth performs no clinical interpretation at all (AGENTS.md; `docs/MVP-1.0.md` §13).
 *    `FOLLOW_UP_REQUIRED` says to contact the clinic, with no urgency, diagnosis, or advice.
 * 3. **No technical vocabulary.** A patient never sees `IN_PROGRESS`, a UUID, a catalog code, or
 *    the word "statut" followed by an enum -- §53's explicit instruction.
 */

import type { LabOrderStatus, PatientResultStatus } from '../../api/exchangeTypes'

export interface PatientStatusWording {
  /** The single large headline -- short enough to be read at a glance (§32/§53). */
  headline: string
  /** One short supporting sentence, or `null` when the headline genuinely needs none. */
  detail: string | null
  /**
   * Non-colour status channel (§52: status must never be conveyed by colour alone). Rendered as a
   * text/shape marker next to the headline and also carried into the accessible name.
   */
  tone: 'waiting' | 'active' | 'done' | 'attention' | 'stopped'
}

/**
 * DH-PATIENT-001 §33: precedence is deliberate and narrow.
 *
 * `REJECTED`/`CANCELLED` are terminal facts about the *order* and win outright -- an order nobody
 * will perform has no meaningful result status. `COMPLETED` defers entirely to the server-owned
 * `result_status` projection, which is the only thing that knows where the clinician's review
 * obligation stands (including after an amendment). Everything else is a plain workflow stage.
 *
 * The `switch` is exhaustive over `LabOrderStatus` and ends in a `never` assertion: adding a
 * status to the backend enum without deciding what a patient should be told about it becomes a
 * **typecheck failure**, not a silently-rendered raw enum value.
 */
export function patientStatusWording(
  status: LabOrderStatus,
  resultStatus: PatientResultStatus,
): PatientStatusWording {
  switch (status) {
    case 'REJECTED':
      return {
        headline: "Le laboratoire n'a pas accepté cet ordre.",
        detail: 'Contactez votre clinique.',
        tone: 'stopped',
      }
    case 'CANCELLED':
      return {
        headline: 'Cet ordre a été annulé.',
        detail: 'Contactez votre clinique.',
        tone: 'stopped',
      }
    case 'AWAITING_LAB_SELECTION':
      return {
        headline: 'Votre clinique prépare l’orientation de votre analyse.',
        detail: null,
        tone: 'waiting',
      }
    case 'SELECTED':
      return {
        // Not "envoyé au laboratoire": see rule 1 above.
        headline: 'Laboratoire attribué',
        detail: null,
        tone: 'waiting',
      }
    case 'ACCEPTED':
      return {
        headline: 'Ordre accepté par le laboratoire',
        detail: null,
        tone: 'active',
      }
    case 'IN_PROGRESS':
      return {
        headline: 'Analyse en cours',
        detail: null,
        tone: 'active',
      }
    case 'COMPLETED':
      return completedWording(resultStatus)
    default:
      return assertNever(status)
  }
}

function completedWording(resultStatus: PatientResultStatus): PatientStatusWording {
  switch (resultStatus) {
    case 'NO_FOLLOW_UP_REQUIRED':
      return {
        headline: 'Votre médecin a revu le résultat.',
        detail: "Aucun suivi supplémentaire n'a été indiqué pour cet ordre.",
        tone: 'done',
      }
    case 'FOLLOW_UP_REQUIRED':
      return {
        headline: 'Votre médecin a indiqué qu’un suivi est nécessaire.',
        detail: 'Contactez votre clinique.',
        tone: 'attention',
      }
    case 'AWAITING_CLINICIAN_REVIEW':
      return {
        headline: 'Résultat disponible auprès de votre clinique.',
        detail: 'Votre médecin doit encore le revoir.',
        tone: 'active',
      }
    case 'NOT_READY':
      // A COMPLETED order whose projection says NOT_READY is a shape the Exchange does not
      // produce (COMPLETED is only reachable through a release, and a release always creates a
      // version). Rendered as the awaiting-review wording rather than a contradiction or a blank
      // screen -- it asserts nothing false about a review that may not have happened.
      return {
        headline: 'Résultat disponible auprès de votre clinique.',
        detail: 'Votre médecin doit encore le revoir.',
        tone: 'active',
      }
    default:
      return assertNever(resultStatus)
  }
}

function assertNever(value: never): never {
  throw new Error(`unhandled patient status: ${String(value)}`)
}

/** §52: the non-colour marker paired with every tone, so status is never colour-only. */
export function toneMarker(tone: PatientStatusWording['tone']): string {
  switch (tone) {
    case 'waiting':
      return '○'
    case 'active':
      return '●'
    case 'done':
      return '✓'
    case 'attention':
      return '!'
    case 'stopped':
      return '×'
    default:
      return assertNever(tone)
  }
}
