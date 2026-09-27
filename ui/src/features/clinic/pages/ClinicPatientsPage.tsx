import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ExchangeApiError,
  isDefinitiveExchangeError,
  mapExchangeErrorToCode,
} from '../../../api/exchangeClient'
import type {
  CatalogEntry,
  LabAssignmentSummary,
  LabOrder,
  LabOrderCreateOutcome,
  LabOrderIntent,
  LabOrderIntentItem,
  PatientCreateRequest,
  PatientDirectoryEntry,
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
  TextInput,
} from '../../../design-system'
import { LabRoutingStep } from '../components/LabRoutingStep'
import { PatientIdentity } from '../components/PatientIdentity'
import { formatOrderStatus } from '../orderStatusVocabulary'
import { useClinic } from '../ClinicContext'
import type { PendingOrderMutation, PendingPatientCreation } from '../../staff/StaffWorkspace'
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

interface ClinicPatientsPageProps {
  pendingCreations?: Record<string, PendingPatientCreation>
  onPendingCreationChange?: (scopeKey: string, pending: PendingPatientCreation | null) => void
  pendingOrders?: Record<string, PendingOrderMutation>
  onPendingOrderChange?: (scopeKey: string, pending: PendingOrderMutation | null) => void
  /**
   * DHEALTH-CLINIC-VISIT-001 — the clinician arrived here from an in-progress visit, to order for
   * that visit's patient.
   *
   * This page is the one that owns the ordering flow (catalogue, durable intent, uncertain-write
   * recovery, lab routing), so the visit screen hands off to it rather than forking any of that. The
   * effect is narrow: preselect this patient, and attach `visit_id` to the intent that gets created.
   * Everything else on this page behaves exactly as it does without a visit.
   */
  orderContext?: { patientId: string; visitId: string } | null
  /** Called once the hand-off has been consumed, so returning to `Patients` later is an ordinary
   * visit-free visit to the directory. */
  onOrderContextConsumed?: () => void
}

/*
 * DH-USABILITY-001 §27/§32 — the two messages a front-desk user meets when this browser cannot
 * keep the record that makes patient creation safe to retry.
 *
 * Both previously read "Impossible de verifier la recuperation sure d'une creation precedente." --
 * unaccented, in a product where French is first class, and written from the inside: "récupération
 * sûre" is the idempotency mechanism, not anything the person at the desk can act on. §32 asks
 * every error to answer three questions, and neither answered the third.
 *
 * What is stated is only what is actually known. The first message cannot claim the patient does
 * or does not exist -- that is precisely what could not be checked -- so it says what to do to
 * find out. The second can claim it, because nothing was sent, and saying so plainly is what
 * stops somebody creating the same patient twice.
 */
const PENDING_PATIENT_UNVERIFIABLE =
  'Impossible de vérifier si une création de patient faite ici auparavant a bien abouti. ' +
  'Par précaution, la création est bloquée sur cet appareil afin d’éviter un doublon. ' +
  'Recherchez le patient par son nom ci-dessous : s’il apparaît, il est déjà enregistré.'
const PENDING_PATIENT_NOT_SENT =
  'Cet appareil n’a pas pu préparer l’enregistrement. Rien n’a été envoyé : aucun patient n’a ' +
  'été créé. Réessayez ; si le message revient, utilisez un autre appareil.'

// DH-CLINIC-005 §16: a plain-text name for inline sentence use (e.g. "Sélectionnez... pour X.")
// -- the full identity banner is `<PatientIdentity>` itself, used everywhere else. Never falls
// back to a technical identifier (that fallback belongs to `<PatientIdentity>`'s own display).
function patientDisplayName(patient: PatientDirectoryEntry): string {
  return patient.display_name?.trim() || 'Nom non renseigné'
}

function safeErrorCode(error: unknown): ErrorCode {
  return error instanceof ExchangeApiError ? mapExchangeErrorToCode(error.code) : 'SERVER_ERROR'
}

// DH-CLINIC-006 §27-30: after a commit (fresh or recovered), the wizard's next step depends on
// the order's *actual* current status, never an assumption -- a freshly-created order is always
// AWAITING_LAB_SELECTION and needs routing; a recovered order may already have been routed in an
// earlier session (the response was merely lost), and must be shown as confirmed, not re-routed.
function stepForCommittedOrder(order: { status: string }): 'routing' | 'confirmation' {
  return order.status === 'AWAITING_LAB_SELECTION' ? 'routing' : 'confirmation'
}

export function ClinicPatientsPage({
  pendingCreations = {},
  onPendingCreationChange = () => {},
  pendingOrders = {},
  onPendingOrderChange = () => {},
  orderContext = null,
  onOrderContextConsumed = () => {},
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
  const [results, setResults] = useState<{ providerId: string; values: PatientDirectoryEntry[] } | null>(null)
  const [loadState, setLoadState] = useState<'loading' | 'success' | 'error'>('loading')
  const [loadErrorCode, setLoadErrorCode] = useState<ErrorCode>('SERVER_ERROR')
  const [selected, setSelected] = useState<{ providerId: string; value: PatientDirectoryEntry } | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [displayName, setDisplayName] = useState('')
  const [dateOfBirth, setDateOfBirth] = useState('')
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
  // DH-CLINIC-006 §37: which history-list order (if any) currently has its routing panel open --
  // the full LabOrder (items included) already lives in `patientOrders`, so no extra fetch is
  // needed here, unlike ClinicOrdersPage.tsx's own list-summary-only shape.
  const [historyRoutingOrderId, setHistoryRoutingOrderId] = useState<string | null>(null)

  // -- DH-CLINIC-003: Order creation wizard state --
  const [showOrderWizard, setShowOrderWizard] = useState(false)
  // DH-CLINIC-006 §27-29: 'routing' sits strictly after a successful durable commit, never before
  // -- the order already exists once this step is reached, so its own action can only be
  // retry-routing/re-read, never a second POST of the original intent.
  const [orderStep, setOrderStep] = useState<'catalog' | 'review' | 'routing' | 'confirmation'>(
    'catalog',
  )
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

  // -- DHEALTH-CLINIC-VISIT-001: the visit hand-off, if the clinician came from one --
  const [visitContext, setVisitContext] = useState<{ patientId: string; visitId: string } | null>(
    orderContext,
  )

  /**
   * The visit this order will be attached to, or `null` for an ordinary directory order.
   *
   * The *recovered* value wins over the hand-off. Once an idempotency key has been used with a
   * visit, that key belongs to that visit for good — the backend compares them on replay — so a
   * resumed attempt must present what it originally sent, not what the screen happens to hold now.
   *
   * The hand-off applies only while the selected patient is still the visit's patient. A clinician
   * who lands here from a visit and then opens somebody else's chart is ordering for that other
   * person, and silently attaching their order to the first patient's consultation would be the
   * worst possible failure this linkage could have.
   */
  const effectiveVisitId = unresolvedStoredOrder
    ? // A stored attempt wins **outright**, including when it has no visit. Written as an explicit
      // branch rather than `unresolvedStoredOrder?.visit_id ?? handOff`, which was the first version
      // and was wrong: `??` falls through on a stored attempt whose `visit_id` is absent, so an
      // ordinary directory order left in flight would be retried carrying a visit its key had never
      // been used with, and the backend's replay comparison would refuse it. The backend catching it
      // is why this was a wedged recovery path rather than a wrong record, but the recovery path is
      // the one thing this storage exists for.
      (unresolvedStoredOrder.visit_id ?? null)
    : selected && visitContext && selected.value.patient_id === visitContext.patientId
      ? visitContext.visitId
      : null

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  /**
   * DHEALTH-CLINIC-VISIT-001: consume the hand-off from an in-progress visit.
   *
   * `getPatient` rather than searching the directory listing: the listing is bounded at 50 rows and
   * the patient being seen may not be in it, which would silently drop the hand-off and leave the
   * clinician on a search box wondering why. A failure here is not fatal — the visit context is kept,
   * so selecting the patient by hand still attaches the order correctly.
   */
  useEffect(() => {
    if (!providerId || !orderContext) return
    setVisitContext(orderContext)
    let cancelled = false
    void (async () => {
      try {
        const patient = await client.getPatient(providerId, orderContext.patientId)
        if (cancelled || !mountedRef.current || currentProviderRef.current !== providerId) return
        if (patient) {
          setSelected({ providerId, value: patient })
          setResults((curr) => ({
            providerId,
            values:
              curr?.providerId === providerId
                ? [patient, ...curr.values.filter((x) => x.patient_id !== patient.patient_id)]
                : [patient],
          }))
          setLoadState('success')
        }
      } catch {
        // Left unselected on purpose. The hand-off is a convenience; nothing about the order's
        // correctness depends on this read succeeding.
      } finally {
        if (!cancelled) onOrderContextConsumed()
      }
    })()
    return () => {
      cancelled = true
    }
    // `onOrderContextConsumed` is deliberately excluded: it is a parent callback whose identity is
    // not stable, and including it would re-run the hand-off on every parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId, orderContext, client])

  const loadPatients = useCallback(async (targetProviderId: string, query = '') => {
    const generation = ++requestGenerationRef.current
    setLoadState('loading')
    try {
      const values = query.trim()
        ? await client.searchPatients(targetProviderId, query.trim(), { limit: 50 })
        : await client.listPatients(targetProviderId, { limit: 50 })
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
    setDateOfBirth(String(inMemoryPending?.body.date_of_birth ?? ''))
    setContactValue(String(inMemoryPending?.body.contact_value ?? ''))
    setShowCreate(Boolean(inMemoryPending && !inMemoryPending.confirmedPatientReferenceId))
    setCreateState(inMemoryPending && !inMemoryPending.confirmedPatientReferenceId ? 'uncertain' : 'idle')
    if (inMemoryPending?.confirmedPatientReferenceId) {
      setConfirmedReadAttempt({
        idempotency_key: inMemoryPending.idempotencyKey,
        confirmed_patient_id: inMemoryPending.confirmedPatientId,
        confirmed_patient_reference_id: inMemoryPending.confirmedPatientReferenceId,
        created_at: Date.now(),
      })
    }

    // 2. Cross-reload recovery: check browser storage for unresolved opaque identity
    if (user && providerId) {
      if (!canReadPendingPatientStorage()) {
        setStorageUnreadable(true)
        setDefinitiveError(PENDING_PATIENT_UNVERIFIABLE)
      }
      let stored: OpaquePendingPatientIdentity | null = null
      try {
        stored = getStoredPendingPatient(user.identity_id, providerId)
      } catch (error) {
        if (error instanceof PendingPatientStorageReadError) {
          setStorageUnreadable(true)
          setDefinitiveError(PENDING_PATIENT_UNVERIFIABLE)
          setShowCreate(false)
        } else throw error
      }
      if (stored) {
        // Block new creation immediately. A stored key may identify a POST which the server
        // committed before its response was lost, so it must be reconciled exactly, never replaced.
        setReconcilingStoredAttempt(true)
        if (!stored.confirmed_patient_reference_id) setUnresolvedStoredAttempt(stored)
        void (async () => {
          let found: PatientDirectoryEntry | null = null
          try {
            if (stored.confirmed_patient_id) {
              // Confirmed by an earlier successful create response -- a real, usable
              // patient_id already, no idempotency-key lookup needed. Reads the Patient
              // directory record directly (DH-CLINIC-005 AC4) -- never the legacy
              // PatientReference read, which cannot represent DOB/provider/created_at honestly.
              found = await client.getPatient(providerId, stored.confirmed_patient_id)
            } else {
              // Ambiguous: the create POST's outcome was never observed. §19's server-side
              // recovery endpoint resolves the opaque idempotency key to the authoritative
              // Patient if (and only if) the registration actually committed.
              found = await client.getPatientRegistration(providerId, stored.idempotency_key)
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
                    ? [found, ...curr.values.filter((x) => x.patient_id !== found.patient_id)]
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
  // DH-CLINIC-005 §14: durable technical recovery identity stays keyed on the exact-identity
  // PatientReference id, never rekeyed to the human-facing Patient id -- 003C's own recovery
  // invariants (intent/order idempotency, storage scoping) are untouched by this ticket.
  currentPatientRef.current = selectedPatient?.primary_patient_reference_id ?? null
  const orderScopeKey =
    providerId && user && selectedPatient
      ? `${user.identity_id}:${providerId}:${selectedPatient.primary_patient_reference_id}`
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
      void loadPatientOrders(providerId, selectedPatient.primary_patient_reference_id)

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
            selectedPatient.primary_patient_reference_id,
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
              currentPatientRef.current === selectedPatient.primary_patient_reference_id &&
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
                  selectedPatient.primary_patient_reference_id,
                )
                if (orderScopeKey) onPendingOrderChange?.(orderScopeKey, null)
                setUnresolvedStoredOrder(null)
                setOrderOutcome({ order: foundOrder, created: false })
                setOrderStep(stepForCommittedOrder(foundOrder))
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
                  currentPatientRef.current === selectedPatient.primary_patient_reference_id &&
                  orderRecoveryGenerationRef.current === recoveryGeneration
                ) {
                  setReconcilingStoredOrder(false)
                  if (
                    foundIntent &&
                    (foundIntent.provider_organization_id !== providerId ||
                      foundIntent.patient_reference_id !== selectedPatient.primary_patient_reference_id ||
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
                        currentPatientRef.current === selectedPatient.primary_patient_reference_id &&
                        orderRecoveryGenerationRef.current === recoveryGeneration &&
                        committedOrder
                      ) {
                        clearStoredPendingOrder(
                          user.identity_id,
                          providerId,
                          selectedPatient.primary_patient_reference_id,
                        )
                        if (orderScopeKey) onPendingOrderChange?.(orderScopeKey, null)
                        setUnresolvedStoredOrder(null)
                        setOrderOutcome({ order: committedOrder, created: false })
                        setOrderStep(stepForCommittedOrder(committedOrder))
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
                        selectedPatient.primary_patient_reference_id,
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
  }, [providerId, selectedPatient?.primary_patient_reference_id, loadPatientOrders])

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
    const targetPatientRefId = selectedPatient.primary_patient_reference_id
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
        // DHEALTH-CLINIC-VISIT-001: persisted *before* the POST, with the key, because the backend
        // compares `visit_id` when a key is replayed. A reload between this write and the intent
        // response would otherwise retry the same key with no visit and be refused as a conflict.
        ...(effectiveVisitId ? { visit_id: effectiveVisitId } : {}),
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
          // DHEALTH-CLINIC-VISIT-001. Omitted when there is no visit, which is the normal case for
          // an order raised straight from the patient directory. The backend freezes it on the
          // intent and copies it to the order at commit, so it is sent here and never again.
          ...(effectiveVisitId ? { visit_id: effectiveVisitId } : {}),
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
              ...(effectiveVisitId ? { visit_id: effectiveVisitId } : {}),
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
      setOrderStep(stepForCommittedOrder(outcome.order))
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

  const handleHistoryOrderAssigned = (orderId: string, assignment: LabAssignmentSummary) => {
    setPatientOrders((curr) =>
      curr
        ? curr.map((o) => (o.id === orderId ? { ...o, lab_assignment: assignment, status: 'SELECTED' } : o))
        : curr,
    )
    setHistoryRoutingOrderId(null)
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
      setDateOfBirth('')
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
    // DH-IDENTITY-001 §13/§28: a request identity only -- the server owns the authoritative
    // Patient id and its native source-identity binding. Never persisted as, or treated as, a
    // patient identity.
    const idempotencyKey =
      existingPending?.idempotencyKey ??
      unresolvedStoredAttempt?.idempotency_key ??
      globalThis.crypto.randomUUID()

    const body: PatientCreateRequest = {
      display_name: trimmedName,
      date_of_birth: dateOfBirth.trim() || null,
      contact_channel_type: contactValue.trim() ? 'PHONE' : null,
      contact_value: contactValue.trim() || null,
    }

    const targetProviderId = providerId
    const hadEarlierAmbiguousAttempt = Boolean(existingPending || unresolvedStoredAttempt)
    // Durability is a precondition of issuing a mutation whose response may be lost.
    if (!setStoredPendingPatient(user.identity_id, targetProviderId, { idempotency_key: idempotencyKey })) {
      setCreateErrorCode('SERVER_ERROR')
      setDefinitiveError(PENDING_PATIENT_NOT_SENT)
      return
    }
    onPendingCreationChange(scopeKey, { body, idempotencyKey })
    setCreateState('submitting')
    let outcome
    try {
      outcome = await client.createPatientWithOutcome(targetProviderId, idempotencyKey, body)
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
        onPendingCreationChange(scopeKey, { body, idempotencyKey })
        setCreateErrorCode(safeErrorCode(error))
        setCreateState('uncertain')
      }
      return
    }

    // Phase A is now authoritatively confirmed. Persist the returned Patient before
    // Phase B so a failed read, reload, or navigation cannot turn it into a fresh create.
    const confirmed = {
      idempotency_key: idempotencyKey,
      confirmed_patient_id: outcome.patient.patient_id,
      confirmed_patient_reference_id: outcome.patient.primary_patient_reference_id,
    }
    if (!setStoredPendingPatient(user.identity_id, targetProviderId, confirmed)) {
      // The mutation is confirmed; retain it in memory and avoid any second create.
      onPendingCreationChange(scopeKey, {
        body,
        idempotencyKey,
        confirmedPatientId: outcome.patient.patient_id,
        confirmedPatientReferenceId: outcome.patient.primary_patient_reference_id,
      })
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
    // cannot mint another registration while this confirmed patient is loading.
    setConfirmedReadAttempt({ ...confirmed, created_at: Date.now() })
    setShowCreate(false)
    try {
      // DH-CLINIC-005 AC4: reads the Patient directory record directly -- the native selected-
      // patient state is `PatientDirectoryEntry`, not the legacy `ClinicPatientSummary` shape, so
      // this read must return DOB/provider/created_at honestly rather than fabricate them.
      const authoritative = await client.getPatient(targetProviderId, outcome.patient.patient_id)
      if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return
      clearStoredPendingPatient(user.identity_id, targetProviderId)
      setUnresolvedStoredAttempt(null)
      setConfirmedReadAttempt(null)
      setResults((current) => ({
        providerId: targetProviderId,
        values: [authoritative, ...(current?.providerId === targetProviderId
          ? current.values.filter((patient) => patient.patient_id !== authoritative.patient_id)
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
    if (!providerId || !user || !confirmedReadAttempt?.confirmed_patient_id) return
    try {
      const authoritative = await client.getPatient(
        providerId,
        confirmedReadAttempt.confirmed_patient_id,
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
            {/* Visually hidden -- `<PatientIdentity>` below is the actual visible identity
                display; this exists only so the section has a real accessible landmark name. */}
            <h2 id="patient-workspace-title" className="visually-hidden">
              {patientDisplayName(selectedPatient)}
            </h2>
            <PatientIdentity patient={selectedPatient} variant="full" />
            {/* §6/§37: the legacy PatientReference id remains available for backend/integration
                correlation, visually secondary to the Patient identity above it. */}
            <p className={styles.hint}>
              Réf. PatientReference (intégration) :{' '}
              <code>{selectedPatient.primary_patient_reference_id}</code>
            </p>

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
                      {/*
                        DH-USABILITY-001 §27/§32: "Stockage local de réconciliation d'ordre" named
                        the mechanism rather than the situation, and left the reader with nothing
                        to do. The fact and the consequence are unchanged; what is added is the
                        third answer -- what happens now.
                      */}
                      <p>
                        <strong>Création d’ordres bloquée sur cet appareil :</strong> DHealth ne peut
                        plus vérifier ici si un ordre envoyé précédemment est bien arrivé. Pour éviter
                        un doublon, la création de nouveaux ordres est suspendue sur cet appareil. Les
                        ordres déjà envoyés ne sont pas affectés ; utilisez un autre appareil pour en
                        créer de nouveaux.
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
                      <strong>{patientDisplayName(selectedPatient)}</strong>.
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
                        <PatientIdentity patient={selectedPatient} />
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
              ) : orderStep === 'routing' && orderOutcome && providerId ? (
                <div>
                  {/* DH-CLINIC-006 §27-29: the order already exists at this point (durable commit
                      succeeded) -- this truthful line, never "La prescription n'a pas été créée.",
                      is why this step's only actions are retry-routing / re-read, never a second
                      POST of the original order intent. */}
                  <p className={styles.orderCardMeta} role="status">
                    L'ordre a été créé mais n'a pas encore été envoyé à un laboratoire.
                  </p>
                  <LabRoutingStep
                    key={orderOutcome.order.id}
                    client={client}
                    providerId={providerId}
                    orderId={orderOutcome.order.id}
                    patient={selectedPatient}
                    items={orderOutcome.order.items}
                    onAssigned={(assignment) => {
                      const assignedOrderId = orderOutcome.order.id
                      setOrderOutcome((prev) =>
                        prev
                          ? {
                              ...prev,
                              order: { ...prev.order, lab_assignment: assignment, status: 'SELECTED' },
                            }
                          : prev,
                      )
                      setPatientOrders((curr) =>
                        curr
                          ? curr.map((o) =>
                              o.id === assignedOrderId
                                ? { ...o, lab_assignment: assignment, status: 'SELECTED' }
                                : o,
                            )
                          : curr,
                      )
                      setOrderStep('confirmation')
                    }}
                  />
                </div>
              ) : orderStep === 'confirmation' && orderOutcome ? (
                <div className={styles.confirmationBanner} role="status">
                  <h2 className={styles.confirmationTitle}>
                    {orderOutcome.created
                      ? 'Ordre de laboratoire créé avec succès'
                      : 'Ordre de laboratoire confirmé (tentative existante récupérée sans doublon)'}
                  </h2>
                  {/* DH-CLINIC-005 §26: confirmation must not show only "success" -- the patient
                      this order was placed for stays visible, catching a wrong-patient selection
                      immediately. */}
                  <PatientIdentity patient={selectedPatient} />
                  <p>
                    <strong>Identifiant de l’ordre :</strong>{' '}
                    <code>{orderOutcome.order.id}</code>
                  </p>
                  <p>
                    <strong>Statut :</strong>{' '}
                    <StatusBadge {...formatOrderStatus(orderOutcome.order.status)} />
                  </p>
                  <p>
                    <strong>Laboratoire :</strong>{' '}
                    {orderOutcome.order.lab_assignment?.lab_organization_name ?? 'Non attribué'}
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
                onRetry={() => void loadPatientOrders(providerId, selectedPatient.primary_patient_reference_id)}
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
                        <strong>Laboratoire :</strong>{' '}
                        {order.lab_assignment?.lab_organization_name ?? 'Non attribué'}
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
                      {/* DH-CLINIC-006 §37: closes the recovery path for an interrupted wizard/
                          reload/failed assignment/navigation-away, without forcing the clinician
                          back through the whole new-order flow. */}
                      {order.status === 'AWAITING_LAB_SELECTION' ? (
                        historyRoutingOrderId === order.id ? (
                          <div style={{ marginTop: 'var(--space-2)' }}>
                            <div style={{ marginBottom: 'var(--space-2)' }}>
                              <Button
                                type="button"
                                variant="secondary"
                                onClick={() => setHistoryRoutingOrderId(null)}
                              >
                                Fermer
                              </Button>
                            </div>
                            {providerId ? (
                              <LabRoutingStep
                                key={order.id}
                                client={client}
                                providerId={providerId}
                                orderId={order.id}
                                patient={order.patient}
                                items={order.items}
                                onAssigned={(assignment) => handleHistoryOrderAssigned(order.id, assignment)}
                              />
                            ) : null}
                          </div>
                        ) : (
                          <div style={{ marginTop: 'var(--space-2)' }}>
                            <Button
                              type="button"
                              variant="secondary"
                              onClick={() => setHistoryRoutingOrderId(order.id)}
                              aria-label={`Choisir un laboratoire pour ${order.patient.display_name?.trim() || 'Nom non renseigné'}`}
                            >
                              Choisir un laboratoire
                            </Button>
                          </div>
                        )
                      ) : null}
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
        <Card><div className={styles.createError}><ErrorState code="SERVER_ERROR" /><p className={styles.errorText}>{PENDING_PATIENT_UNVERIFIABLE}</p></div></Card>
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
                label="Nom du patient (obligatoire)"
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
              label="Date de naissance (facultatif)"
              type="date"
              value={dateOfBirth}
              disabled={createState !== 'idle'}
              onChange={(event) => setDateOfBirth(event.target.value)}
            />
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
              <li key={patient.patient_id}>
                <Card>
                  <div className={styles.patientRow}>
                    <PatientIdentity patient={patient} />
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
