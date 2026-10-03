/**
 * DHEALTH-APPOINTMENTS-001/ADR-073 — what a booking's row says, and what it refuses to say.
 *
 * Its own module rather than an export from `ClinicAppointments.tsx` so the component file exports
 * only a component (fast refresh), and so this logic — the part with a decision in it — is unit
 * tested directly rather than through a rendered tree.
 *
 * ── The decision this encodes ──
 *
 * Arrival is a **projection**, never a stored appointment state: `visit_id !== null` plus the linked
 * visit's own status. There is no `ARRIVED` column to read and none to keep in sync.
 *
 * And "the clock has moved past this booking" is reported as an observation and nothing more.
 * `Heure passée` carries the **same neutral tone** as `À venir`: a warning or danger colour would
 * turn a timestamp comparison into something that reads as a verdict on the patient, which is
 * precisely the no-show judgement ADR-073 declines to invent (Human Decision 4). The words differ;
 * the colour does not, so nothing is communicated by colour alone either.
 */

import type { Appointment } from '../../api/exchangeTypes'

/** Only the two tones `StatusBadge` needs here. Deliberately no `warning`/`danger` — see above. */
export type AppointmentTone = 'success' | 'neutral'

export interface AppointmentPresentation {
  label: string
  tone: AppointmentTone
  /** True when the scheduled instant is in the past and no visit is linked. **Not** a no-show. */
  passed: boolean
}

export function appointmentPresentation(
  appointment: Appointment,
  now: Date,
): AppointmentPresentation {
  if (appointment.status === 'CANCELLED') {
    return { label: 'Annulé', tone: 'neutral', passed: false }
  }
  if (appointment.visit_id !== null) {
    return {
      label: appointment.visit_status === 'IN_PROGRESS' ? 'En consultation' : 'Arrivé',
      tone: 'success',
      passed: false,
    }
  }
  if (new Date(appointment.scheduled_at).getTime() < now.getTime()) {
    return { label: 'Heure passée', tone: 'neutral', passed: true }
  }
  return { label: 'À venir', tone: 'neutral', passed: false }
}
