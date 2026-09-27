/**
 * Storage for unresolved native Clinic diagnostic order creation (DH-CLINIC-003A).
 *
 * Corrected [DH-STATE-001, 2026-09-17]: the persisted tuple carries no *direct* PHI
 * (no name, DOB, phone, address, or other demographic field is ever written here), but
 * it is not "non-sensitive" — it is a linkable technical health-workflow identifier set.
 * `patient_reference_id` ties a specific browser/user session to a specific patient's
 * order activity at a specific provider; that linkage is sensitive metadata and must be
 * protected accordingly even though it contains no demographic content of its own.
 *
 * Requirements:
 * - Persists ONLY the technical opaque identity tuple across reload/restart.
 * - No direct PHI: no name, no phone, no contact, no demographics.
 * - ZERO clinical payload in persistent storage: no test codes, no catalog entry IDs,
 *   no test names, no clinical results.
 * - Bound strictly to authenticated user identity + active provider + patient reference scope.
 * - Provider A cannot access Provider B's pending order token.
 * - User A cannot access User B's pending order token.
 * - Patient A cannot access Patient B's pending order token.
 * - Never expires an unresolved identity: expiry would make a potentially committed
 *   order unrecoverable and permit a duplicate.
 * - Cleared only once the server has authoritatively resolved the attempt.
 */

export interface OpaquePendingOrderIdentity {
  user_id: string
  provider_id: string
  patient_reference_id: string
  idempotency_key: string
  created_at: number
  /** Set after the intent is created on backend; still no direct PHI, still non-clinical. */
  intent_id?: string
  /**
   * DHEALTH-CLINIC-VISIT-001: the clinic visit this order was being raised during, when it was.
   *
   * Persisted for a correctness reason, not for display. `visit_id` is part of the backend's
   * intent-replay comparison, so a retry that presents the same idempotency key with *no* visit
   * context is refused with a conflict rather than quietly creating an unlinked order. Without this
   * field, a page reload between "intent POST sent" and "intent_id stored" would lose the visit and
   * turn a recoverable attempt into a dead key.
   *
   * It is another opaque technical identifier of the same kind as the three above, and the same
   * sensitivity note applies: no demographic content, but a linkable health-workflow identifier, so
   * it is protected accordingly. Still zero clinical payload — a visit id is not a note, a reason,
   * or a test.
   */
  visit_id?: string
  /** Set only after the order create POST has returned successfully; still no direct PHI. */
  confirmed_order_id?: string
}

export class PendingOrderStorageReadError extends Error {
  constructor(message = "Impossible de lire le stockage de réconciliation d'ordre.") {
    super(message)
    this.name = 'PendingOrderStorageReadError'
  }
}

const STORAGE_PREFIX = 'dhealth_clinic_pending_order_v1:'

function makeKey(userId: string, providerId: string, patientReferenceId: string): string {
  return `${STORAGE_PREFIX}${userId}:${providerId}:${patientReferenceId}`
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

/** A read failure means we cannot prove that this scoped browser has no pending order. */
export function canReadPendingOrderStorage(): boolean {
  const storage = getStorage()
  if (!storage) return false
  try {
    storage.getItem(STORAGE_PREFIX)
    return true
  } catch {
    return false
  }
}

export function getStoredPendingOrder(
  userId: string,
  providerId: string,
  patientReferenceId: string,
): OpaquePendingOrderIdentity | null {
  const storage = getStorage()
  if (!storage || !userId || !providerId || !patientReferenceId) return null

  const key = makeKey(userId, providerId, patientReferenceId)
  let raw: string | null
  try {
    raw = storage.getItem(key)
  } catch {
    throw new PendingOrderStorageReadError('Storage access error')
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
    throw new PendingOrderStorageReadError('Corrupted pending order storage: malformed JSON')
  }

  // 3. Schema validation: must be a plain object
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new PendingOrderStorageReadError('Corrupted pending order storage: expected JSON object')
  }

  const obj = parsed as Record<string, unknown>

  // 4. Validate required technical fields
  const hasValidUserId = typeof obj.user_id === 'string' && obj.user_id.trim().length > 0
  const hasValidProviderId = typeof obj.provider_id === 'string' && obj.provider_id.trim().length > 0
  const hasValidPatientRefId =
    typeof obj.patient_reference_id === 'string' && obj.patient_reference_id.trim().length > 0
  const hasValidIdempotencyKey =
    typeof obj.idempotency_key === 'string' && obj.idempotency_key.trim().length > 0
  const hasValidCreatedAt =
    typeof obj.created_at === 'number' && Number.isFinite(obj.created_at)

  if (
    !hasValidUserId ||
    !hasValidProviderId ||
    !hasValidPatientRefId ||
    !hasValidIdempotencyKey ||
    !hasValidCreatedAt
  ) {
    throw new PendingOrderStorageReadError(
      'Corrupted pending order storage: missing or invalid required technical fields',
    )
  }

  // 5. Optional intent_id must be valid string if present
  if ('intent_id' in obj && obj.intent_id !== undefined) {
    if (
      typeof obj.intent_id !== 'string' ||
      obj.intent_id.trim().length === 0
    ) {
      throw new PendingOrderStorageReadError(
        'Corrupted pending order storage: invalid intent_id',
      )
    }
  }

  // 6. Optional visit_id must be a valid string if present. Validated rather than trusted for the
  //    same reason as the two ids around it: a corrupted value here would be sent to the backend as
  //    part of an idempotency-replay comparison, where it would produce a conflict the clinician
  //    cannot interpret. An absent visit_id is normal — most orders have no visit.
  if ('visit_id' in obj && obj.visit_id !== undefined) {
    if (typeof obj.visit_id !== 'string' || obj.visit_id.trim().length === 0) {
      throw new PendingOrderStorageReadError('Corrupted pending order storage: invalid visit_id')
    }
  }

  // 7. Optional confirmed_order_id must be valid string if present
  if ('confirmed_order_id' in obj && obj.confirmed_order_id !== undefined) {
    if (
      typeof obj.confirmed_order_id !== 'string' ||
      obj.confirmed_order_id.trim().length === 0
    ) {
      throw new PendingOrderStorageReadError(
        'Corrupted pending order storage: invalid confirmed_order_id',
      )
    }
  }

  return {
    user_id: obj.user_id as string,
    provider_id: obj.provider_id as string,
    patient_reference_id: obj.patient_reference_id as string,
    idempotency_key: obj.idempotency_key as string,
    created_at: obj.created_at as number,
    ...(typeof obj.intent_id === 'string' ? { intent_id: obj.intent_id } : {}),
    ...(typeof obj.visit_id === 'string' ? { visit_id: obj.visit_id } : {}),
    ...(typeof obj.confirmed_order_id === 'string'
      ? { confirmed_order_id: obj.confirmed_order_id }
      : {}),
  }
}

export function setStoredPendingOrder(
  userId: string,
  providerId: string,
  patientReferenceId: string,
  pending: Omit<OpaquePendingOrderIdentity, 'created_at' | 'user_id' | 'provider_id' | 'patient_reference_id'> & {
    created_at?: number
  },
): boolean {
  const storage = getStorage()
  if (!storage || !userId || !providerId || !patientReferenceId) return false

  const record: OpaquePendingOrderIdentity = {
    user_id: userId,
    provider_id: providerId,
    patient_reference_id: patientReferenceId,
    idempotency_key: pending.idempotency_key,
    created_at: pending.created_at ?? Date.now(),
    ...(typeof pending.visit_id === 'string' ? { visit_id: pending.visit_id } : {}),
    ...(typeof pending.intent_id === 'string'
      ? { intent_id: pending.intent_id }
      : {}),
    ...(typeof pending.confirmed_order_id === 'string'
      ? { confirmed_order_id: pending.confirmed_order_id }
      : {}),
  }

  try {
    const key = makeKey(userId, providerId, patientReferenceId)
    storage.setItem(key, JSON.stringify(record))
    return storage.getItem(key) === JSON.stringify(record)
  } catch {
    return false
  }
}

export function clearStoredPendingOrder(
  userId: string,
  providerId: string,
  patientReferenceId: string,
): void {
  const storage = getStorage()
  if (!storage || !userId || !providerId || !patientReferenceId) return
  try {
    storage.removeItem(makeKey(userId, providerId, patientReferenceId))
  } catch {
    // Best-effort removal
  }
}

export function clearAllStoredPendingOrdersForUser(userId: string): void {
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

export function clearAllStoredPendingOrders(): void {
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

