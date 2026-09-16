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
})
