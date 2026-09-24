import { useCallback, useEffect, useRef, useState } from 'react'
import type { Amendment, LabOrderStatus, LabWorkflowOrder, ResultEntryWorkspace } from '../../../api/exchangeTypes'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  StatusBadge,
  type StatusTone,
} from '../../../design-system'
import { AmendmentWorkspace } from '../components/AmendmentWorkspace'
import { LabPatientIdentity } from '../components/LabPatientIdentity'
import { NativeResultEntryForm } from '../components/NativeResultEntryForm'
import { ReleaseVersionHistory } from '../components/ReleaseVersionHistory'
import { useLab } from '../LabContext'
import { PANEL_COLUMNS } from '../panelLayout'

// DH-LAB-001 §29: SELECTED does NOT mean accepted -- preserve that distinction textually, never
// by color/icon alone.
function formatOrderStatusLabel(status: LabOrderStatus): string {
  switch (status) {
    case 'SELECTED':
      return 'Nouvel ordre — à accepter'
    case 'ACCEPTED':
      return 'Accepté'
    case 'IN_PROGRESS':
      return 'En cours'
    case 'COMPLETED':
      return 'Résultats libérés'
    case 'REJECTED':
      return 'Refusé'
    case 'CANCELLED':
      return 'Annulé'
    default:
      return status
  }
}

function orderStatusTone(status: LabOrderStatus): StatusTone {
  switch (status) {
    case 'SELECTED':
      return 'neutral'
    case 'ACCEPTED':
    case 'IN_PROGRESS':
      return 'warning'
    case 'COMPLETED':
      return 'success'
    case 'REJECTED':
    case 'CANCELLED':
      return 'danger'
    default:
      return 'neutral'
  }
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleDateString('fr-FR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// DH-LAB-001 §38: a fixed, generic message -- never interpolated with the order/lab ids that
// disagreed (mirrors DH-CLINIC-007 §57's identical discipline).
const ORDER_MISMATCH_ERROR = 'Impossible de vérifier de manière sûre cet ordre.'
const ORDER_LOAD_ERROR = "Impossible de charger le détail de cet ordre."
const WORKSPACE_LOAD_ERROR = "Impossible de charger la structure des résultats pour cet ordre."
const MUTATION_UNCONFIRMED_ERROR = "L'action n'a pas pu être confirmée. Vous pouvez réessayer."
const MUTATION_CONFLICT_ERROR =
  "Cet ordre a déjà été traité différemment -- l'état actuel est affiché ci-dessous."
// DH-LAB-002 §26 Case C: start-processing's own fourth, distinct recovery outcome -- the
// milestone this transition requires was never recorded, so (per §26/§48) no retry is offered for
// *this* action; the panel's own status/collection-driven render falls back to offering "Enregistrer
// la collecte" instead, once the authoritative recheck below updates it.
const PROCESSING_PREREQUISITE_ERROR =
  "La collecte n'a pas été enregistrée dans DHealth -- l'analyse ne peut pas commencer avant."

const QUEUE_PAGE_LIMIT = 100

type QueueFilter = 'SELECTED' | 'ACCEPTED' | 'IN_PROGRESS' | 'COMPLETED' | 'REJECTED' | 'ALL'

const FILTER_OPTIONS: { id: QueueFilter; label: string }[] = [
  { id: 'SELECTED', label: 'Nouveaux' },
  { id: 'ACCEPTED', label: 'Acceptés' },
  { id: 'IN_PROGRESS', label: 'En cours' },
  { id: 'COMPLETED', label: 'Terminés' },
  { id: 'REJECTED', label: 'Refusés' },
  { id: 'ALL', label: 'Tous' },
]

interface PanelState {
  orderId: string
  loading: boolean
  error: string | null
  order: LabWorkflowOrder | null
}

interface WorkspaceState {
  orderId: string
  loading: boolean
  error: string | null
  workspace: ResultEntryWorkspace | null
}

interface AmendmentDiscoveryState {
  orderId: string
  loading: boolean
  error: string | null
  amendments: Amendment[] | null
}

const AMENDMENT_DISCOVERY_ERROR = 'Impossible de vérifier si un amendement est en cours pour cet ordre.'

export function LabOrdersPage() {
  const { status: authStatus, activeLab, capabilities, client } = useLab()

  const [filter, setFilter] = useState<QueueFilter>('SELECTED')
  const [queueState, setQueueState] = useState<{
    entries: LabWorkflowOrder[]
    loading: boolean
    error: string | null
  }>({ entries: [], loading: true, error: null })
  const [panel, setPanel] = useState<PanelState | null>(null)
  const [workspaceState, setWorkspaceState] = useState<WorkspaceState | null>(null)
  const [amendmentDiscovery, setAmendmentDiscovery] = useState<AmendmentDiscoveryState | null>(null)
  const [startAmendmentState, setStartAmendmentState] = useState<{ submitting: boolean }>({
    submitting: false,
  })
  const [mutationState, setMutationState] = useState<{
    action: 'accept' | 'reject' | 'collect' | 'start' | null
    submitting: boolean
    error: string | null
  }>({ action: null, submitting: false, error: null })
  // DH-LAB-002 §15/§19/§44, extended by DH-LAB-005 §25: one confirmation slot for whichever
  // deliberate action is pending -- reject, collection, start-processing, and starting an
  // amendment are all mutually exclusive, and the panel stays the single confirmation surface.
  const [confirming, setConfirming] = useState<'reject' | 'collect' | 'start' | 'start-amendment' | null>(
    null,
  )

  // DH-LAB-001 §39-42: same generation-ref/mounted-ref/tenant-ref stale-response discipline
  // DH-CLINIC-005/007 already established, extended here across the lab-switch dimension.
  const queueGenerationRef = useRef(0)
  const panelGenerationRef = useRef(0)
  const mountedRef = useRef(true)
  const currentLabRef = useRef<string | null>(activeLab?.organization_id ?? null)
  currentLabRef.current = activeLab?.organization_id ?? null

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // DH-LAB-001 §12/§39: a lab switch invalidates every outstanding request and clears every
  // piece of lab-scoped state in the SAME render pass -- never merely `loading: true` (that
  // alone let a stale provider's rows render under a new tenant's heading in an earlier ticket;
  // see DH-CLINIC-007's own post-hoc fix). Render-phase, not an effect, so the old lab's queue
  // rows are gone from the very next paint, never waiting for the new lab's request to resolve.
  const [prevLabId, setPrevLabId] = useState<string | null>(activeLab?.organization_id ?? null)
  if (activeLab && activeLab.organization_id !== prevLabId) {
    setPrevLabId(activeLab.organization_id)
    setPanel(null)
    setWorkspaceState(null)
    setAmendmentDiscovery(null)
    setStartAmendmentState({ submitting: false })
    setMutationState({ action: null, submitting: false, error: null })
    setConfirming(null)
    setQueueState({ entries: [], loading: true, error: null })
    queueGenerationRef.current += 1
    panelGenerationRef.current += 1
  }

  const loadQueue = useCallback(
    (statusFilter: QueueFilter) => {
      if (authStatus !== 'ready' || !activeLab || !capabilities.canWorkOrders) return
      const generation = ++queueGenerationRef.current
      const targetLab = activeLab.organization_id
      const controller = new AbortController()

      setQueueState((prev) => ({ entries: prev.entries, loading: true, error: null }))
      client
        .listLabOrders(targetLab, {
          status: statusFilter === 'ALL' ? undefined : statusFilter,
          limit: QUEUE_PAGE_LIMIT,
          signal: controller.signal,
        })
        .then((entries) => {
          if (!mountedRef.current) return
          if (queueGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setQueueState({ entries, loading: false, error: null })
        })
        .catch(() => {
          if (controller.signal.aborted) return
          if (!mountedRef.current) return
          if (queueGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setQueueState({ entries: [], loading: false, error: 'Erreur lors du chargement des ordres.' })
        })
      return () => controller.abort()
    },
    [client, activeLab, authStatus, capabilities.canWorkOrders],
  )

  useEffect(() => {
    return loadQueue(filter)
  }, [loadQueue, filter])

  const loadPanel = useCallback(
    (orderId: string, options: { isRecheck?: boolean } = {}) => {
      if (!activeLab) return
      const generation = ++panelGenerationRef.current
      const targetLab = activeLab.organization_id
      const controller = new AbortController()

      setPanel((prev) => ({
        orderId,
        loading: true,
        error: null,
        order: options.isRecheck && prev?.orderId === orderId ? prev.order : null,
      }))
      client
        .getLabOrder(targetLab, orderId, { signal: controller.signal })
        .then((order) => {
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          // §38: the response's own order_id must match what was requested before ever being
          // displayed -- fail closed, never silently render a different order's detail.
          if (order.order_id !== orderId) {
            setPanel({ orderId, loading: false, error: ORDER_MISMATCH_ERROR, order: null })
            return
          }
          setPanel({ orderId, loading: false, error: null, order })
        })
        .catch(() => {
          if (controller.signal.aborted) return
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setPanel({ orderId, loading: false, error: ORDER_LOAD_ERROR, order: null })
        })
      return () => controller.abort()
    },
    [client, activeLab],
  )

  // DH-LAB-003 §34/§47/§48, extended by DH-LAB-004: a third request in the panel's own
  // lifecycle, deliberately sharing `panelGenerationRef` rather than a dedicated ref/counter of
  // its own -- this workspace is a read that only ever makes sense *for the order currently
  // occupying the panel*, so any event that already invalidates the panel (opening a different
  // order, closing it, switching labs) must invalidate this exactly the same way, with no
  // separate bookkeeping that could drift out of sync. The generation is read here, never
  // incremented -- incrementing would wrongly invalidate whatever `loadPanel` call is
  // concurrently in flight for the very order this workspace belongs to. Also re-run after every
  // save/completion (§71 authoritative re-read discipline) via `NativeResultEntryForm`'s own
  // `onChanged` callback below.
  const loadWorkspace = useCallback(
    (orderId: string) => {
      if (!activeLab) return
      const generation = panelGenerationRef.current
      const targetLab = activeLab.organization_id
      const controller = new AbortController()

      setWorkspaceState({ orderId, loading: true, error: null, workspace: null })
      client
        .getResultEntryWorkspace(targetLab, orderId, { signal: controller.signal })
        .then((workspace) => {
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          if (workspace.order_id !== orderId) {
            setWorkspaceState({ orderId, loading: false, error: ORDER_MISMATCH_ERROR, workspace: null })
            return
          }
          setWorkspaceState({ orderId, loading: false, error: null, workspace })
        })
        .catch(() => {
          if (controller.signal.aborted) return
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setWorkspaceState({ orderId, loading: false, error: WORKSPACE_LOAD_ERROR, workspace: null })
        })
      return () => controller.abort()
    },
    [client, activeLab],
  )

  // DH-LAB-003 §35, extended by DH-LAB-004: the workspace is meaningful only while the panel's
  // own order is IN_PROGRESS -- loading it here (derived from the currently displayed order)
  // rather than from any mutation handler means it also loads correctly after a start-processing
  // success moves an order into IN_PROGRESS via its own recheck, with no separate wiring needed.
  useEffect(() => {
    if (panel?.order?.status === 'IN_PROGRESS' && panel.orderId) {
      return loadWorkspace(panel.orderId)
    }
    setWorkspaceState(null)
  }, [panel?.order?.status, panel?.orderId, loadWorkspace])

  // DH-LAB-005 §27-28: amendment discovery is meaningful only while the panel's own order is
  // COMPLETED -- a PREPARING amendment must survive a page refresh/browser close, so this is a
  // GET against the server on every panel open, never dependent on local/browser memory of
  // having started one. Shares `panelGenerationRef` for the identical reason `loadWorkspace` does.
  const loadAmendmentDiscovery = useCallback(
    (orderId: string) => {
      if (!activeLab) return
      const generation = panelGenerationRef.current
      const targetLab = activeLab.organization_id
      const controller = new AbortController()

      setAmendmentDiscovery({ orderId, loading: true, error: null, amendments: null })
      client
        .listLabOrderAmendments(targetLab, orderId, { signal: controller.signal })
        .then((response) => {
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          if (response.order_id !== orderId) {
            setAmendmentDiscovery({ orderId, loading: false, error: ORDER_MISMATCH_ERROR, amendments: null })
            return
          }
          setAmendmentDiscovery({ orderId, loading: false, error: null, amendments: response.amendments })
        })
        .catch(() => {
          if (controller.signal.aborted) return
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setAmendmentDiscovery({ orderId, loading: false, error: AMENDMENT_DISCOVERY_ERROR, amendments: null })
        })
      return () => controller.abort()
    },
    [client, activeLab],
  )

  useEffect(() => {
    if (panel?.order?.status === 'COMPLETED' && panel.orderId) {
      return loadAmendmentDiscovery(panel.orderId)
    }
    setAmendmentDiscovery(null)
  }, [panel?.order?.status, panel?.orderId, loadAmendmentDiscovery])

  const handleOpenOrder = (orderId: string) => {
    if (panel?.orderId === orderId) return
    setMutationState({ action: null, submitting: false, error: null })
    setConfirming(null)
    setWorkspaceState(null)
    setAmendmentDiscovery(null)
    setStartAmendmentState({ submitting: false })
    loadPanel(orderId)
  }

  const handleClosePanel = () => {
    // §41: bump the generation even though loadPanel isn't called here -- otherwise a mutation
    // started before this close (see runMutation below) would still find its captured generation
    // current and resurrect the just-closed panel once its response/recheck resolves.
    panelGenerationRef.current += 1
    setPanel(null)
    setWorkspaceState(null)
    setAmendmentDiscovery(null)
    setStartAmendmentState({ submitting: false })
    setMutationState({ action: null, submitting: false, error: null })
    setConfirming(null)
  }

  // DH-LAB-005 §25: no `targetStatus` recovery predicate here (unlike accept/reject/processing) --
  // starting an amendment never changes `LabOrder.status` at all (it stays COMPLETED). Success is
  // judged the same way collection's own milestone-based recovery is: by re-reading the
  // authoritative amendment discovery list and finding a PREPARING entry, never by inferring it
  // from any response shape.
  const runStartAmendment = useCallback(async () => {
    if (!activeLab || !panel?.order) return
    const orderId = panel.order.order_id
    const targetLab = activeLab.organization_id
    const mutationGeneration = panelGenerationRef.current
    setStartAmendmentState({ submitting: true })
    try {
      await client.startLabOrderAmendment(targetLab, orderId)
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      setConfirming(null)
      setStartAmendmentState({ submitting: false })
      if (panelGenerationRef.current === mutationGeneration) {
        loadAmendmentDiscovery(orderId)
      }
    } catch {
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      setConfirming(null)
      // Ambiguous (network/5xx) or an `AmendmentAlreadyInProgress` conflict (a concurrent tech
      // already started one) -- re-read authoritative discovery either way, never a blind retry.
      if (panelGenerationRef.current === mutationGeneration) {
        loadAmendmentDiscovery(orderId)
      }
      setStartAmendmentState({ submitting: false })
    }
  }, [activeLab, panel, client, loadAmendmentDiscovery])

  // DH-LAB-001 §36/§37/§41: an ambiguous mutation response (network failure, 5xx -- "network
  // failure is not proof of failure") never gets blindly retried. It re-reads authoritative
  // state directly and follows the exact three-way rule both sections specify: the target status
  // was actually reached -> show success, no error; the order is still SELECTED -> genuinely
  // unconfirmed, explicit retry stays available; the OPPOSITE terminal was reached by a
  // concurrent caller -> a real conflict, never silently presented as "unconfirmed."
  const runMutation = useCallback(
    async (action: 'accept' | 'reject') => {
      if (!activeLab || !panel?.order) return
      const orderId = panel.order.order_id
      const targetLab = activeLab.organization_id
      const targetStatus = action === 'accept' ? 'ACCEPTED' : 'REJECTED'
      // §41: bound to the panel's generation at the moment the mutation was launched. If the
      // user navigates to a different order (or closes the panel) before this resolves,
      // panelGenerationRef advances past this value and every check below bails out of touching
      // panel/mutation state -- a late accept/reject response must never mutate whatever order
      // is now actually visible. The queue refresh alone stays safe to run regardless, since it
      // isn't bound to any single order.
      const mutationGeneration = panelGenerationRef.current
      setMutationState({ action, submitting: true, error: null })
      try {
        const response =
          action === 'accept'
            ? await client.acceptLabOrder(targetLab, orderId)
            : await client.rejectLabOrder(targetLab, orderId)
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) {
          loadQueue(filter)
          return
        }
        if (response.order_id !== orderId) {
          setMutationState({ action, submitting: false, error: ORDER_MISMATCH_ERROR })
        } else {
          setMutationState({ action: null, submitting: false, error: null })
        }
        setConfirming(null)
        loadPanel(orderId, { isRecheck: true })
        loadQueue(filter)
      } catch {
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) {
          loadQueue(filter)
          return
        }
        setConfirming(null)
        try {
          const authoritative = await client.getLabOrder(targetLab, orderId)
          if (!mountedRef.current || currentLabRef.current !== targetLab) return
          if (panelGenerationRef.current !== mutationGeneration) {
            loadQueue(filter)
            return
          }
          if (authoritative.order_id !== orderId) {
            setMutationState({ action, submitting: false, error: ORDER_MISMATCH_ERROR })
          } else if (authoritative.status === targetStatus) {
            setMutationState({ action: null, submitting: false, error: null })
          } else if (authoritative.status === 'SELECTED') {
            setMutationState({ action, submitting: false, error: MUTATION_UNCONFIRMED_ERROR })
          } else {
            setMutationState({ action, submitting: false, error: MUTATION_CONFLICT_ERROR })
          }
          if (authoritative.order_id === orderId) {
            setPanel({ orderId, loading: false, error: null, order: authoritative })
          }
          loadQueue(filter)
        } catch {
          if (!mountedRef.current || currentLabRef.current !== targetLab) return
          if (panelGenerationRef.current !== mutationGeneration) return
          setMutationState({ action, submitting: false, error: MUTATION_UNCONFIRMED_ERROR })
        }
      }
    },
    [activeLab, panel, client, loadPanel, loadQueue, filter],
  )

  // DH-LAB-002 §25/§27/§28: collection's own recovery predicate is deliberately NOT the same
  // three-way shape as accept/reject's `targetStatus` equality above -- there is no
  // `targetStatus` for a milestone that never changes `LabOrder.status` at all. Success is judged
  // by `collection_recorded_at` being non-null, never by inferring it from `status`.
  const runCollection = useCallback(async () => {
    if (!activeLab || !panel?.order) return
    const orderId = panel.order.order_id
    const targetLab = activeLab.organization_id
    const mutationGeneration = panelGenerationRef.current
    setMutationState({ action: 'collect', submitting: true, error: null })
    try {
      const response = await client.recordLabOrderCollection(targetLab, orderId)
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      if (panelGenerationRef.current !== mutationGeneration) {
        loadQueue(filter)
        return
      }
      // §23: the collection response carries no status of its own -- only `order_id` is checked
      // against what was requested before ever treating this as success.
      if (response.order_id !== orderId) {
        setMutationState({ action: 'collect', submitting: false, error: ORDER_MISMATCH_ERROR })
      } else {
        setMutationState({ action: null, submitting: false, error: null })
      }
      setConfirming(null)
      loadPanel(orderId, { isRecheck: true })
      loadQueue(filter)
    } catch {
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      if (panelGenerationRef.current !== mutationGeneration) {
        loadQueue(filter)
        return
      }
      setConfirming(null)
      try {
        const authoritative = await client.getLabOrder(targetLab, orderId)
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) {
          loadQueue(filter)
          return
        }
        if (authoritative.order_id !== orderId) {
          setMutationState({ action: 'collect', submitting: false, error: ORDER_MISMATCH_ERROR })
        } else if (authoritative.collection_recorded_at !== null) {
          // §25 Case A: the milestone genuinely exists now -- success, no error, never a second
          // POST (this branch never calls `recordLabOrderCollection` again).
          setMutationState({ action: null, submitting: false, error: null })
        } else if (authoritative.status === 'ACCEPTED') {
          // §25 Case B: still ACCEPTED with no milestone -- genuinely unconfirmed, explicit retry
          // stays available (the "Enregistrer la collecte" button remains offered below).
          setMutationState({ action: 'collect', submitting: false, error: MUTATION_UNCONFIRMED_ERROR })
        } else {
          // §25 Case C: the order moved on (e.g. rejected/cancelled) with no milestone ever
          // recorded -- a real conflict, never fabricated collection history.
          setMutationState({ action: 'collect', submitting: false, error: MUTATION_CONFLICT_ERROR })
        }
        if (authoritative.order_id === orderId) {
          setPanel({ orderId, loading: false, error: null, order: authoritative })
        }
        loadQueue(filter)
      } catch {
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) return
        setMutationState({ action: 'collect', submitting: false, error: MUTATION_UNCONFIRMED_ERROR })
      }
    }
  }, [activeLab, panel, client, loadPanel, loadQueue, filter])

  // DH-LAB-002 §26/§28: processing's own recovery predicate has a genuine fourth branch accept/
  // reject/collection do not: "prerequisite not met" (ACCEPTED with no collection milestone) is
  // distinct from "unconfirmed, retry available" (ACCEPTED with a milestone already present).
  const runProcessing = useCallback(async () => {
    if (!activeLab || !panel?.order) return
    const orderId = panel.order.order_id
    const targetLab = activeLab.organization_id
    const mutationGeneration = panelGenerationRef.current
    setMutationState({ action: 'start', submitting: true, error: null })
    try {
      const response = await client.startProcessingLabOrder(targetLab, orderId)
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      if (panelGenerationRef.current !== mutationGeneration) {
        loadQueue(filter)
        return
      }
      // §24: both the responding order_id AND the resulting status must match what was expected
      // before ever rendering success.
      if (response.order_id !== orderId) {
        setMutationState({ action: 'start', submitting: false, error: ORDER_MISMATCH_ERROR })
      } else if (response.status !== 'IN_PROGRESS') {
        setMutationState({ action: 'start', submitting: false, error: MUTATION_CONFLICT_ERROR })
      } else {
        setMutationState({ action: null, submitting: false, error: null })
      }
      setConfirming(null)
      loadPanel(orderId, { isRecheck: true })
      loadQueue(filter)
    } catch {
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      if (panelGenerationRef.current !== mutationGeneration) {
        loadQueue(filter)
        return
      }
      setConfirming(null)
      try {
        const authoritative = await client.getLabOrder(targetLab, orderId)
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) {
          loadQueue(filter)
          return
        }
        if (authoritative.order_id !== orderId) {
          setMutationState({ action: 'start', submitting: false, error: ORDER_MISMATCH_ERROR })
        } else if (authoritative.status === 'IN_PROGRESS') {
          // §26 Case A: transition genuinely committed.
          setMutationState({ action: null, submitting: false, error: null })
        } else if (authoritative.status === 'ACCEPTED' && authoritative.collection_recorded_at !== null) {
          // §26 Case B: still ACCEPTED, milestone present -- unconfirmed, explicit retry stays
          // available (the "Commencer l'analyse" button remains offered below).
          setMutationState({ action: 'start', submitting: false, error: MUTATION_UNCONFIRMED_ERROR })
        } else if (authoritative.status === 'ACCEPTED' && authoritative.collection_recorded_at === null) {
          // §26 Case C: the prerequisite itself is unmet -- never auto-retried, and the panel's
          // own status/collection-driven render (below) naturally offers "Enregistrer la
          // collecte" instead once `authoritative` replaces `panel.order`.
          setMutationState({
            action: 'start',
            submitting: false,
            error: PROCESSING_PREREQUISITE_ERROR,
          })
        } else {
          // §26 Case D: a terminal status was reached by a concurrent caller -- real conflict.
          setMutationState({ action: 'start', submitting: false, error: MUTATION_CONFLICT_ERROR })
        }
        if (authoritative.order_id === orderId) {
          setPanel({ orderId, loading: false, error: null, order: authoritative })
        }
        loadQueue(filter)
      } catch {
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) return
        setMutationState({ action: 'start', submitting: false, error: MUTATION_UNCONFIRMED_ERROR })
      }
    }
  }, [activeLab, panel, client, loadPanel, loadQueue, filter])

  if (authStatus === 'loading') {
    return <LoadingSkeleton />
  }

  if (authStatus === 'no_labs') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState title="Aucun laboratoire actif">
          Aucun laboratoire actif n'est associé à votre compte.
        </EmptyState>
      </div>
    )
  }

  if (!capabilities.canWorkOrders) {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState title="File de travail non disponible">
          Les fonctions de validation/libération des résultats seront disponibles dans la
          prochaine étape.
        </EmptyState>
      </div>
    )
  }

  return (
    <div style={{ padding: 'var(--space-5)', maxWidth: '1100px', margin: '0 auto' }}>
      <header style={{ marginBottom: 'var(--space-4)' }}>
        <h1 style={{ margin: 0, fontSize: 'var(--font-size-2xl)', color: 'var(--color-neutral-900)' }}>
          Ordres de laboratoire
        </h1>
      </header>

      <div
        role="tablist"
        aria-label="Filtrer les ordres par statut"
        style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}
      >
        {FILTER_OPTIONS.map((option) => (
          <button
            key={option.id}
            type="button"
            role="tab"
            aria-selected={filter === option.id}
            onClick={() => setFilter(option.id)}
            style={{
              padding: 'var(--space-2) var(--space-3)',
              borderRadius: 'var(--radius-md, 6px)',
              border: '1px solid var(--color-neutral-300)',
              background: filter === option.id ? 'var(--color-surface-alt, #ecfdf5)' : 'transparent',
              cursor: 'pointer',
            }}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div style={{ display: 'grid', gap: 'var(--space-4)', gridTemplateColumns: panel ? PANEL_COLUMNS : '1fr' }}>
        <Card>
          <div style={{ padding: 'var(--space-4)' }}>
            {queueState.loading && queueState.entries.length === 0 ? (
              <LoadingSkeleton />
            ) : queueState.error && queueState.entries.length === 0 ? (
              <ErrorState code="NETWORK_ERROR" onRetry={() => loadQueue(filter)} />
            ) : queueState.entries.length === 0 ? (
              <EmptyState title="Aucun ordre">Aucun ordre ne correspond à ce filtre.</EmptyState>
            ) : (
              <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 'var(--space-3)' }}>
                {queueState.entries.map((entry) => (
                  <li
                    key={entry.order_id}
                    style={{
                      border: '1px solid var(--color-neutral-200)',
                      borderRadius: 'var(--radius-md, 6px)',
                      padding: 'var(--space-3)',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-3)' }}>
                      <div style={{ minWidth: 0 }}>
                        <LabPatientIdentity patient={entry.patient} />
                        <p style={{ margin: '4px 0 0', fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                          {entry.ordering_provider_name}
                        </p>
                        <p style={{ margin: '2px 0 0', fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                          {entry.requested_tests.map((t) => t.catalog_display_name_snapshot).join(', ')}
                        </p>
                        <p style={{ margin: '4px 0 0', fontSize: 'var(--font-size-xs)', color: 'var(--color-neutral-500)', fontFamily: 'monospace' }}>
                          Référence de l'ordre : <code>{entry.order_id}</code>
                        </p>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 'var(--space-2)' }}>
                        <StatusBadge tone={orderStatusTone(entry.status)} label={formatOrderStatusLabel(entry.status)} />
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={() => handleOpenOrder(entry.order_id)}
                          aria-label={`Examiner l'ordre de ${entry.patient.display_name?.trim() || 'Nom non renseigné'}`}
                        >
                          Examiner
                        </Button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>

        {panel ? (
          <div>
            <div style={{ marginBottom: 'var(--space-3)' }}>
              <Button variant="secondary" onClick={handleClosePanel} disabled={mutationState.submitting}>
                Fermer le détail
              </Button>
            </div>

            {mutationState.error ? (
              <p role="alert" style={{ color: 'var(--color-danger, #b91c1c)', marginBottom: 'var(--space-3)' }}>
                {mutationState.error}
              </p>
            ) : null}

            {panel.loading ? (
              <LoadingSkeleton />
            ) : panel.error ? (
              <p role="alert" style={{ color: 'var(--color-danger, #b91c1c)' }}>
                {panel.error}
              </p>
            ) : panel.order ? (
              <Card>
                <div style={{ padding: 'var(--space-4)' }}>
                  <div style={{ paddingBottom: 'var(--space-3)', marginBottom: 'var(--space-3)', borderBottom: '1px solid var(--color-neutral-200)' }}>
                    <LabPatientIdentity patient={panel.order.patient} variant="full" />
                  </div>

                  <div style={{ display: 'grid', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                      <strong>Établissement prescripteur :</strong> {panel.order.ordering_provider_name}
                    </p>
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                      <strong>Analyses demandées :</strong>{' '}
                      {panel.order.requested_tests.map((t) => t.catalog_display_name_snapshot).join(', ')}
                    </p>
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                      {panel.order.collection_recorded_at ? (
                        <>
                          <strong>Collecte enregistrée</strong>{' '}
                          {formatDateTime(panel.order.collection_recorded_at)}
                        </>
                      ) : (
                        // §11/§49: null is worded as "not recorded in DHealth", never as
                        // "definitely not collected" -- a legacy order may predate DH-EX-011.
                        <>Collecte non enregistrée dans DHealth</>
                      )}
                    </p>
                    <p style={{ margin: 0, fontSize: 'var(--font-size-xs)', color: 'var(--color-neutral-500)', fontFamily: 'monospace' }}>
                      Référence de l'ordre : <code>{panel.order.order_id}</code>
                    </p>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
                    <StatusBadge tone={orderStatusTone(panel.order.status)} label={formatOrderStatusLabel(panel.order.status)} />
                  </div>

                  {panel.order.status === 'SELECTED' ? (
                    confirming === 'reject' ? (
                      <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
                        <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                          Refuser cet ordre ? Le laboratoire ne pourra pas accepter cet ordre dans
                          son état actuel.
                        </p>
                        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                          <Button
                            type="button"
                            onClick={() => void runMutation('reject')}
                            disabled={mutationState.submitting}
                          >
                            {mutationState.submitting && mutationState.action === 'reject'
                              ? 'Enregistrement…'
                              : 'Confirmer le refus'}
                          </Button>
                          <Button
                            type="button"
                            variant="secondary"
                            onClick={() => setConfirming(null)}
                            disabled={mutationState.submitting}
                          >
                            Annuler
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                        <Button
                          type="button"
                          onClick={() => void runMutation('accept')}
                          disabled={mutationState.submitting}
                        >
                          {mutationState.submitting && mutationState.action === 'accept'
                            ? 'Enregistrement…'
                            : "Accepter l'ordre"}
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={() => setConfirming('reject')}
                          disabled={mutationState.submitting}
                        >
                          Refuser l'ordre
                        </Button>
                      </div>
                    )
                  ) : panel.order.status === 'ACCEPTED' && !panel.order.collection_recorded_at ? (
                    // §9/§14/§48: a *new* collection milestone may only be created while ACCEPTED
                    // and no milestone exists yet -- exactly the one state this button is offered
                    // in, matching the backend's own creation rule.
                    confirming === 'collect' ? (
                      <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
                        <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                          Enregistrer la collecte ? Confirmez que la collecte correspondant à cet
                          ordre doit être enregistrée dans DHealth.
                        </p>
                        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                          <Button
                            type="button"
                            onClick={() => void runCollection()}
                            disabled={mutationState.submitting}
                          >
                            {mutationState.submitting && mutationState.action === 'collect'
                              ? 'Enregistrement…'
                              : 'Confirmer la collecte'}
                          </Button>
                          <Button
                            type="button"
                            variant="secondary"
                            onClick={() => setConfirming(null)}
                            disabled={mutationState.submitting}
                          >
                            Annuler
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <Button
                        type="button"
                        onClick={() => setConfirming('collect')}
                        disabled={mutationState.submitting}
                      >
                        Enregistrer la collecte
                      </Button>
                    )
                  ) : panel.order.status === 'ACCEPTED' && panel.order.collection_recorded_at ? (
                    // §18/§48: collection is recorded and the order is still ACCEPTED -- the only
                    // state "Commencer l'analyse" is offered in.
                    confirming === 'start' ? (
                      <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
                        <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                          Commencer l'analyse ? L'ordre passera à l'état « En cours ».
                        </p>
                        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                          <Button
                            type="button"
                            onClick={() => void runProcessing()}
                            disabled={mutationState.submitting}
                          >
                            {mutationState.submitting && mutationState.action === 'start'
                              ? 'Enregistrement…'
                              : 'Commencer'}
                          </Button>
                          <Button
                            type="button"
                            variant="secondary"
                            onClick={() => setConfirming(null)}
                            disabled={mutationState.submitting}
                          >
                            Annuler
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <Button
                        type="button"
                        onClick={() => setConfirming('start')}
                        disabled={mutationState.submitting}
                      >
                        Commencer l'analyse
                      </Button>
                    )
                  ) : panel.order.status === 'IN_PROGRESS' ? (
                    // DH-LAB-004 §7-48: a controlled Native entry form per ordered item, built
                    // from the actual configured template definitions -- never a hardcoded
                    // example field, never a free-form fallback when no template is configured.
                    <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
                      <h2 style={{ margin: 0, fontSize: 'var(--font-size-md)', color: 'var(--color-neutral-900)' }}>
                        Saisie des résultats
                      </h2>
                      {workspaceState?.loading ? (
                        <LoadingSkeleton />
                      ) : workspaceState?.error ? (
                        <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)' }}>
                          {workspaceState.error}
                        </p>
                      ) : workspaceState?.workspace ? (
                        <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
                          {workspaceState.workspace.items.map((item) => (
                            <div
                              key={item.item_id}
                              style={{
                                border: '1px solid var(--color-neutral-200)',
                                borderRadius: 'var(--radius-md, 6px)',
                                padding: 'var(--space-3)',
                              }}
                            >
                              <p style={{ margin: '0 0 var(--space-2)', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
                                {item.catalog_display_name_snapshot}
                              </p>
                              <NativeResultEntryForm
                                client={client}
                                labId={activeLab!.organization_id}
                                orderId={panel.order!.order_id}
                                item={item}
                                onChanged={() => loadWorkspace(panel.order!.order_id)}
                              />
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : panel.order.status === 'COMPLETED' ? (
                    // DH-LAB-005 §25-28/§58: amendment discovery/start/resume -- never dependent
                    // on browser memory. "Commencer un amendement" and "Reprendre l'amendement"
                    // are mutually exclusive and driven entirely by this GET, never by local
                    // component state alone. There is no "Annuler l'amendement" control anywhere
                    // in this branch: PREPARING/RELEASED are the only two amendment states that
                    // exist in this version of DHealth.
                    <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
                      <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                        Résultats libérés.
                      </p>
                      <ReleaseVersionHistory client={client} labId={activeLab!.organization_id} orderId={panel.order!.order_id} />
                      {amendmentDiscovery?.loading ? (
                        <LoadingSkeleton />
                      ) : amendmentDiscovery?.error ? (
                        <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)' }}>
                          {amendmentDiscovery.error}
                        </p>
                      ) : (() => {
                          const preparing = amendmentDiscovery?.amendments?.find(
                            (a) => a.status === 'PREPARING',
                          )
                          if (preparing) {
                            return (
                              <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
                                <h2 style={{ margin: 0, fontSize: 'var(--font-size-md)', color: 'var(--color-neutral-900)' }}>
                                  Amendement en cours (n°{preparing.amendment_number})
                                </h2>
                                <AmendmentWorkspace
                                  client={client}
                                  labId={activeLab!.organization_id}
                                  orderId={panel.order!.order_id}
                                  amendmentId={preparing.amendment_id}
                                />
                              </div>
                            )
                          }
                          if (confirming === 'start-amendment') {
                            return (
                              <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
                                <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                                  Commencer un amendement pour cet ordre ? Les résultats déjà
                                  libérés restent inchangés jusqu'à la libération de cet
                                  amendement.
                                </p>
                                <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                                  <Button
                                    type="button"
                                    onClick={() => void runStartAmendment()}
                                    disabled={startAmendmentState.submitting}
                                  >
                                    {startAmendmentState.submitting
                                      ? 'Enregistrement…'
                                      : "Commencer l'amendement"}
                                  </Button>
                                  <Button
                                    type="button"
                                    variant="secondary"
                                    onClick={() => setConfirming(null)}
                                    disabled={startAmendmentState.submitting}
                                  >
                                    Annuler
                                  </Button>
                                </div>
                              </div>
                            )
                          }
                          return (
                            <Button
                              type="button"
                              onClick={() => setConfirming('start-amendment')}
                              disabled={startAmendmentState.submitting}
                            >
                              Commencer un amendement
                            </Button>
                          )
                        })()}
                    </div>
                  ) : (
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                      Décision enregistrée — <strong>{formatOrderStatusLabel(panel.order.status)}</strong>.
                    </p>
                  )}
                </div>
              </Card>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  )
}
