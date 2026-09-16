import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearAllStoredPendingOrders,
  clearAllStoredPendingOrdersForUser,
  clearStoredPendingOrder,
  getStoredPendingOrder,
  PendingOrderStorageReadError,
  setStoredPendingOrder,
} from './pendingOrderStorage'

describe('pendingOrderStorage', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('stores and retrieves opaque pending order identity', () => {
    const success = setStoredPendingOrder('user-1', 'prov-1', 'pat-1', {
      idempotency_key: 'idemp-uuid-1',
    })
    expect(success).toBe(true)

    const stored = getStoredPendingOrder('user-1', 'prov-1', 'pat-1')
    expect(stored).not.toBeNull()
    expect(stored?.user_id).toBe('user-1')
    expect(stored?.provider_id).toBe('prov-1')
    expect(stored?.patient_reference_id).toBe('pat-1')
    expect(stored?.idempotency_key).toBe('idemp-uuid-1')
    expect(typeof stored?.created_at).toBe('number')
    expect(stored?.confirmed_order_id).toBeUndefined()
  })

  it('stores optional intent_id', () => {
    setStoredPendingOrder('user-1', 'prov-1', 'pat-1', {
      idempotency_key: 'idemp-uuid-1',
      intent_id: 'intent-uuid-1',
    })

    const stored = getStoredPendingOrder('user-1', 'prov-1', 'pat-1')
    expect(stored?.intent_id).toBe('intent-uuid-1')
  })

  it('stores optional confirmed_order_id', () => {
    setStoredPendingOrder('user-1', 'prov-1', 'pat-1', {
      idempotency_key: 'idemp-uuid-1',
      confirmed_order_id: 'order-uuid-1',
    })

    const stored = getStoredPendingOrder('user-1', 'prov-1', 'pat-1')
    expect(stored?.confirmed_order_id).toBe('order-uuid-1')
  })

  it('isolates by user, provider, and patient reference', () => {
    setStoredPendingOrder('user-1', 'prov-1', 'pat-1', {
      idempotency_key: 'idemp-u1-p1-pat1',
    })

    // Different user
    expect(getStoredPendingOrder('user-2', 'prov-1', 'pat-1')).toBeNull()

    // Different provider
    expect(getStoredPendingOrder('user-1', 'prov-2', 'pat-1')).toBeNull()

    // Different patient
    expect(getStoredPendingOrder('user-1', 'prov-1', 'pat-2')).toBeNull()
  })

  it('clears stored pending order', () => {
    setStoredPendingOrder('user-1', 'prov-1', 'pat-1', {
      idempotency_key: 'idemp-uuid-1',
    })
    expect(getStoredPendingOrder('user-1', 'prov-1', 'pat-1')).not.toBeNull()

    clearStoredPendingOrder('user-1', 'prov-1', 'pat-1')
    expect(getStoredPendingOrder('user-1', 'prov-1', 'pat-1')).toBeNull()
  })

  it('throws PendingOrderStorageReadError on corrupted JSON', () => {
    localStorage.setItem('dhealth_clinic_pending_order_v1:user-1:prov-1:pat-1', '{corrupt-json')
    expect(() => getStoredPendingOrder('user-1', 'prov-1', 'pat-1')).toThrow(
      PendingOrderStorageReadError,
    )
  })

  it('throws PendingOrderStorageReadError on missing required fields', () => {
    localStorage.setItem(
      'dhealth_clinic_pending_order_v1:user-1:prov-1:pat-1',
      JSON.stringify({ user_id: 'user-1' }),
    )
    expect(() => getStoredPendingOrder('user-1', 'prov-1', 'pat-1')).toThrow(
      PendingOrderStorageReadError,
    )
  })

  it('clears all stored pending orders for a specific user', () => {
    setStoredPendingOrder('user-1', 'prov-1', 'pat-1', { idempotency_key: 'key-1' })
    setStoredPendingOrder('user-1', 'prov-2', 'pat-2', { idempotency_key: 'key-2' })
    setStoredPendingOrder('user-2', 'prov-1', 'pat-1', { idempotency_key: 'key-3' })

    clearAllStoredPendingOrdersForUser('user-1')

    expect(getStoredPendingOrder('user-1', 'prov-1', 'pat-1')).toBeNull()
    expect(getStoredPendingOrder('user-1', 'prov-2', 'pat-2')).toBeNull()
    expect(getStoredPendingOrder('user-2', 'prov-1', 'pat-1')).not.toBeNull()
  })

  it('clears all stored pending orders across all users', () => {
    setStoredPendingOrder('user-1', 'prov-1', 'pat-1', { idempotency_key: 'key-1' })
    setStoredPendingOrder('user-2', 'prov-2', 'pat-2', { idempotency_key: 'key-2' })

    clearAllStoredPendingOrders()

    expect(getStoredPendingOrder('user-1', 'prov-1', 'pat-1')).toBeNull()
    expect(getStoredPendingOrder('user-2', 'prov-2', 'pat-2')).toBeNull()
  })

  // -- DH-CLINIC-003C negative-control coverage closure: NC18 permanent regression ----------------
  //
  // The negative-control audit found that round-tripping through `getStoredPendingOrder` cannot
  // detect an extra clinical field on the serialized record: the getter only ever echoes back its
  // own known-field allowlist, silently dropping anything else even if the setter leaked it. This
  // test reads the RAW localStorage string directly and inspects its actual key set -- the only
  // way to make a `catalog_entry_ids`-style leak in `setStoredPendingOrder` observably RED.

  it('serializes ONLY the approved non-clinical technical field set to raw localStorage', () => {
    setStoredPendingOrder('user-1', 'prov-1', 'pat-1', {
      idempotency_key: 'idemp-uuid-1',
      intent_id: 'intent-uuid-1',
    })

    const raw = localStorage.getItem('dhealth_clinic_pending_order_v1:user-1:prov-1:pat-1')
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw as string) as Record<string, unknown>

    const allowedKeys = new Set([
      'user_id',
      'provider_id',
      'patient_reference_id',
      'idempotency_key',
      'created_at',
      'intent_id',
      'confirmed_order_id',
    ])
    for (const key of Object.keys(parsed)) {
      expect(allowedKeys.has(key)).toBe(true)
    }

    // Explicit forbidden-content checks -- zero clinical/diagnostic payload of any kind.
    expect(parsed).not.toHaveProperty('catalog_entry_ids')
    expect(parsed).not.toHaveProperty('catalog_entry_id')
    expect(parsed).not.toHaveProperty('items')
    expect(parsed).not.toHaveProperty('catalog_code_snapshot')
    expect(parsed).not.toHaveProperty('catalog_display_name_snapshot')
    expect(parsed).not.toHaveProperty('display_name')
    expect(parsed).not.toHaveProperty('contact_value')
    expect(parsed).not.toHaveProperty('contact_channel_type')
    expect(parsed).not.toHaveProperty('result')
    expect(parsed).not.toHaveProperty('result_value')
    expect(JSON.stringify(parsed)).not.toMatch(/catalog|display_name|contact_|result_value/i)
  })

  // -- DH-CLINIC-003C negative-control coverage closure: NC19 permanent regression ----------------
  //
  // The audit found no TTL/expiry code exists anywhere in this module (verified by reading the
  // complete file) and therefore did not force a mutation -- inventing destructive TTL logic just
  // to then prove a test catches it would mean testing a defect that does not exist in production.
  // This test instead locks in the durability invariant itself as a permanent regression guard:
  // an old `created_at` must never cause silent removal, so if TTL logic is added later without
  // reading this test, it goes RED immediately.

  it('never silently discards an old unresolved pending order (no destructive TTL)', () => {
    const yearsAgo = Date.now() - 1000 * 60 * 60 * 24 * 365 * 3 // ~3 years in the past
    setStoredPendingOrder('user-1', 'prov-1', 'pat-1', {
      idempotency_key: 'idemp-uuid-old',
      created_at: yearsAgo,
    })

    const stored = getStoredPendingOrder('user-1', 'prov-1', 'pat-1')
    expect(stored).not.toBeNull()
    expect(stored?.idempotency_key).toBe('idemp-uuid-old')
    expect(stored?.created_at).toBe(yearsAgo)
  })
})
