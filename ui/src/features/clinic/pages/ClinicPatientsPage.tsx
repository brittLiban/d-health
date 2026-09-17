import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ExchangeApiError,
  isDefinitiveExchangeError,
  mapExchangeErrorToCode,
} from '../../../api/exchangeClient'
import type {
  CatalogEntry,
  ClinicPatientSummary,
  LabOrder,
  LabOrderCreateOutcome,
  LabOrderIntent,
  LabOrderIntentItem,
  PatientReferenceBindRequest,
} from '../../../api/exchangeTypes'
import type { ErrorCode } from '../../../api/types'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  SearchInput,
  StatusBadge,
  type StatusTone,
  TextInput,
} from '../../../design-system'
import { useClinic } from '../ClinicContext'
import type { PendingOrderMutation, PendingPatientCreation } from '../ClinicApp'
import {
  clearStoredPendingPatient,
  canReadPendingPatientStorage,
  PendingPatientStorageReadError,
  getStoredPendingPatient,
  setStoredPendingPatient,
  type OpaquePendingPatientIdentity,
} from '../pendingPatientStorage'
import {
  canReadPendingOrderStorage,
  clearStoredPendingOrder,
  getStoredPendingOrder,
  type OpaquePendingOrderIdentity,
  PendingOrderStorageReadError,
  setStoredPendingOrder,
} from '../pendingOrderStorage'
import styles from './ClinicPatientsPage.module.css'

const NATIVE_PATIENT_SOURCE_SYSTEM = 'dhealth-clinic'
const NATIVE_PATIENT_IDENTITY_SYSTEM = 'urn:dhealth:clinic:patient-reference-id'

interface ClinicPatientsPageProps {
  pendingCreations?: Record<string, PendingPatientCreation>
  onPendingCreationChange?: (scopeKey: string, pending: PendingPatientCreation | null) => void
  pendingOrders?: Record<string, PendingOrderMutation>
  onPendingOrderChange?: (scopeKey: string, pending: PendingOrderMutation | null) => void
}

function patientLabel(patient: ClinicPatientSummary): string {
  return patient.display_name?.trim() || patient.local_reference
}

function safeErrorCode(error: unknown): ErrorCode {
  return error instanceof ExchangeApiError ? mapExchangeErrorToCode(error.code) : 'SERVER_ERROR'
}

function formatOrderStatus(status: string): { label: string; tone: StatusTone } {
  switch (status) {
    case 'AWAITING_LAB_SELECTION':
      return { label: 'En attente de choix labo', tone: 'neutral' }
    case 'SELECTED':
      return { label: 'Labo sélectionné', tone: 'neutral' }
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
    default:
      return { label: status, tone: 'neutral' }
  }
}

function formatOrderLabText(status: string): string {
  if (status === 'AWAITING_LAB_SELECTION') {
    return 'Pas encore sélectionné'
  }
  if (status === 'CANCELLED' || status === 'REJECTED') {
    return 'Indéterminé pour cet ordre clôturé'
  }
  return 'Sélectionné'
}

export function ClinicPatientsPage({
  pendingCreations = {},
  onPendingCreationChange = () => {},
  pendingOrders = {},
  onPendingOrderChange = () => {},
}: ClinicPatientsPageProps) {
  const { activeProvider, user, client } = useClinic()
  const providerId = activeProvider?.organization_id ?? null
  const scopeKey = providerId && user ? `${user.identity_id}:${providerId}` : null
  const currentProviderRef = useRef(providerId)
  const currentPatientRef = useRef<string | null>(null)
  const mountedRef = useRef(true)
  const requestGenerationRef = useRef(0)
  const ordersGenerationRef = useRef(0)
  const orderRecoveryGenerationRef = useRef(0)
  currentProviderRef.current = providerId

  const [searchInput, setSearchInput] = useState('')
  const [results, setResults] = useState<{ providerId: string; values: ClinicPatientSummary[] } | null>(null)
  const [loadState, setLoadState] = useState<'loading' | 'success' | 'error'>('loading')
  const [loadErrorCode, setLoadErrorCode] = useState<ErrorCode>('SERVER_ERROR')
  const [selected, setSelected] = useState<{ providerId: string; value: ClinicPatientSummary } | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [displayName, setDisplayName] = useState('')
  const [contactValue, setContactValue] = useState('')
  const [displayNameError, setDisplayNameError] = useState<string | null>(null)
  const [createState, setCreateState] = useState<'idle' | 'submitting' | 'uncertain'>('idle')
  const [createErrorCode, setCreateErrorCode] = useState<ErrorCode>('SERVER_ERROR')
  const [definitiveError, setDefinitiveError] = useState<string | null>(null)
  const [unresolvedStoredAttempt, setUnresolvedStoredAttempt] = useState<OpaquePendingPatientIdentity | null>(null)
  const [confirmedReadAttempt, setConfirmedReadAttempt] = useState<OpaquePendingPatientIdentity | null>(null)
  const [reconcilingStoredAttempt, setReconcilingStoredAttempt] = useState(false)
  const [storageUnreadable, setStorageUnreadable] = useState(false)

  // -- DH-CLINIC-003: Patient lab orders history state --
  const [patientOrders, setPatientOrders] = useState<LabOrder[] | null>(null)
  const [ordersLoadState, setOrdersLoadState] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [ordersErrorCode, setOrdersErrorCode] = useState<ErrorCode>('SERVER_ERROR')

  // -- DH-CLINIC-003: Order creation wizard state --
  const [showOrderWizard, setShowOrderWizard] = useState(false)
  const [orderStep, setOrderStep] = useState<'catalog' | 'review' | 'confirmation'>('catalog')
  const [catalogEntries, setCatalogEntries] = useState<CatalogEntry[] | null>(null)
  const [catalogLoadState, setCatalogLoadState] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [catalogErrorCode, setCatalogErrorCode] = useState<ErrorCode>('SERVER_ERROR')
  const [catalogSearch, setCatalogSearch] = useState('')
  const [selectedCatalogEntryIds, setSelectedCatalogEntryIds] = useState<string[]>([])
  const [orderSubmitState, setOrderSubmitState] = useState<'idle' | 'submitting' | 'uncertain' | 'error'>('idle')
  const [orderSubmitError, setOrderSubmitError] = useState<string | null>(null)
  const [orderSubmitErrorCode, setOrderSubmitErrorCode] = useState<ErrorCode>('SERVER_ERROR')
  const [orderOutcome, setOrderOutcome] = useState<LabOrderCreateOutcome | null>(null)
  const [orderDraftIdempotencyKey, setOrderDraftIdempotencyKey] = useState<string | null>(null)

  // -- DH-CLINIC-003A: Pending order reconciliation state --
  const [unresolvedStoredOrder, setUnresolvedStoredOrder] = useState<OpaquePendingOrderIdentity | null>(null)
  const [reconcilingStoredOrder, setReconcilingStoredOrder] = useState(false)
  const [orderStorageUnreadable, setOrderStorageUnreadable] = useState(false)
  const [hadPriorAmbiguousAttempt, setHadPriorAmbiguousAttempt] = useState(false)
  const [intentItemSnapshots, setIntentItemSnapshots] = useState<LabOrderIntentItem[] | null>(null)


  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const loadPatients = useCallback(async (targetProviderId: string, query = '') => {
    const generation = ++requestGenerationRef.current
    setLoadState('loading')
    try {
      const values = await client.listPatientReferences(targetProviderId, {
        query,
        limit: 50,
      })
      if (
        mountedRef.current &&
        currentProviderRef.current === targetProviderId &&
        requestGenerationRef.current === generation
      ) {
        setResults({ providerId: targetProviderId, values })
        setLoadState('success')
      }
    } catch (error) {
      if (
        mountedRef.current &&
        currentProviderRef.current === targetProviderId &&
        requestGenerationRef.current === generation
      ) {
        setResults({ providerId: targetProviderId, values: [] })
        setLoadErrorCode(safeErrorCode(error))
        setLoadState('error')
      }
    }
  }, [client])

  useEffect(() => {
    requestGenerationRef.current += 1
    setSearchInput('')
    setResults(null)
    setSelected(null)
    setLoadState('loading')
    setDisplayNameError(null)
    setDefinitiveError(null)
    setUnresolvedStoredAttempt(null)
    setConfirmedReadAttempt(null)
    setReconcilingStoredAttempt(false)
    setStorageUnreadable(false)

    // 1. In-memory pending attempt from tab switching within the same React session
    const inMemoryPending = scopeKey ? pendingCreations[scopeKey] : undefined
    setDisplayName(String(inMemoryPending?.body.display_name ?? ''))
    setContactValue(String(inMemoryPending?.body.contact_value ?? ''))
    setShowCreate(Boolean(inMemoryPending && !inMemoryPending.confirmedPatientReferenceId))
    setCreateState(inMemoryPending && !inMemoryPending.confirmedPatientReferenceId ? 'uncertain' : 'idle')
    if (inMemoryPending?.confirmedPatientReferenceId) {
      setConfirmedReadAttempt({
        source_system: inMemoryPending.body.source_system,
        source_identity_system: inMemoryPending.body.source_identity_system,
        source_identity_value: inMemoryPending.body.source_identity_value,
        confirmed_patient_reference_id: inMemoryPending.confirmedPatientReferenceId,
        created_at: Date.now(),
      })
    }

    // 2. Cross-reload recovery: check browser storage for unresolved opaque identity
    if (user && providerId) {
      if (!canReadPendingPatientStorage()) {
        setStorageUnreadable(true)
        setDefinitiveError("Impossible de verifier la recuperation sure d'une creation precedente.")
      }
      let stored: OpaquePendingPatientIdentity | null = null
      try {
        stored = getStoredPendingPatient(user.identity_id, providerId)
      } catch (error) {
        if (error instanceof PendingPatientStorageReadError) {
          setStorageUnreadable(true)
          setDefinitiveError("Impossible de verifier la recuperation sure d'une creation precedente.")
          setShowCreate(false)
        } else throw error
      }
      if (stored) {
        // Block new creation immediately. A stored UUID may identify a POST which the server
        // committed before its response was lost, so it must be reconciled exactly, never replaced.
        setReconcilingStoredAttempt(true)
        if (!stored.confirmed_patient_reference_id) setUnresolvedStoredAttempt(stored)
        void (async () => {
          let found: ClinicPatientSummary | null = null
          try {
            if (stored.confirmed_patient_reference_id) {
              found = await client.getPatientReference(
                providerId,
                stored.confirmed_patient_reference_id,
              )
            }
          } catch {
            // A read failure cannot undo a confirmed bind POST.
          }

          if (mountedRef.current && currentProviderRef.current === providerId) {
            if (found) {
              clearStoredPendingPatient(user.identity_id, providerId)
              if (scopeKey) onPendingCreationChange(scopeKey, null)
              setSelected({ providerId, value: found })
              setResults((curr) => ({
                providerId,
                values:
                  curr?.providerId === providerId
                    ? [found, ...curr.values.filter((x) => x.patient_reference_id !== found.patient_reference_id)]
                    : [found],
              }))
              setLoadState('success')
            } else if (stored.confirmed_patient_reference_id) setConfirmedReadAttempt(stored)
            else setUnresolvedStoredAttempt(stored)
            setReconcilingStoredAttempt(false)
          }
        })()
      }
    }

    if (providerId) {
      void loadPatients(providerId)
    }
    // Pending attempts are intentionally read only when the authenticated provider scope changes.
    // Updating the attempt itself must not restart the page or replace an in-flight request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId, scopeKey, loadPatients])

  const visibleResults = results?.providerId === providerId ? results.values : []
  const selectedPatient = selected?.providerId === providerId ? selected.value : null
  currentPatientRef.current = selectedPatient?.patient_reference_id ?? null
  const orderScopeKey =
    providerId && user && selectedPatient
      ? `${user.identity_id}:${providerId}:${selectedPatient.patient_reference_id}`
      : null

  const loadPatientOrders = useCallback(
    async (targetProviderId: string, targetPatientRefId: string) => {
      const generation = ++ordersGenerationRef.current
      setOrdersLoadState('loading')
      try {
        const orders = await client.listPatientOrders(targetProviderId, targetPatientRefId, {
          limit: 50,
        })
        if (
          mountedRef.current &&
          currentProviderRef.current === targetProviderId &&
          currentPatientRef.current === targetPatientRefId &&
          ordersGenerationRef.current === generation
        ) {
          setPatientOrders(orders)
          setOrdersLoadState('success')
        }
      } catch (error) {
        if (
          mountedRef.current &&
          currentProviderRef.current === targetProviderId &&
          currentPatientRef.current === targetPatientRefId &&
          ordersGenerationRef.current === generation
        ) {
          setOrdersErrorCode(safeErrorCode(error))
          setOrdersLoadState('error')
        }
      }
    },
    [client],
  )

  useEffect(() => {
    const recoveryGeneration = ++orderRecoveryGenerationRef.current
    // DH-CLINIC-003C defect fix (NC25/NC26): reconcilingStoredOrder and orderStorageUnreadable are
    // both scope-global state, but their resets to `false` only run guarded by staleness checks
    // (inside the async callback, or implicitly by never re-running) -- if the scope changes before
    // a prior scope's check completes, the newly active scope can silently inherit a stuck `true`
    // (permanently disabling "Nouvel ordre de laboratoire" for a patient/provider with no problem
    // of their own). The newly active scope must start from a clean slate every time; the checks
    // immediately below re-arm `true` synchronously if THIS scope's own storage read is genuinely
    // unreadable/corrupt, and the async mismatch guard further below re-arms it if this scope's own
    // recovered intent tuple genuinely mismatches.
    setReconcilingStoredOrder(false)
    setOrderStorageUnreadable(false)
    if (providerId && selectedPatient) {
      void loadPatientOrders(providerId, selectedPatient.patient_reference_id)

      // Rehydrate in-memory pending order for this patient if any
      const inMemoryPendingOrder = orderScopeKey ? pendingOrders[orderScopeKey] : undefined
      if (inMemoryPendingOrder) {
        setOrderDraftIdempotencyKey(inMemoryPendingOrder.idempotencyKey)
        setSelectedCatalogEntryIds(inMemoryPendingOrder.catalogEntryIds)
        setOrderSubmitState('uncertain')
        setHadPriorAmbiguousAttempt(true)
        setOrderStep('review')
        setShowOrderWizard(false)
      } else {
        setOrderDraftIdempotencyKey(null)
        setSelectedCatalogEntryIds([])
        setIntentItemSnapshots(null)
        setOrderSubmitState('idle')
        setOrderSubmitError(null)
        setHadPriorAmbiguousAttempt(false)
        setOrderOutcome(null)
        setShowOrderWizard(false)
        setOrderStep('catalog')
      }

      // Check persistent storage for cross-reload recovery
      if (user) {
        if (!canReadPendingOrderStorage()) {
          setOrderStorageUnreadable(true)
        }
        let storedOrder: OpaquePendingOrderIdentity | null = null
        try {
          storedOrder = getStoredPendingOrder(
            user.identity_id,
            providerId,
            selectedPatient.patient_reference_id,
          )
        } catch (error) {
          if (error instanceof PendingOrderStorageReadError) {
            setOrderStorageUnreadable(true)
          } else throw error
        }

        if (storedOrder) {
          setUnresolvedStoredOrder(storedOrder)
          setOrderDraftIdempotencyKey(storedOrder.idempotency_key)
          // DH-CLINIC-003B fix: Any unresolved stored order represents a prior ambiguous attempt
          setHadPriorAmbiguousAttempt(true)
          setReconcilingStoredOrder(true)
          void (async () => {
            let foundOrder: LabOrder | null = null
            try {
              foundOrder = await client.getOrderByIdempotencyKey(
                providerId,
                storedOrder.idempotency_key,
              )
            } catch {
              // Read failure during reconciliation
            }

            if (
              mountedRef.current &&
              currentProviderRef.current === providerId &&
              currentPatientRef.current === selectedPatient.patient_reference_id &&
              orderRecoveryGenerationRef.current === recoveryGeneration
            ) {
              if (
                foundOrder &&
                foundOrder.provider_organization_id === providerId &&
                foundOrder.patient_reference_id === storedOrder.patient_reference_id
              ) {
                setReconcilingStoredOrder(false)
                clearStoredPendingOrder(
                  user.identity_id,
                  providerId,
                  selectedPatient.patient_reference_id,
                )
                if (orderScopeKey) onPendingOrderChange?.(orderScopeKey, null)
                setUnresolvedStoredOrder(null)
                setOrderOutcome({ order: foundOrder, created: false })
                setOrderStep('confirmation')
                setShowOrderWizard(true)
                setPatientOrders((curr) => {
                  if (!curr) return [foundOrder]
                  if (curr.some((o) => o.id === foundOrder.id)) return curr
                  return [foundOrder, ...curr]
                })
              } else {
                // Order not found. Check if backend-owned intent exists (DH-CLINIC-003C Option A)
                let foundIntent: LabOrderIntent | null = null
                try {
                  if (storedOrder.intent_id) {
                    foundIntent = await client.getOrderIntent(providerId, storedOrder.intent_id)
                  } else {
                    foundIntent = await client.getOrderIntentByIdempotencyKey(
                      providerId,
                      storedOrder.idempotency_key,
                    )
                  }
                } catch {
                  // Intent read failure
                }

                if (
                  mountedRef.current &&
                  currentProviderRef.current === providerId &&
                  currentPatientRef.current === selectedPatient.patient_reference_id &&
                  orderRecoveryGenerationRef.current === recoveryGeneration
                ) {
                  setReconcilingStoredOrder(false)
                  if (
                    foundIntent &&
                    (foundIntent.provider_organization_id !== providerId ||
                      foundIntent.patient_reference_id !== selectedPatient.patient_reference_id ||
                      foundIntent.idempotency_key !== storedOrder.idempotency_key ||
                      (storedOrder.intent_id !== undefined && foundIntent.id !== storedOrder.intent_id))
                  ) {
                    // A syntactically valid technical pointer that resolves to another
                    // canonical tuple is unsafe.  Retain the stored record fail-closed.
                    setOrderStorageUnreadable(true)
                    setOrderSubmitState('uncertain')
                    return
                  }
                  if (foundIntent) {
                    if (foundIntent.committed_lab_order_id) {
                      let committedOrder: LabOrder | null = null
                      try {
                        committedOrder = await client.getOrder(
                          providerId,
                          foundIntent.committed_lab_order_id,
                        )
                      } catch {
                        // ignore
                      }
                      if (
                        mountedRef.current &&
                        currentProviderRef.current === providerId &&
                        currentPatientRef.current === selectedPatient.patient_reference_id &&
                        orderRecoveryGenerationRef.current === recoveryGeneration &&
                        committedOrder
                      ) {
                        clearStoredPendingOrder(
                          user.identity_id,
                          providerId,
                          selectedPatient.patient_reference_id,
                        )
                        if (orderScopeKey) onPendingOrderChange?.(orderScopeKey, null)
                        setUnresolvedStoredOrder(null)
                        setOrderOutcome({ order: committedOrder, created: false })
                        setOrderStep('confirmation')
                        setShowOrderWizard(true)
                        setPatientOrders((curr) => {
                          if (!curr) return [committedOrder]
                          if (curr.some((o) => o.id === committedOrder.id)) return curr
                          return [committedOrder, ...curr]
                        })
                        return
                      }
                    }

                    // Intent exists and is not yet committed.
                    // If intent_id was not yet in storedOrder, update persistent storage
                    if (!storedOrder.intent_id) {
                      setStoredPendingOrder(
                        user.identity_id,
                        providerId,
                        selectedPatient.patient_reference_id,
                        {
                          idempotency_key: storedOrder.idempotency_key,
                          intent_id: foundIntent.id,
                          created_at: storedOrder.created_at,
                        },
                      )
                      setUnresolvedStoredOrder({
                        ...storedOrder,
                        intent_id: foundIntent.id,
                      })
                    }

                    // Faithfully restore catalog selections strictly from backend intent!
                    // Zero catalog IDs in browser local storage.
                    setIntentItemSnapshots(foundIntent.items)
                    setSelectedCatalogEntryIds(foundIntent.items.map((item) => item.catalog_entry_id))
                    setOrderStep('review')
                    setOrderSubmitState('uncertain')
                    setHadPriorAmbiguousAttempt(true)
                  }
                }
              }
            }
          })()
        } else {
          setUnresolvedStoredOrder(null)
          setIntentItemSnapshots(null)
        }
      }
    } else {
      setPatientOrders(null)
      setOrdersLoadState('idle')
      setShowOrderWizard(false)
      setOrderOutcome(null)
      setOrderDraftIdempotencyKey(null)
      setSelectedCatalogEntryIds([])
      setIntentItemSnapshots(null)
      setOrderSubmitState('idle')
      setUnresolvedStoredOrder(null)
      setHadPriorAmbiguousAttempt(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId, selectedPatient?.patient_reference_id, loadPatientOrders])

  const loadCatalog = useCallback(async () => {
    setCatalogLoadState('loading')
    try {
      const entries = await client.listCatalogTests()
      if (mountedRef.current) {
        setCatalogEntries(entries)
        setCatalogLoadState('success')
      }
    } catch (error) {
      if (mountedRef.current) {
        setCatalogErrorCode(safeErrorCode(error))
        setCatalogLoadState('error')
      }
    }
  }, [client])

  const handleOpenOrderWizard = () => {
    if (!orderDraftIdempotencyKey) {
      setOrderDraftIdempotencyKey(crypto.randomUUID())
    }
    if (orderSubmitState !== 'uncertain' && !unresolvedStoredOrder) {
      setSelectedCatalogEntryIds([])
      setCatalogSearch('')
      setOrderSubmitState('idle')
      setOrderSubmitError(null)
      setOrderOutcome(null)
      setOrderStep('catalog')
    }
    setShowOrderWizard(true)
    if (!unresolvedStoredOrder && (catalogEntries === null || catalogLoadState !== 'success')) {
      void loadCatalog()
    }
  }

  const handleResumeUncertainOrder = () => {
    setShowOrderWizard(true)
    if (selectedCatalogEntryIds.length > 0) {
      setOrderStep('review')
    } else {
      // A durable intent is immutable.  Never revive the old Option-C path in
      // which a clinician reconstructs unknown service selections after reload.
      // The reconciliation effect must first retrieve the canonical intent.
      setOrderSubmitState('uncertain')
    }
  }

  const handleToggleService = (entryId: string) => {
    if (orderSubmitState === 'submitting') return
    if (unresolvedStoredOrder?.intent_id || orderSubmitState === 'uncertain') return
    setSelectedCatalogEntryIds((prev) =>
      prev.includes(entryId) ? prev.filter((id) => id !== entryId) : [...prev, entryId],
    )
  }

  const handleSubmitOrder = async () => {
    if (!providerId || !selectedPatient || selectedCatalogEntryIds.length === 0) return
    const targetProviderId = providerId
    const targetPatientRefId = selectedPatient.patient_reference_id
    const targetUserId = user?.identity_id ?? ''
    const currentScopeKey = orderScopeKey
    let key = orderDraftIdempotencyKey
    if (!key) {
      key = crypto.randomUUID()
      setOrderDraftIdempotencyKey(key)
    }

    // Persist-before-POST durability gate:
    // We MUST record the technical non-PHI identity tuple in persistent storage
    // and verify read-back BEFORE firing any network mutation.
    // If storage write or read-back fails, we fail closed and NEVER call POST.
    let writeOk = false
    let readBackOk = false
    if (targetUserId) {
      writeOk = setStoredPendingOrder(targetUserId, targetProviderId, targetPatientRefId, {
        idempotency_key: key,
        created_at: unresolvedStoredOrder?.created_at ?? Date.now(),
        ...(unresolvedStoredOrder?.intent_id ? { intent_id: unresolvedStoredOrder.intent_id } : {}),
      })
      try {
        const readBack = getStoredPendingOrder(targetUserId, targetProviderId, targetPatientRefId)
        readBackOk = Boolean(readBack && readBack.idempotency_key === key)
      } catch {
        readBackOk = false
      }
    }

    if (!writeOk || !readBackOk) {
      setOrderStorageUnreadable(true)
      setOrderSubmitState('idle')
      return
    }

    // Keep frozen intent in session memory for in-session review/retry
    if (currentScopeKey) {
      onPendingOrderChange?.(currentScopeKey, {
        userId: targetUserId,
        providerId: targetProviderId,
        patientReferenceId: targetPatientRefId,
        catalogEntryIds: selectedCatalogEntryIds,
        idempotencyKey: key,
        createdAt: Date.now(),
      })
    }

    setOrderSubmitState('submitting')
    setOrderSubmitError(null)

    let durableIntentRegistered = Boolean(unresolvedStoredOrder?.intent_id)
    try {
      // Step 1: Ensure backend intent exists (DH-CLINIC-003C Option A)
      let intentId = unresolvedStoredOrder?.intent_id
      if (!intentId) {
        const intentOutcome = await client.createOrderIntentWithOutcome(targetProviderId, key, {
          patient_reference_id: targetPatientRefId,
          catalog_entry_ids: selectedCatalogEntryIds,
        })
        intentId = intentOutcome.intent.id

        if (
          !mountedRef.current ||
          currentProviderRef.current !== targetProviderId ||
          currentPatientRef.current !== targetPatientRefId
        ) {
          return
        }

        // Persist intent_id to storage and verify read-back
        if (targetUserId) {
          const updatedWriteOk = setStoredPendingOrder(
            targetUserId,
            targetProviderId,
            targetPatientRefId,
            {
              idempotency_key: key,
              intent_id: intentId,
              created_at: unresolvedStoredOrder?.created_at ?? Date.now(),
            },
          )
          const readBack = getStoredPendingOrder(targetUserId, targetProviderId, targetPatientRefId)
          if (!updatedWriteOk || !readBack || readBack.intent_id !== intentId) {
            setOrderStorageUnreadable(true)
            setOrderSubmitState('uncertain')
            setHadPriorAmbiguousAttempt(true)
            return
          }
        }
        setUnresolvedStoredOrder((prev) =>
          prev
            ? { ...prev, intent_id: intentId }
            : {
                user_id: targetUserId,
                provider_id: targetProviderId,
                patient_reference_id: targetPatientRefId,
                idempotency_key: key,
                intent_id: intentId,
                created_at: Date.now(),
              },
        )
        durableIntentRegistered = true
      }

      // Step 2: Commit the backend intent
      const outcome = await client.commitOrderIntentWithOutcome(targetProviderId, intentId)

      if (
        !mountedRef.current ||
        currentProviderRef.current !== targetProviderId ||
        currentPatientRef.current !== targetPatientRefId
      ) {
        return
      }

      setOrderOutcome(outcome)
      setOrderStep('confirmation')
      setOrderSubmitState('idle')
      setHadPriorAmbiguousAttempt(false)
      setUnresolvedStoredOrder(null)
      setIntentItemSnapshots(null)

      // Immediately prepend outcome.order to local patientOrders state
      setPatientOrders((curr) => {
        if (!curr) return [outcome.order]
        if (curr.some((o) => o.id === outcome.order.id)) return curr
        return [outcome.order, ...curr]
      })

      // Authoritative commit clears pending state
      if (currentScopeKey) onPendingOrderChange?.(currentScopeKey, null)
      if (targetUserId) {
        clearStoredPendingOrder(targetUserId, targetProviderId, targetPatientRefId)
      }

      // Auxiliary background refresh (failure does NOT invalidate outcome or repeat POST)
      void loadPatientOrders(targetProviderId, targetPatientRefId)
    } catch (error) {
      if (
        !mountedRef.current ||
        currentProviderRef.current !== targetProviderId ||
        currentPatientRef.current !== targetPatientRefId
      ) {
        return
      }

      if (isDefinitiveExchangeError(error)) {
        if (error instanceof ExchangeApiError && error.status === 409) {
          setOrderSubmitState('uncertain')
          setOrderSubmitErrorCode(safeErrorCode(error))
          setOrderSubmitError(
            error.detail ||
              'Conflit d’idempotence : Cette clé a déjà été utilisée avec un contenu différent.',
          )
        } else if (!hadPriorAmbiguousAttempt && !durableIntentRegistered) {
          // If first attempt was definitively refused before any ambiguity, clear stored pending order
          if (targetUserId) {
            clearStoredPendingOrder(targetUserId, targetProviderId, targetPatientRefId)
          }
          if (currentScopeKey) onPendingOrderChange?.(currentScopeKey, null)
          setUnresolvedStoredOrder(null)
          setIntentItemSnapshots(null)
          setOrderSubmitState('error')
          setOrderSubmitErrorCode(safeErrorCode(error))
          setOrderSubmitError(
            error instanceof ExchangeApiError
              ? error.detail
              : 'Erreur lors de la création de l’ordre',
          )
        } else {
          // Definitive error on retry after earlier ambiguous attempt: keep uncertain
          setOrderSubmitState('uncertain')
          setOrderSubmitErrorCode(safeErrorCode(error))
          setOrderSubmitError(
            error instanceof ExchangeApiError
              ? error.detail
              : 'Erreur lors de la vérification de l’ordre',
          )
        }
      } else {
        // Network error / timeout -> enters uncertain state
        // Notice: non-PHI technical token was ALREADY durably persisted before POST!
        setOrderSubmitState('uncertain')
        setOrderSubmitErrorCode(safeErrorCode(error))
        setHadPriorAmbiguousAttempt(true)
      }
    }
  }

  const handleCloseOrderWizard = () => {
    setShowOrderWizard(false)
    if (
      orderSubmitState !== 'uncertain' &&
      !hadPriorAmbiguousAttempt &&
      !unresolvedStoredOrder?.intent_id
    ) {
      setOrderOutcome(null)
      setOrderDraftIdempotencyKey(null)
      setSelectedCatalogEntryIds([])
      setIntentItemSnapshots(null)
      setOrderSubmitState('idle')
      setOrderSubmitError(null)
      setOrderStep('catalog')
    }
  }



  const handleSearch = (event: FormEvent) => {
    event.preventDefault()
    if (providerId) void loadPatients(providerId, searchInput.trim())
  }

  const handleResumeStoredAttempt = () => {
    setShowCreate(true)
    setCreateState('idle')
    setDefinitiveError(null)
  }

  const handleCancelCreate = () => {
    setShowCreate(false)
    // Cancellation is presentation-only if a create is unresolved. The durable
    // source identity remains available for exact reconciliation.
    if (!unresolvedStoredAttempt && !pendingCreations[scopeKey ?? '']) {
      setDisplayName('')
      setContactValue('')
      setDisplayNameError(null)
      setDefinitiveError(null)
      setCreateState('idle')
    }
  }

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault()
    if (!providerId || !scopeKey || !user || storageUnreadable) return

    const trimmedName = displayName.trim()
    if (!trimmedName) {
      setDisplayNameError('Le nom affiché est obligatoire.')
      return
    }
    setDisplayNameError(null)
    setDefinitiveError(null)

    const existingPending = pendingCreations[scopeKey]
    const identityValue =
      existingPending?.body.source_identity_value ??
      unresolvedStoredAttempt?.source_identity_value ??
      globalThis.crypto.randomUUID()

    const body: PatientReferenceBindRequest = {
      source_system: NATIVE_PATIENT_SOURCE_SYSTEM,
      source_identity_system: NATIVE_PATIENT_IDENTITY_SYSTEM,
      source_identity_value: identityValue,
      display_name: trimmedName,
      contact_channel_type: contactValue.trim() ? 'PHONE' : null,
      contact_value: contactValue.trim() || null,
    }

    const targetProviderId = providerId
    const hadEarlierAmbiguousAttempt = Boolean(existingPending || unresolvedStoredAttempt)
    // Durability is a precondition of issuing a mutation whose response may be lost.
    if (!setStoredPendingPatient(user.identity_id, targetProviderId, {
      source_system: NATIVE_PATIENT_SOURCE_SYSTEM,
      source_identity_system: NATIVE_PATIENT_IDENTITY_SYSTEM,
      source_identity_value: identityValue,
    })) {
      setCreateErrorCode('SERVER_ERROR')
      setDefinitiveError("Impossible de preparer la recuperation sure de cette creation. Aucun envoi n'a ete effectue.")
      return
    }
    onPendingCreationChange(scopeKey, { body })
    setCreateState('submitting')
    let outcome
    try {
      outcome = await client.bindPatientReferenceWithOutcome(targetProviderId, body)
    } catch (error) {
      if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return

      if (isDefinitiveExchangeError(error) && !hadEarlierAmbiguousAttempt) {
        // This specific first POST was authoritatively refused before any ambiguity existed.
        clearStoredPendingPatient(user.identity_id, targetProviderId)
        onPendingCreationChange(scopeKey, null)
        setUnresolvedStoredAttempt(null)
        setCreateErrorCode(safeErrorCode(error))
        setDefinitiveError("Impossible d'enregistrer le patient. Veuillez vérifier les informations et vos autorisations.")
        setCreateState('idle')
      } else {
        // A later retry refusal cannot prove that an earlier ambiguous POST did not commit.
        onPendingCreationChange(scopeKey, { body })
        setCreateErrorCode(safeErrorCode(error))
        setCreateState('uncertain')
      }
      return
    }

    // Phase A is now authoritatively confirmed. Persist the returned reference before
    // Phase B so a failed read, reload, or navigation cannot turn it into a fresh create.
    const confirmed = {
      source_system: NATIVE_PATIENT_SOURCE_SYSTEM,
      source_identity_system: NATIVE_PATIENT_IDENTITY_SYSTEM,
      source_identity_value: identityValue,
      confirmed_patient_reference_id: outcome.reference.id,
    }
    if (!setStoredPendingPatient(user.identity_id, targetProviderId, confirmed)) {
      // The mutation is confirmed; retain it in memory and avoid any second create.
      onPendingCreationChange(scopeKey, { body, confirmedPatientReferenceId: outcome.reference.id })
      if (mountedRef.current && currentProviderRef.current === targetProviderId) {
        setConfirmedReadAttempt({ ...confirmed, created_at: Date.now() })
        setCreateErrorCode('SERVER_ERROR')
        setCreateState('idle')
        setShowCreate(false)
      }
      return
    }
    onPendingCreationChange(scopeKey, null)
    // The authoritative record is durable for targetProviderId. Never attach it to
    // whatever provider the clinician may have selected while the POST was in flight.
    if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return
    // Phase B is a read only. Gate creation before awaiting it so cancel/navigation
    // cannot mint another source identity while this confirmed reference is loading.
    setConfirmedReadAttempt({ ...confirmed, created_at: Date.now() })
    setShowCreate(false)
    try {
      const authoritative = await client.getPatientReference(targetProviderId, outcome.reference.id)
      if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return
      clearStoredPendingPatient(user.identity_id, targetProviderId)
      setUnresolvedStoredAttempt(null)
      setConfirmedReadAttempt(null)
      setResults((current) => ({
        providerId: targetProviderId,
        values: [authoritative, ...(current?.providerId === targetProviderId
          ? current.values.filter((patient) => patient.patient_reference_id !== authoritative.patient_reference_id)
          : [])],
      }))
      setSelected({ providerId: targetProviderId, value: authoritative })
      setLoadState('success')
      setCreateState('idle')
      setShowCreate(false)
    } catch (error) {
      if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return
      setCreateErrorCode(safeErrorCode(error))
      setCreateState('idle')
      setShowCreate(false)
    }
  }

  const handleRetryConfirmedRead = async () => {
    if (!providerId || !user || !confirmedReadAttempt?.confirmed_patient_reference_id) return
    try {
      const authoritative = await client.getPatientReference(
        providerId,
        confirmedReadAttempt.confirmed_patient_reference_id,
      )
      if (!mountedRef.current || currentProviderRef.current !== providerId) return
      clearStoredPendingPatient(user.identity_id, providerId)
      setConfirmedReadAttempt(null)
      setUnresolvedStoredAttempt(null)
      if (scopeKey) onPendingCreationChange(scopeKey, null)
      setSelected({ providerId, value: authoritative })
    } catch (error) {
      if (mountedRef.current && currentProviderRef.current === providerId) {
        setCreateErrorCode(safeErrorCode(error))
      }
    }
  }

  if (!providerId || !scopeKey) {
    return <div className={styles.page}><LoadingSkeleton /></div>
  }

  if (selectedPatient) {
    return (
      <div className={styles.page}>
        <Button
          type="button"
          variant="secondary"
          disabled={orderSubmitState === 'submitting'}
          onClick={() => {
            handleCloseOrderWizard()
            setSelected(null)
          }}
        >
          Retour aux patients
        </Button>
        <header className={styles.header}>
          <h1>Patients</h1>
          <p>{activeProvider?.organization_name}</p>
        </header>

        <Card>
          <section className={styles.workspace} aria-labelledby="patient-workspace-title">
            <h2 id="patient-workspace-title">{patientLabel(selectedPatient)}</h2>
            {selectedPatient.contact_value ? (
              <p><strong>Contact :</strong> {selectedPatient.contact_value}</p>
            ) : null}
            <p><strong>Référence patient :</strong> <code>{selectedPatient.patient_reference_id}</code></p>
            {!selectedPatient.display_name ? (
              <p><strong>Référence locale :</strong> <code>{selectedPatient.local_reference}</code></p>
            ) : null}

            {(() => {
              const hasUnresolvedOrder = Boolean(
                orderSubmitState === 'uncertain' ||
                unresolvedStoredOrder ||
                (orderScopeKey && pendingOrders[orderScopeKey])
              )
              return (
                <>
                  {hasUnresolvedOrder && !showOrderWizard ? (
                    <div className={styles.alertBanner} role="alert" style={{ marginTop: 'var(--space-3)' }}>
                      <p>
                        <strong>Tentative d’ordre non confirmée :</strong> La création de cet ordre n’a pas
                        encore pu être confirmée. Vérifiez ou réessayez cette même tentative avant de créer
                        un nouvel ordre.
                      </p>
                      <div className={styles.actions}>
                        <Button type="button" onClick={handleResumeUncertainOrder}>
                          Vérifier / réessayer
                        </Button>
                      </div>
                    </div>
                  ) : null}

                  {orderStorageUnreadable ? (
                    <div className={styles.alertBanner} role="alert" style={{ marginTop: 'var(--space-3)' }}>
                      <ErrorState code="SERVER_ERROR" />
                      <p>
                        <strong>Stockage local corrompu :</strong> Le stockage local de réconciliation d’ordre
                        est corrompu ou illisible. Pour prévenir la création d’ordres en doublon, la création
                        de nouveaux ordres est temporairement bloquée.
                      </p>
                    </div>
                  ) : null}

                  {!showOrderWizard ? (
                    <div className={styles.actions} style={{ justifyContent: 'flex-start', marginTop: 'var(--space-2)' }}>
                      <Button
                        type="button"
                        disabled={Boolean(hasUnresolvedOrder || orderStorageUnreadable || reconcilingStoredOrder)}
                        onClick={handleOpenOrderWizard}
                      >
                        Nouvel ordre de laboratoire
                      </Button>
                    </div>
                  ) : null}
                </>
              )
            })()}
          </section>
        </Card>

        {showOrderWizard ? (
          <Card>
            <div className={styles.orderWizard} aria-labelledby="order-wizard-title">
              {orderStep === 'catalog' ? (
                <>
                  <div className={styles.wizardHeader}>
                    <h2 id="order-wizard-title">Prescrire un ordre de laboratoire</h2>
                    <p className={styles.hint}>
                      Sélectionnez les services diagnostiques à prescrire pour{' '}
                      <strong>{patientLabel(selectedPatient)}</strong>.
                    </p>
                  </div>

                  <SearchInput
                    label="Filtrer les services diagnostiques"
                    value={catalogSearch}
                    maxLength={100}
                    onChange={(e) => setCatalogSearch(e.target.value)}
                  />

                  {catalogLoadState === 'loading' ? (
                    <LoadingSkeleton label="Chargement du catalogue de services…" />
                  ) : catalogLoadState === 'error' ? (
                    <ErrorState code={catalogErrorCode} onRetry={() => void loadCatalog()} />
                  ) : (
                    <>
                      {(() => {
                        const query = catalogSearch.trim().toLowerCase()
                        const filtered = (catalogEntries ?? []).filter(
                          (entry) =>
                            !query ||
                            entry.code.toLowerCase().includes(query) ||
                            entry.display_name.toLowerCase().includes(query),
                        )

                        if (filtered.length === 0) {
                          return (
                            <p className={styles.hint}>
                              Aucun service diagnostique ne correspond à votre recherche.
                            </p>
                          )
                        }

                        return (
                          <ul
                            className={styles.catalogList}
                            role="listbox"
                            aria-label="Services diagnostiques disponibles"
                          >
                            {filtered.map((entry) => {
                              const isChecked = selectedCatalogEntryIds.includes(entry.id)
                              return (
                                <li key={entry.id} className={styles.catalogItem}>
                                  <label
                                    style={{
                                      display: 'flex',
                                      alignItems: 'center',
                                      gap: 'var(--space-2)',
                                      width: '100%',
                                      cursor: 'pointer',
                                    }}
                                  >
                                    <input
                                      type="checkbox"
                                      checked={isChecked}
                                      onChange={() => handleToggleService(entry.id)}
                                    />
                                    <div className={styles.catalogItemContent}>
                                      <span className={styles.catalogCode}>{entry.code}</span>
                                      <span className={styles.catalogName}>{entry.display_name}</span>
                                    </div>
                                  </label>
                                </li>
                              )
                            })}
                          </ul>
                        )
                      })()}

                      <p className={styles.hint}>
                        {selectedCatalogEntryIds.length === 0
                          ? 'Aucun service sélectionné.'
                          : `${selectedCatalogEntryIds.length} service${selectedCatalogEntryIds.length > 1 ? 's' : ''} sélectionné${selectedCatalogEntryIds.length > 1 ? 's' : ''}.`}
                      </p>

                      <div className={styles.actions}>
                        <Button
                          type="button"
                          disabled={selectedCatalogEntryIds.length === 0}
                          onClick={() => setOrderStep('review')}
                        >
                          Suivant : Réviser l’ordre
                        </Button>
                        <Button type="button" variant="secondary" onClick={handleCloseOrderWizard}>
                          Annuler
                        </Button>
                      </div>
                    </>
                  )}
                </>
              ) : orderStep === 'review' ? (
                <>
                  <div className={styles.wizardHeader}>
                    <h2 id="order-wizard-title">Révision de l’ordre de laboratoire</h2>
                    <p className={styles.hint}>
                      Vérifiez les détails de l’ordre avant confirmation.
                    </p>
                  </div>

                  <dl className={styles.reviewSummary}>
                    <div className={styles.reviewRow}>
                      <dt>Patient</dt>
                      <dd>
                        <strong>{patientLabel(selectedPatient)}</strong> (
                        <code>{selectedPatient.patient_reference_id}</code>)
                      </dd>
                    </div>
                    <div className={styles.reviewRow}>
                      <dt>Établissement prescripteur</dt>
                      <dd>{activeProvider?.organization_name}</dd>
                    </div>
                    <div className={styles.reviewRow}>
                      <dt>Praticien prescripteur</dt>
                      <dd>{user?.display_name || user?.email || 'Praticien connecté'}</dd>
                    </div>
                    <div className={styles.reviewRow}>
                      <dt>Laboratoire</dt>
                      <dd>Pas encore sélectionné</dd>
                    </div>
                  </dl>

                  <div>
                    <h3>Services sélectionnés ({selectedCatalogEntryIds.length})</h3>
                    <ul className={styles.reviewServicesList}>
                      {selectedCatalogEntryIds.map((id) => {
                        const snapshot = intentItemSnapshots?.find((s) => s.catalog_entry_id === id)
                        const entry = (catalogEntries ?? []).find((e) => e.id === id)
                        const code = snapshot?.catalog_code_snapshot || entry?.code || id
                        const displayName =
                          snapshot?.catalog_display_name_snapshot ||
                          entry?.display_name ||
                          'Service diagnostique'
                        return (
                          <li key={id}>
                            <strong>{code}</strong> — {displayName}
                          </li>
                        )
                      })}
                    </ul>
                  </div>

                  {orderSubmitState === 'uncertain' ? (
                    <div className={styles.alertBanner} role="alert">
                      <ErrorState code={orderSubmitErrorCode} />
                      <p>
                        <strong>Tentative non confirmée :</strong> La requête n’a pas pu aboutir en raison
                        d’une interruption réseau. Réessayez pour confirmer l’ordre sans créer de doublon
                        (clé d’idempotence conservée).
                      </p>
                    </div>
                  ) : null}

                  {orderSubmitState === 'error' ? (
                    <div className={styles.createError}>
                      <ErrorState code={orderSubmitErrorCode} />
                      <p className={styles.errorText}>{orderSubmitError}</p>
                    </div>
                  ) : null}

                  <div className={styles.actions}>
                    <Button
                      type="button"
                      disabled={orderSubmitState === 'submitting'}
                      onClick={() => void handleSubmitOrder()}
                    >
                      {orderSubmitState === 'submitting'
                        ? 'Prescription en cours…'
                        : orderSubmitState === 'uncertain'
                          ? 'Vérifier / réessayer'
                          : 'Confirmer et prescrire'}
                    </Button>
                    {orderSubmitState !== 'uncertain' && !unresolvedStoredOrder ? (
                      <Button
                        type="button"
                        variant="secondary"
                        disabled={orderSubmitState === 'submitting'}
                        onClick={() => setOrderStep('catalog')}
                      >
                        Modifier les services
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={orderSubmitState === 'submitting'}
                      onClick={handleCloseOrderWizard}
                    >
                      {orderSubmitState === 'uncertain' ? 'Fermer' : 'Annuler'}
                    </Button>
                  </div>
                </>
              ) : orderStep === 'confirmation' && orderOutcome ? (
                <div className={styles.confirmationBanner} role="status">
                  <h2 className={styles.confirmationTitle}>
                    {orderOutcome.created
                      ? 'Ordre de laboratoire créé avec succès'
                      : 'Ordre de laboratoire confirmé (tentative existante récupérée sans doublon)'}
                  </h2>
                  <p>
                    <strong>Identifiant de l’ordre :</strong>{' '}
                    <code>{orderOutcome.order.id}</code>
                  </p>
                  <p>
                    <strong>Statut :</strong>{' '}
                    <StatusBadge {...formatOrderStatus(orderOutcome.order.status)} />
                  </p>
                  <p>
                    <strong>Laboratoire :</strong> {formatOrderLabText(orderOutcome.order.status)}
                  </p>
                  <p>
                    <strong>Date de prescription :</strong>{' '}
                    {new Date(orderOutcome.order.created_at).toLocaleDateString('fr-FR', {
                      year: 'numeric',
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </p>
                  <h3>Services prescrits ({orderOutcome.order.items.length})</h3>
                  <ul className={styles.orderItemList}>
                    {orderOutcome.order.items.map((item) => (
                      <li key={item.catalog_entry_id}>
                        <strong>{item.catalog_code_snapshot}</strong> —{' '}
                        {item.catalog_display_name_snapshot}
                      </li>
                    ))}
                  </ul>
                  <div className={styles.actions}>
                    <Button type="button" onClick={handleCloseOrderWizard}>
                      Fermer et afficher la fiche patient
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          </Card>
        ) : null}

        <Card>
          <section className={styles.orderSection} aria-labelledby="patient-orders-title">
            <div className={styles.orderSectionHeader}>
              <h3 id="patient-orders-title">Historique des ordres de laboratoire</h3>
            </div>

            {ordersLoadState === 'loading' ? (
              <LoadingSkeleton label="Chargement des ordres de laboratoire…" />
            ) : ordersLoadState === 'error' ? (
              <ErrorState
                code={ordersErrorCode}
                onRetry={() => void loadPatientOrders(providerId, selectedPatient.patient_reference_id)}
              />
            ) : patientOrders && patientOrders.length > 0 ? (
              <ul className={styles.orderList}>
                {patientOrders.map((order) => {
                  const { label, tone } = formatOrderStatus(order.status)
                  return (
                    <li key={order.id} className={styles.orderCard}>
                      <div className={styles.orderCardHeader}>
                        <StatusBadge label={label} tone={tone} />
                        <span className={styles.orderCardMeta}>
                          {new Date(order.created_at).toLocaleDateString('fr-FR', {
                            year: 'numeric',
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </span>
                      </div>
                      <p className={styles.orderCardMeta}>
                        <strong>Ordre :</strong> <code>{order.id}</code>
                      </p>
                      <p className={styles.orderCardMeta}>
                        <strong>Laboratoire :</strong> {formatOrderLabText(order.status)}
                      </p>
                      <div>
                        <strong>Services demandés ({order.items.length}) :</strong>
                        <ul className={styles.orderItemList}>
                          {order.items.map((item) => (
                            <li key={item.catalog_entry_id}>
                              <strong>{item.catalog_code_snapshot}</strong> —{' '}
                              {item.catalog_display_name_snapshot}
                            </li>
                          ))}
                        </ul>
                      </div>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className={styles.hint}>Aucun ordre de laboratoire enregistré pour ce patient.</p>
            )}
          </section>
        </Card>
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1>Patients</h1>
        <p>Établissement prescripteur : <strong>{activeProvider?.organization_name}</strong></p>
      </header>

      {storageUnreadable ? (
        <Card><div className={styles.createError}><ErrorState code="SERVER_ERROR" /><p className={styles.errorText}>Impossible de verifier la recuperation sure d'une creation precedente.</p></div></Card>
      ) : null}

      {confirmedReadAttempt ? (
        <Card>
          <div className={styles.alertBanner} role="alert">
            <p><strong>Patient créé :</strong> La création est confirmée, mais la fiche patient ne peut pas être ouverte pour le moment.</p>
            <Button type="button" onClick={() => void handleRetryConfirmedRead()}>Réessayer l’ouverture</Button>
          </div>
        </Card>
      ) : unresolvedStoredAttempt && !showCreate ? (
        <Card>
          <div className={styles.alertBanner} role="alert">
            <p>
              <strong>Tentative non confirmée :</strong> La création du patient n’a pas pu être confirmée. Vérifiez la tentative existante avant de créer un nouveau patient.
            </p>
            <div className={styles.actions}>
              <Button type="button" onClick={handleResumeStoredAttempt}>
                Reprendre
              </Button>
            </div>
          </div>
        </Card>
      ) : null}

      <Card>
        <form className={styles.searchForm} onSubmit={(event) => handleSearch(event)}>
          <SearchInput
            label="Rechercher un patient"
            value={searchInput}
            maxLength={100}
            onChange={(event) => setSearchInput(event.target.value)}
          />
          <Button type="submit" disabled={loadState === 'loading'}>Rechercher</Button>
        </form>
      </Card>

      {showCreate ? (
        <Card>
          <form className={styles.createForm} onSubmit={(event) => void handleCreate(event)}>
            <h2>Nouveau patient</h2>
            <div>
              <TextInput
                label="Nom affiché (obligatoire)"
                value={displayName}
                maxLength={256}
                disabled={createState !== 'idle'}
                onChange={(event) => {
                  setDisplayName(event.target.value)
                  if (displayNameError) setDisplayNameError(null)
                }}
              />
              {displayNameError ? (
                <p role="alert" className={styles.errorText}>
                  {displayNameError}
                </p>
              ) : null}
            </div>
            <TextInput
              label="Téléphone (facultatif)"
              type="tel"
              value={contactValue}
              maxLength={256}
              disabled={createState !== 'idle'}
              onChange={(event) => setContactValue(event.target.value)}
            />
            {definitiveError ? (
              <div className={styles.createError}>
                <ErrorState code={createErrorCode} />
                <p className={styles.errorText}>{definitiveError}</p>
              </div>
            ) : null}
            {createState === 'uncertain' ? (
              <div className={styles.createError}>
                <ErrorState code={createErrorCode} />
                <p>Réessayez pour vérifier la même référence sans créer un doublon.</p>
              </div>
            ) : null}
            <div className={styles.actions}>
              <Button type="submit" disabled={createState === 'submitting' || storageUnreadable}>
                {createState === 'submitting' ? 'Création…' : 'Créer et ouvrir'}
              </Button>
              <Button type="button" variant="secondary" onClick={handleCancelCreate}>
                Annuler
              </Button>
            </div>
          </form>
        </Card>
      ) : null}

      {loadState === 'loading' ? (
        <LoadingSkeleton label="Chargement des patients…" />
      ) : loadState === 'error' ? (
        <ErrorState code={loadErrorCode} onRetry={() => void loadPatients(providerId, searchInput.trim())} />
      ) : visibleResults.length === 0 ? (
        <EmptyState
          title="Aucun patient trouvé"
          action={<Button type="button" disabled={Boolean(unresolvedStoredAttempt || confirmedReadAttempt || reconcilingStoredAttempt || storageUnreadable)} onClick={() => setShowCreate(true)}>Nouveau patient</Button>}
        />
      ) : (
        <section aria-label="Résultats de recherche" className={styles.results}>
          <div className={styles.resultsHeader}>
            <h2>Patients trouvés</h2>
            <Button type="button" variant="secondary" disabled={Boolean(unresolvedStoredAttempt || confirmedReadAttempt || reconcilingStoredAttempt || storageUnreadable)} onClick={() => setShowCreate(true)}>
              Nouveau patient
            </Button>
          </div>
          <ul className={styles.patientList}>
            {visibleResults.map((patient) => (
              <li key={patient.patient_reference_id}>
                <Card>
                  <div className={styles.patientRow}>
                    <div>
                      <h3>{patientLabel(patient)}</h3>
                      {patient.contact_value ? <p>Contact : {patient.contact_value}</p> : null}
                      <p>Référence locale : <code>{patient.local_reference}</code></p>
                    </div>
                    <Button type="button" onClick={() => setSelected({ providerId, value: patient })}>Ouvrir</Button>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
