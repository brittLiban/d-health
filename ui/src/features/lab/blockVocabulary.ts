/**
 * DHEALTH-LAB-BLOCKED-001: the French wording for a blocked diagnostic order, in one place.
 *
 * One module, deliberately, even though two different screens in two different sections use it.
 * The laboratory and the ordering clinic must not drift into describing the same block
 * differently -- a technician reading "Prélèvement inutilisable" and a clinician reading
 * something subtly different about the same order is precisely the kind of gap a telephone call
 * then has to close, which is what this whole feature exists to avoid.
 *
 * Three rules govern every string below.
 *
 * 1. **Operational, never clinical.** `SPECIMEN_UNUSABLE` renders as "Prélèvement inutilisable",
 *    a statement about a tube. It is never rendered as a haemolysis finding, a sample-quality
 *    judgement, or anything a clinician would read as an observation about the patient. DHealth
 *    performs no clinical interpretation (`AGENTS.md`; `docs/MVP-1.0.md` §13).
 * 2. **Say only what was recorded.** The reason is what a laboratory worker chose from a list.
 *    It is not evidence of what physically happened, and no label implies a next physical step --
 *    "Prélèvement inutilisable" does not say a new sample has been requested, because DHealth
 *    does not know that. Recollection has no representation in this release (§12; **D-3**).
 * 3. **No raw enum on screen.** An unrecognised key falls back to a neutral "Motif non précisé"
 *    rather than printing `SPECIMEN_UNUSABLE` at a technician, and never to a guessed French
 *    label -- a wrong label is worse than an absent one.
 *
 * The four keys are `docs/NATIVE-OPERATING-PRODUCT.md` §16's own list and the catalog is
 * **provisional**: §16 requires it to be confirmed with a laboratory participant before it is
 * fixed, and that has not happened. Notably absent, and absent on purpose: any equipment or
 * analyser reason. A laboratory whose analyser is down currently records the nearest true reason
 * and explains it in the free-text note.
 */

import type { LabOrderBlockReason } from '../../api/exchangeTypes'

/**
 * The order the reasons are offered in. Explicit rather than `Object.keys`, so the selector's
 * order is a decision rather than an artefact of how the object literal was typed.
 */
export const BLOCK_REASONS: readonly LabOrderBlockReason[] = [
  'SPECIMEN_UNUSABLE',
  'SPECIMEN_INSUFFICIENT',
  'PATIENT_DID_NOT_ATTEND',
  'CLARIFICATION_REQUIRED',
] as const

export function formatBlockReason(reason: string): string {
  switch (reason) {
    case 'SPECIMEN_UNUSABLE':
      return 'Prélèvement inutilisable'
    case 'SPECIMEN_INSUFFICIENT':
      return 'Quantité insuffisante'
    case 'PATIENT_DID_NOT_ATTEND':
      return 'Patient non présenté'
    case 'CLARIFICATION_REQUIRED':
      return 'Précision nécessaire'
    default:
      // Rule 3. A reason key this build does not know about is a deployment skew, not an
      // opportunity to guess.
      return 'Motif non précisé'
  }
}
