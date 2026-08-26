import type { OpenMrsSession } from '../../api/types'
import { DAHER_CLINICIAN_ADMISSION_ROLE } from '../../api/admissionConstants'

export type AdmissionReadiness =
  | { status: 'NOT_ELIGIBLE' } // no Daher Clinician Admission role — UX gate only (§4)
  | { status: 'PROVIDER_MISSING' } // has the role, but currentProvider is null (§5) — fail closed
  | { status: 'READY' }

// Pure over an already-fetched session (§5): fails closed whenever the
// exact "Provider" role's activation (ADR-017E2) hasn't produced a
// currentProvider for a session that otherwise holds the admission role.
// Never manufactures a Provider and never treats role membership alone as
// sufficient — both conditions must hold for READY.
export function deriveReadiness(session: OpenMrsSession): AdmissionReadiness {
  const hasAdmissionRole =
    session.authenticated &&
    (session.user?.roles ?? []).some((role) => role.name === DAHER_CLINICIAN_ADMISSION_ROLE)

  if (!hasAdmissionRole) {
    return { status: 'NOT_ELIGIBLE' }
  }
  if (!session.currentProvider) {
    return { status: 'PROVIDER_MISSING' }
  }
  return { status: 'READY' }
}
