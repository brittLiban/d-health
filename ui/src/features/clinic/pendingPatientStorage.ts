/**
 * Non-PHI storage for unresolved native Clinic patient creation (DH-CLINIC-002A, updated
 * DH-IDENTITY-001).
 *
 * DH-IDENTITY-001 rewrite: the persisted identity is now a request-identity `idempotency_key`
 * (a value the browser generates for retry/recovery purposes only), never a patient identity --
 * the server owns the authoritative Patient id and its native source-identity binding (§13/§28).
 * The old shape (`source_system`/`source_identity_system`/`source_identity_value`) is gone; those
 * fields never described a real patient identity to begin with, only the browser's own stand-in
 * for one, which this ticket explicitly retires.
 *
 * Requirements (unchanged from before this rewrite):
 * - Persists ONLY technical opaque identity across reload/restart.
 * - ZERO patient PHI: no name, DOB, phone, contact, or other demographics (§30).
 * - Bound strictly to authenticated user identity + active provider scope.
 * - Provider A cannot access Provider B's pending token.
 * - User A cannot access User B's pending token.
 * - Never expires an unresolved identity: expiry would make a potentially committed
 *   create unrecoverable and permit a duplicate.
 * - Cleared only once the server has authoritatively resolved the attempt.
 */

export interface OpaquePendingPatientIdentity {
  idempotency_key: string
  created_at: number
  /** Set only after the create POST has returned successfully; still non-PHI. */
  confirmed_patient_id?: string
  /** Compatibility-bridge id (§33) -- avoids a second network round trip to resolve the
   * existing patient-reference-scoped order/results UI once a Patient is confirmed. Still
   * non-PHI: an opaque reference id, not a demographic field. */
  confirmed_patient_reference_id?: string
}

export class PendingPatientStorageReadError extends Error {
  constructor(message = "Impossible de lire le stockage de réconciliation patient.") {
    super(message)
    this.name = 'PendingPatientStorageReadError'
  }
}

const STORAGE_PREFIX = 'dhealth_clinic_pending_patient_v2:'

function makeKey(userId: string, providerId: string): string {
  return `${STORAGE_PREFIX}${userId}:${providerId}`
}

function getStorage(): Storage | null {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage
    }
  } catch {
    // Storage access may be restricted
  }
  return null
}

/** A read failure means we cannot prove that this scoped browser has no pending registration. */
export function canReadPendingPatientStorage(): boolean {
  const storage = getStorage()
  if (!storage) return false
  try {
    storage.getItem(STORAGE_PREFIX)
    return true
  } catch {
    return false
  }
}

export function getStoredPendingPatient(
  userId: string,
  providerId: string,
): OpaquePendingPatientIdentity | null {
  const storage = getStorage()
  if (!storage || !userId || !providerId) return null

  const key = makeKey(userId, providerId)
  let raw: string | null
  try {
    raw = storage.getItem(key)
  } catch {
    throw new PendingPatientStorageReadError('Storage access error')
  }

  // 1. Truly absent: key does not exist in storage
  if (raw === null) {
    return null
  }

  // 2. Value exists: must parse as valid JSON
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    throw new PendingPatientStorageReadError('Corrupted pending patient storage: malformed JSON')
  }

  // 3. Schema validation: must be a plain object
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new PendingPatientStorageReadError(
      'Corrupted pending patient storage: expected JSON object',
    )
  }

  const obj = parsed as Record<string, unknown>

  // 4. Validate required technical fields
  const hasValidIdempotencyKey =
    typeof obj.idempotency_key === 'string' && obj.idempotency_key.trim().length > 0
  const hasValidCreatedAt = typeof obj.created_at === 'number' && Number.isFinite(obj.created_at)

  if (!hasValidIdempotencyKey || !hasValidCreatedAt) {
    throw new PendingPatientStorageReadError(
      'Corrupted pending patient storage: missing or invalid required technical fields',
    )
  }

  // 5. Optional confirmed_patient_id must be a valid string if present
  if ('confirmed_patient_id' in obj && obj.confirmed_patient_id !== undefined) {
    if (typeof obj.confirmed_patient_id !== 'string' || obj.confirmed_patient_id.trim().length === 0) {
      throw new PendingPatientStorageReadError(
        'Corrupted pending patient storage: invalid confirmed_patient_id',
      )
    }
  }

  // 6. Optional confirmed_patient_reference_id must be a valid string if present
  if ('confirmed_patient_reference_id' in obj && obj.confirmed_patient_reference_id !== undefined) {
    if (
      typeof obj.confirmed_patient_reference_id !== 'string' ||
      obj.confirmed_patient_reference_id.trim().length === 0
    ) {
      throw new PendingPatientStorageReadError(
        'Corrupted pending patient storage: invalid confirmed_patient_reference_id',
      )
    }
  }

  return {
    idempotency_key: obj.idempotency_key as string,
    created_at: obj.created_at as number,
    ...(typeof obj.confirmed_patient_id === 'string'
      ? { confirmed_patient_id: obj.confirmed_patient_id }
      : {}),
    ...(typeof obj.confirmed_patient_reference_id === 'string'
      ? { confirmed_patient_reference_id: obj.confirmed_patient_reference_id }
      : {}),
  }
}

export function setStoredPendingPatient(
  userId: string,
  providerId: string,
  pending: Omit<OpaquePendingPatientIdentity, 'created_at'> & { created_at?: number },
): boolean {
  const storage = getStorage()
  if (!storage || !userId || !providerId) return false

  const record: OpaquePendingPatientIdentity = {
    idempotency_key: pending.idempotency_key,
    created_at: pending.created_at ?? Date.now(),
    ...(typeof pending.confirmed_patient_id === 'string'
      ? { confirmed_patient_id: pending.confirmed_patient_id }
      : {}),
    ...(typeof pending.confirmed_patient_reference_id === 'string'
      ? { confirmed_patient_reference_id: pending.confirmed_patient_reference_id }
      : {}),
  }

  try {
    storage.setItem(makeKey(userId, providerId), JSON.stringify(record))
    return storage.getItem(makeKey(userId, providerId)) === JSON.stringify(record)
  } catch {
    return false
  }
}

export function clearStoredPendingPatient(userId: string, providerId: string): void {
  const storage = getStorage()
  if (!storage || !userId || !providerId) return

  try {
    storage.removeItem(makeKey(userId, providerId))
  } catch {
    // Ignore storage errors
  }
}

export function clearAllStoredPendingPatientsForUser(userId: string): void {
  const storage = getStorage()
  if (!storage || !userId) return

  const userPrefix = `${STORAGE_PREFIX}${userId}:`
  try {
    const keysToRemove: string[] = []
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (key && key.startsWith(userPrefix)) {
        keysToRemove.push(key)
      }
    }
    for (const key of keysToRemove) {
      storage.removeItem(key)
    }
  } catch {
    // Ignore errors
  }
}

export function clearAllStoredPendingPatients(): void {
  const storage = getStorage()
  if (!storage) return

  try {
    const keysToRemove: string[] = []
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (key && key.startsWith(STORAGE_PREFIX)) {
        keysToRemove.push(key)
      }
    }
    for (const key of keysToRemove) {
      storage.removeItem(key)
    }
  } catch {
    // Ignore errors
  }
}
