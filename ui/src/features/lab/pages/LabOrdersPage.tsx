import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  Amendment,
  LabOrderBlock,
  LabOrderBlockReason,
  LabOrderStatus,
  LabSpecimen,
  LabWorkflowOrder,
  ResultEntryWorkspace,
} from '../../../api/exchangeTypes'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  StatusBadge,
  type StatusTone,
} from '../../../design-system'
import { BLOCK_REASONS, formatBlockReason } from '../blockVocabulary'
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
    case 'BLOCKED':
      // DHEALTH-LAB-BLOCKED-001 §25. The laboratory's own wording is deliberately not the
      // clinic's "Bloqué au laboratoire" — to this reader, *here* is the laboratory, and naming
      // it back at them would be noise. The reason is rendered next to this badge, so the badge
      // itself stays the one-word state.
      return 'Bloqué'
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
    case 'BLOCKED':
      // DHEALTH-LAB-BLOCKED-001 §25: `danger`, and here that is right — on the laboratory's own
      // queue a blocked order is work owed to somebody in this room, and it must not blend in
      // with the `warning` tone that ordinary in-flight work carries. Colour is never the only
      // channel: the "Bloqué" text and the reason line carry the same state.
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

/**
 * DHEALTH-LAB-BLOCKED-001 §27: who did this, in words a colleague can use.
 *
 * `ExchangeIdentity` carries no display name by design — it identifies a human by their OIDC
 * `(issuer, subject)` pair, and DH-EX-002 is explicit that email and name are not stable identity.
 * So there is no name to print, and this deliberately does not invent one or fall back to a raw
 * UUID at a technician. "Vous" versus "un autre membre du laboratoire" is the whole of what this
 * screen can truthfully say, and it happens to be the distinction that actually matters at a
 * bench: whether the person reading is the one who has to explain the block.
 */
function formatActor(actorIdentityId: string, viewerIdentityId: string | null): string {
  return viewerIdentityId !== null && actorIdentityId === viewerIdentityId
    ? 'vous'
    : 'un autre membre du laboratoire'
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

// DHEALTH-LAB-BLOCKED-001 §26/§27. Blocking and resolving get the same recovery vocabulary the
// four actions above already use, plus one of their own: a 409 on `block` means a colleague has
// already blocked this order for a *different* reason, which is neither "unconfirmed" nor the
// generic "handled differently" — the user's own reason was genuinely not recorded and the panel
// is about to show them somebody else's.
const BLOCK_CONFLICT_ERROR =
  'Cet ordre vient d’être bloqué pour un autre motif — le blocage actuel est affiché ci-dessous.'
const BLOCK_HISTORY_ERROR = "Impossible de charger l'historique des blocages de cet ordre."
const SPECIMEN_HISTORY_ERROR = "Impossible de charger les prélèvements de cet ordre."

/**
 * DHEALTH-SPECIMEN-001: the order statuses for which the server will record a physical specimen.
 *
 * Mirrors the backend's `SPECIMEN_RECORDABLE_ORDER_STATUSES` -- "work this laboratory has taken and
 * has not yet released". Used for two things, and deliberately not for a third: deciding whether to
 * offer the collection action, and distinguishing "unconfirmed, retry available" from "the order
 * moved somewhere this can no longer be recorded" during lost-response recovery.
 *
 * It is **not** an authorization check. The server decides; this only avoids offering a control that
 * would be refused, exactly as the existing status-driven actions on this page already do.
 */
const SPECIMEN_RECORDABLE_STATUSES: LabOrderStatus[] = ['ACCEPTED', 'IN_PROGRESS']

const QUEUE_PAGE_LIMIT = 100

type QueueFilter =
  | 'SELECTED'
  | 'ACCEPTED'
  | 'IN_PROGRESS'
  | 'BLOCKED'
  | 'COMPLETED'
  | 'REJECTED'
  | 'ALL'

const FILTER_OPTIONS: { id: QueueFilter; label: string }[] = [
  { id: 'SELECTED', label: 'Nouveaux' },
  { id: 'ACCEPTED', label: 'Acceptés' },
  { id: 'IN_PROGRESS', label: 'En cours' },
  // §25: blocked work is work *owed to somebody in this room*, so it gets its own filter rather
  // than being findable only under "Tous". A state nobody can list is the silent state again.
  { id: 'BLOCKED', label: 'Bloqués' },
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

/** DHEALTH-LAB-BLOCKED-001 §28: every episode this order has had, newest first. */
interface BlockHistoryState {
  orderId: string
  loading: boolean
  error: string | null
  blocks: LabOrderBlock[] | null
}

/**
 * DHEALTH-SPECIMEN-001: the order's physical specimen history, read from the server.
 *
 * Its own panel-scoped read rather than a field on the queue, mirroring `BlockHistoryState`
 * exactly -- the number of specimens per order is unbounded in principle and the queue is a
 * fixed-width view. `specimens: null` means "not loaded or failed", and an empty array means
 * "loaded, and this order has none", which are different facts and must stay distinguishable.
 */
interface SpecimenHistoryState {
  orderId: string
  loading: boolean
  error: string | null
  specimens: LabSpecimen[] | null
}

/**
 * DHEALTH-LAB-BLOCKED-001 §26: the block form's own draft, and the whole of what a user supplies.
 * There is deliberately no actor, organization, timestamp or resume-state field here — not
 * disabled ones, not hidden ones, none at all. Anything this shape cannot express is a fact the
 * server derives, and a form that cannot represent it cannot accidentally send it.
 */
interface BlockDraft {
  reason: LabOrderBlockReason
  note: string
}

const AMENDMENT_DISCOVERY_ERROR = 'Impossible de vérifier si un amendement est en cours pour cet ordre.'

export function LabOrdersPage() {
  const { status: authStatus, activeLab, capabilities, client, user } = useLab()

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
  const [blockHistory, setBlockHistory] = useState<BlockHistoryState | null>(null)
  const [specimenHistory, setSpecimenHistory] = useState<SpecimenHistoryState | null>(null)
  // DHEALTH-SPECIMEN-001: the key for the collection the user is *currently intending*, held in a
  // ref rather than regenerated inside `runCollection`.
  //
  // This is the whole of the duplicate-specimen defence on the client side, and the reason it is a
  // ref is the reason it exists at all: if the key were generated inside the POST, a user who
  // pressed "Confirmer" twice after an uncertain first response would send two *different* keys,
  // and the server -- correctly, by its own contract -- would record two physical specimens for one
  // physical collection. The key is minted once when the user opens the confirmation for a
  // collection, and every attempt at that same collection reuses it. A genuinely new collection
  // opens a new confirmation and therefore mints a new key, which is exactly the distinction
  // between "retry the request that may already have won" and "a second sample was taken".
  const collectionKeyRef = useRef<string | null>(null)
  const [blockDraft, setBlockDraft] = useState<BlockDraft>({
    reason: BLOCK_REASONS[0],
    note: '',
  })
  const [mutationState, setMutationState] = useState<{
    action: 'accept' | 'reject' | 'collect' | 'start' | 'block' | 'resolve-block' | null
    submitting: boolean
    error: string | null
  }>({ action: null, submitting: false, error: null })
  // DH-LAB-002 §15/§19/§44, extended by DH-LAB-005 §25 and DHEALTH-LAB-BLOCKED-001 §26/§27: one
  // confirmation slot for whichever deliberate action is pending -- reject, collection,
  // start-processing, starting an amendment, blocking and resolving a block are all mutually
  // exclusive, and the panel stays the single confirmation surface.
  const [confirming, setConfirming] = useState<
    'reject' | 'collect' | 'start' | 'start-amendment' | 'block' | 'resolve-block' | null
  >(null)

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
    // DHEALTH-LAB-BLOCKED-001: block history is lab-scoped clinical-adjacent state and clears in
    // the same render pass as everything else — a previous laboratory's block reasons must never
    // survive a tenant switch, for exactly the reason §12/§39 gives for the queue rows.
    setBlockHistory(null)
    setBlockDraft({ reason: BLOCK_REASONS[0], note: '' })
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

  // DHEALTH-LAB-BLOCKED-001 §28: the order's own block history, read from the server rather than
  // assembled from whatever this browser happens to have seen. Same generation/mount/tenant
  // stale-response discipline as every other panel-scoped read on this page.
  const loadBlockHistory = useCallback(
    (orderId: string) => {
      if (!activeLab) return
      const generation = panelGenerationRef.current
      const targetLab = activeLab.organization_id
      const controller = new AbortController()

      setBlockHistory({ orderId, loading: true, error: null, blocks: null })
      client
        .listLabOrderBlocks(targetLab, orderId, { signal: controller.signal })
        .then((blocks) => {
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setBlockHistory({ orderId, loading: false, error: null, blocks })
        })
        .catch(() => {
          if (controller.signal.aborted) return
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setBlockHistory({ orderId, loading: false, error: BLOCK_HISTORY_ERROR, blocks: null })
        })
      return () => controller.abort()
    },
    [client, activeLab],
  )

  // Loaded for every opened order, not only blocked ones: "has this order been blocked before?"
  // is a question about an order that looks perfectly healthy right now, and an empty history is
  // itself the answer.
  useEffect(() => {
    if (panel?.orderId && panel.order) {
      return loadBlockHistory(panel.orderId)
    }
    setBlockHistory(null)
  }, [panel?.orderId, panel?.order, loadBlockHistory])

  // DHEALTH-SPECIMEN-001: the order's physical specimen history. Same generation/mount/tenant
  // stale-response discipline as every other panel-scoped read on this page -- deliberately
  // reading `panelGenerationRef` without incrementing it, exactly as `loadBlockHistory` does.
  const loadSpecimenHistory = useCallback(
    (orderId: string) => {
      if (!activeLab) return
      const generation = panelGenerationRef.current
      const targetLab = activeLab.organization_id
      const controller = new AbortController()

      setSpecimenHistory({ orderId, loading: true, error: null, specimens: null })
      client
        .listLabOrderSpecimens(targetLab, orderId, { signal: controller.signal })
        .then((specimens) => {
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setSpecimenHistory({ orderId, loading: false, error: null, specimens })
        })
        .catch(() => {
          if (controller.signal.aborted) return
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setSpecimenHistory({
            orderId,
            loading: false,
            error: SPECIMEN_HISTORY_ERROR,
            specimens: null,
          })
        })
      return () => controller.abort()
    },
    [client, activeLab],
  )

  // Loaded for every opened order, for the same reason block history is: "how many physical samples
  // does this order have?" is a question about an order that looks ordinary, and an empty list is
  // itself the answer.
  useEffect(() => {
    if (panel?.orderId && panel.order) {
      return loadSpecimenHistory(panel.orderId)
    }
    setSpecimenHistory(null)
  }, [panel?.orderId, panel?.order, loadSpecimenHistory])

  const handleOpenOrder = (orderId: string) => {
    if (panel?.orderId === orderId) return
    setMutationState({ action: null, submitting: false, error: null })
    setConfirming(null)
    setWorkspaceState(null)
    setAmendmentDiscovery(null)
    setStartAmendmentState({ submitting: false })
    // DHEALTH-SPECIMEN-001: an unconfirmed collection key belongs to the order it was minted for.
    // Carrying it to a different order would send one order's retry key as another order's first
    // collection -- harmless today (the key is scoped `(lab_order_id, idempotency_key)`, so it
    // would simply not match anything) but meaningless, and it would make the ref's one invariant
    // -- "non-null means an attempt on *this* order is outstanding" -- false.
    collectionKeyRef.current = null
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
  //
  // **[Changed 2026-10-01, DHEALTH-SPECIMEN-001.** The POST now carries
  // `collectionKeyRef.current`, and the lost-response recovery below is rewritten around it.
  //
  // The old recovery predicate -- "`collection_recorded_at` is non-null, therefore my request
  // won" -- was correct while an order could have at most one collection, and becomes *false* the
  // moment a second one is recordable: after a recollection the field is already non-null from the
  // *first* collection, so an uncertain second POST would have been reported as a success whether
  // it had been recorded or not. The predicate is therefore now "a specimen carrying **my own**
  // idempotency key exists", which is a fact about this specific request rather than about the
  // order, and it is read back from the server rather than assumed.
  //
  // What has not changed is the shape that matters: recovery is a **read**, never a blind re-POST.
  // This function calls `recordLabOrderCollection` exactly once per invocation.]**
  const runCollection = useCallback(async () => {
    if (!activeLab || !panel?.order) return
    const orderId = panel.order.order_id
    const targetLab = activeLab.organization_id
    const mutationGeneration = panelGenerationRef.current
    // Minted when the confirmation was opened; reused by every attempt at that same collection.
    // The `??` branch is defensive only -- the action is unreachable without a confirmation.
    const idempotencyKey = collectionKeyRef.current ?? crypto.randomUUID()
    collectionKeyRef.current = idempotencyKey
    setMutationState({ action: 'collect', submitting: true, error: null })
    try {
      const response = await client.recordLabOrderCollection(targetLab, orderId, {
        idempotencyKey,
      })
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
        // This collection is settled, so the next one is a genuinely new physical sample and must
        // not reuse this key -- reusing it would make a real second collection look like a retry.
        collectionKeyRef.current = null
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
        const [authoritative, specimens] = await Promise.all([
          client.getLabOrder(targetLab, orderId),
          client.listLabOrderSpecimens(targetLab, orderId),
        ])
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) {
          loadQueue(filter)
          return
        }
        // Did *my* request land? Decided by my own key, never by the order's collection field.
        const mine = specimens.some((specimen) => specimen.idempotency_key === idempotencyKey)
        if (authoritative.order_id !== orderId) {
          setMutationState({ action: 'collect', submitting: false, error: ORDER_MISMATCH_ERROR })
        } else if (mine) {
          // Case A: the specimen this request was for genuinely exists -- success, no error, and
          // never a second POST (this branch never calls `recordLabOrderCollection` again).
          setMutationState({ action: null, submitting: false, error: null })
          collectionKeyRef.current = null
        } else if (SPECIMEN_RECORDABLE_STATUSES.includes(authoritative.status)) {
          // Case B: the order is still in a state where this collection could be recorded, and it
          // was not -- genuinely unconfirmed. An explicit retry stays available and will reuse the
          // same key, so it cannot create a duplicate if the first attempt did in fact land.
          setMutationState({
            action: 'collect',
            submitting: false,
            error: MUTATION_UNCONFIRMED_ERROR,
          })
        } else {
          // Case C: the order moved somewhere this collection can no longer be recorded (rejected,
          // cancelled, blocked, released) and it never was -- a real conflict, never fabricated
          // collection history.
          setMutationState({ action: 'collect', submitting: false, error: MUTATION_CONFLICT_ERROR })
        }
        if (authoritative.order_id === orderId) {
          setPanel({ orderId, loading: false, error: null, order: authoritative })
          setSpecimenHistory({ orderId, loading: false, error: null, specimens })
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

  // DHEALTH-LAB-BLOCKED-001 §26. The recovery predicate is `status === 'BLOCKED'` plus an active
  // episode, and the two are read together because the server writes them together -- judging
  // success from the status alone would call a *colleague's* block this user's success.
  const runBlock = useCallback(async () => {
    if (!activeLab || !panel?.order) return
    const orderId = panel.order.order_id
    const targetLab = activeLab.organization_id
    const mutationGeneration = panelGenerationRef.current
    const note = blockDraft.note.trim()
    setMutationState({ action: 'block', submitting: true, error: null })
    try {
      const response = await client.blockLabOrder(targetLab, orderId, {
        reason: blockDraft.reason,
        note: note === '' ? null : note,
      })
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      if (panelGenerationRef.current !== mutationGeneration) {
        loadQueue(filter)
        return
      }
      if (response.order_id !== orderId) {
        setMutationState({ action: 'block', submitting: false, error: ORDER_MISMATCH_ERROR })
      } else if (response.status !== 'BLOCKED') {
        setMutationState({ action: 'block', submitting: false, error: MUTATION_CONFLICT_ERROR })
      } else {
        setMutationState({ action: null, submitting: false, error: null })
        setBlockDraft({ reason: BLOCK_REASONS[0], note: '' })
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
        const active = authoritative.active_block
        if (authoritative.order_id !== orderId) {
          setMutationState({ action: 'block', submitting: false, error: ORDER_MISMATCH_ERROR })
        } else if (
          authoritative.status === 'BLOCKED' &&
          active !== null &&
          active.reason === blockDraft.reason &&
          (active.note ?? '') === note
        ) {
          // Case A: the episode that exists is genuinely this user's own request, recorded. No
          // error, and never a second POST.
          setMutationState({ action: null, submitting: false, error: null })
          setBlockDraft({ reason: BLOCK_REASONS[0], note: '' })
        } else if (authoritative.status === 'BLOCKED') {
          // Case B: blocked, but for something else -- a colleague won the race. This user's own
          // reason was *not* recorded, and saying "unconfirmed" would invite them to retry into a
          // guaranteed 409. The panel below now shows whose block it actually is.
          setMutationState({ action: 'block', submitting: false, error: BLOCK_CONFLICT_ERROR })
        } else if (
          authoritative.status === 'ACCEPTED' ||
          authoritative.status === 'IN_PROGRESS'
        ) {
          // Case C: still blockable, no episode -- genuinely unconfirmed, retry stays available.
          setMutationState({ action: 'block', submitting: false, error: MUTATION_UNCONFIRMED_ERROR })
        } else {
          // Case D: the order moved somewhere a block is no longer possible at all.
          setMutationState({ action: 'block', submitting: false, error: MUTATION_CONFLICT_ERROR })
        }
        if (authoritative.order_id === orderId) {
          setPanel({ orderId, loading: false, error: null, order: authoritative })
        }
        loadQueue(filter)
      } catch {
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) return
        setMutationState({ action: 'block', submitting: false, error: MUTATION_UNCONFIRMED_ERROR })
      }
    }
  }, [activeLab, panel, client, blockDraft, loadPanel, loadQueue, filter])

  // §27. The episode id is captured at the moment the user confirmed, and the request names it.
  // A late retry therefore resolves the block this user was looking at or nothing at all -- never
  // a second block raised in the meantime for an unrelated reason.
  const runResolveBlock = useCallback(
    async (blockId: string) => {
      if (!activeLab || !panel?.order) return
      const orderId = panel.order.order_id
      const targetLab = activeLab.organization_id
      const mutationGeneration = panelGenerationRef.current
      setMutationState({ action: 'resolve-block', submitting: true, error: null })
      try {
        const response = await client.resolveLabOrderBlock(targetLab, orderId, blockId)
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) {
          loadQueue(filter)
          return
        }
        if (response.order_id !== orderId) {
          setMutationState({
            action: 'resolve-block',
            submitting: false,
            error: ORDER_MISMATCH_ERROR,
          })
        } else if (response.status === 'BLOCKED') {
          setMutationState({
            action: 'resolve-block',
            submitting: false,
            error: MUTATION_CONFLICT_ERROR,
          })
        } else {
          // The server chose where this lands. This client deliberately asserts nothing about
          // *which* state resumed -- it reports whatever came back.
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
            setMutationState({
              action: 'resolve-block',
              submitting: false,
              error: ORDER_MISMATCH_ERROR,
            })
          } else if (authoritative.active_block === null) {
            // Resolved -- by this call or by a colleague's. Either way the work has resumed and
            // there is nothing left for this user to do, so this is success rather than a
            // conflict.
            setMutationState({ action: null, submitting: false, error: null })
          } else if (authoritative.active_block.id === blockId) {
            // The same episode is still unresolved: genuinely unconfirmed, retry stays available.
            setMutationState({
              action: 'resolve-block',
              submitting: false,
              error: MUTATION_UNCONFIRMED_ERROR,
            })
          } else {
            // A *different* episode is active now -- the one this user saw was resolved and the
            // order has since been blocked again. Not an error to retry into.
            setMutationState({
              action: 'resolve-block',
              submitting: false,
              error: MUTATION_CONFLICT_ERROR,
            })
          }
          if (authoritative.order_id === orderId) {
            setPanel({ orderId, loading: false, error: null, order: authoritative })
          }
          loadQueue(filter)
        } catch {
          if (!mountedRef.current || currentLabRef.current !== targetLab) return
          if (panelGenerationRef.current !== mutationGeneration) return
          setMutationState({
            action: 'resolve-block',
            submitting: false,
            error: MUTATION_UNCONFIRMED_ERROR,
          })
        }
      }
    },
    [activeLab, panel, client, loadPanel, loadQueue, filter],
  )

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
                        {/*
                          DHEALTH-LAB-BLOCKED-001 §25: the reason, in text, on the queue row
                          itself. The badge's colour is never the only channel carrying this --
                          "Bloqué" is a word and so is the motif, so the state survives a
                          greyscale screen, a colour-blind reader and a screen reader alike. The
                          note is deliberately not here: the queue says what is wrong, the detail
                          panel says the rest.
                        */}
                        {entry.active_block ? (
                          <p style={{ margin: '4px 0 0', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
                            Motif : {formatBlockReason(entry.active_block.reason)}
                          </p>
                        ) : null}
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
                    {/*
                      DHEALTH-SPECIMEN-001: the specimen history *replaces* the single milestone
                      timestamp once any specimen exists, rather than sitting beside it.

                      Showing both would be two displays of an overlapping fact: for the first
                      collection they are the same instant by construction, and after a second
                      collection the milestone line would silently keep showing the first one next to
                      a two-row history, which reads as a contradiction. So the milestone line
                      survives for exactly the case it is still the only truth -- an order whose
                      collection was recorded before specimens were modelled.
                    */}
                    {specimenHistory?.orderId === panel.order.order_id &&
                    specimenHistory.specimens !== null &&
                    specimenHistory.specimens.length > 0 ? (
                      <div>
                        <p
                          style={{
                            margin: '0 0 var(--space-1)',
                            fontSize: 'var(--font-size-sm)',
                            fontWeight: 'var(--font-weight-semibold)',
                          }}
                        >
                          Prélèvements ({specimenHistory.specimens.length})
                        </p>
                        <ul
                          style={{
                            listStyle: 'none',
                            margin: 0,
                            padding: 0,
                            display: 'grid',
                            gap: 'var(--space-1)',
                          }}
                        >
                          {specimenHistory.specimens.map((specimen, index) => (
                            <li key={specimen.id} style={{ fontSize: 'var(--font-size-sm)' }}>
                              {/*
                                "Prélèvement enregistré", and the same wording for every row.

                                Deliberately NOT "Nouveau prélèvement", "Prélèvement de contrôle" or
                                anything implying the second row replaced the first: DHealth does not
                                know *why* a later sample was recorded. An order may legitimately
                                need more than one, and nothing in the collection action says which
                                case this is -- so the UI states what the domain knows (a sample was
                                recorded, at this instant) and nothing more. Equally not
                                "Prélèvement effectué": the server recorded that someone entered it,
                                never that collection physically happened at that moment.

                                The index is an ordinal for reading, not a clinical label.
                              */}
                              {index + 1}. Prélèvement enregistré {formatDateTime(specimen.recorded_at)}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : panel.order.collection_recorded_at ? (
                      // A collection recorded before DHEALTH-SPECIMEN-001: the milestone is the only
                      // record, and how many physical samples it stood for is not known. Worded as
                      // the milestone it is, with no specimen count implied.
                      <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                        <strong>Collecte enregistrée</strong>{' '}
                        {formatDateTime(panel.order.collection_recorded_at)}
                      </p>
                    ) : (
                      // §11/§49: null is worded as "not recorded in DHealth", never as
                      // "definitely not collected" -- a legacy order may predate DH-EX-011.
                      <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                        Collecte non enregistrée dans DHealth
                      </p>
                    )}
                    {specimenHistory?.orderId === panel.order.order_id && specimenHistory.error ? (
                      <p
                        role="alert"
                        style={{
                          margin: 0,
                          fontSize: 'var(--font-size-sm)',
                          color: 'var(--color-danger, #b91c1c)',
                        }}
                      >
                        {specimenHistory.error}
                      </p>
                    ) : null}
                    <p style={{ margin: 0, fontSize: 'var(--font-size-xs)', color: 'var(--color-neutral-500)', fontFamily: 'monospace' }}>
                      Référence de l'ordre : <code>{panel.order.order_id}</code>
                    </p>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
                    <StatusBadge tone={orderStatusTone(panel.order.status)} label={formatOrderStatusLabel(panel.order.status)} />
                  </div>

                  {/*
                    DHEALTH-LAB-BLOCKED-001 §27. The active block, shown above every action, so a
                    technician who opens a blocked order reads why before deciding anything. The
                    server's own `blocked_from_status` is what the resume sentence quotes -- this
                    component never computes where the order will land, because it does not decide
                    that.
                  */}
                  {panel.order.active_block ? (
                    <div
                      role="status"
                      style={{
                        border: '1px solid var(--color-danger, #b91c1c)',
                        borderRadius: 'var(--radius-md, 6px)',
                        padding: 'var(--space-3)',
                        marginBottom: 'var(--space-3)',
                        display: 'grid',
                        gap: 'var(--space-2)',
                      }}
                    >
                      <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
                        Travail bloqué — {formatBlockReason(panel.order.active_block.reason)}
                      </p>
                      <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                        Bloqué {formatDateTime(panel.order.active_block.blocked_at)} par{' '}
                        {formatActor(panel.order.active_block.blocked_by_identity_id, user?.identity_id ?? null)}.
                      </p>
                      {panel.order.active_block.note ? (
                        <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                          <strong>Note :</strong> {panel.order.active_block.note}
                        </p>
                      ) : null}
                      {confirming === 'resolve-block' ? (
                        <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
                          <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                            Résoudre ce blocage ? L'ordre reprendra à l'état «{' '}
                            {formatOrderStatusLabel(
                              panel.order.active_block.blocked_from_status as LabOrderStatus,
                            )}{' '}
                            » et ce blocage restera dans l'historique.
                          </p>
                          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                            <Button
                              type="button"
                              onClick={() => void runResolveBlock(panel.order!.active_block!.id)}
                              disabled={mutationState.submitting}
                            >
                              {mutationState.submitting && mutationState.action === 'resolve-block'
                                ? 'Enregistrement…'
                                : 'Confirmer la reprise'}
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
                        <div>
                          <Button
                            type="button"
                            onClick={() => setConfirming('resolve-block')}
                            disabled={mutationState.submitting}
                          >
                            Résoudre le blocage
                          </Button>
                        </div>
                      )}
                    </div>
                  ) : null}

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
                        onClick={() => {
                          // DHEALTH-SPECIMEN-001: one key per *intended* collection.
                          //
                          // `??=`, not `=`, and the difference is the whole duplicate-specimen
                          // defence. A non-null ref means "an attempt at this collection is
                          // outstanding and unconfirmed" -- the success path clears it to null, so
                          // the only way it survives to here is a lost or refused response. Minting
                          // a fresh key on that retry would send a *different* key for the same
                          // physical collection, and a first POST that landed late would then have
                          // become a second specimen: the exact double-click failure this
                          // mechanism exists to prevent.
                          collectionKeyRef.current ??= crypto.randomUUID()
                          setConfirming('collect')
                        }}
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

                  {/*
                    DHEALTH-SPECIMEN-001: recording an *additional* physical specimen for an order
                    that already has one.

                    Offered only where the server will accept it -- a milestone already exists
                    (otherwise the primary "Enregistrer la collecte" action above is the right
                    control) and the order is still work this laboratory holds and has not released.
                    A control shown where it would be refused is a lie about what the product does;
                    hiding one is usability and never the boundary, which stays server-side.

                    Secondary and placed after the workflow actions, for the same reason the block
                    control is: taking another sample is the exception, not the ordinary next step.

                    **The wording says only what DHealth knows.** "Enregistrer un nouveau
                    prélèvement" -- never "recollecte", never "nouveau prélèvement de remplacement",
                    never anything implying the earlier sample was rejected. The system does not
                    know why a second sample was taken: an order may legitimately require more than
                    one, and the collection action carries no statement of cause. The operational
                    reason, where there is one, lives in the block episode the technician recorded.
                  */}
                  {SPECIMEN_RECORDABLE_STATUSES.includes(panel.order.status) &&
                  panel.order.collection_recorded_at ? (
                    <div
                      style={{
                        marginTop: 'var(--space-4)',
                        paddingTop: 'var(--space-3)',
                        borderTop: '1px solid var(--color-neutral-200)',
                      }}
                    >
                      {confirming === 'collect' ? (
                        <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
                          <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                            Enregistrer un nouveau prélèvement ? Les prélèvements déjà enregistrés
                            sont conservés — celui-ci s'ajoute à l'historique.
                          </p>
                          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                            <Button
                              type="button"
                              onClick={() => void runCollection()}
                              disabled={mutationState.submitting}
                            >
                              {mutationState.submitting && mutationState.action === 'collect'
                                ? 'Enregistrement…'
                                : 'Confirmer le prélèvement'}
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
                          variant="secondary"
                          onClick={() => {
                            // `??=` for the same reason as the primary action above: a surviving key
                            // means an unconfirmed attempt at *this* collection is outstanding, and
                            // the retry must carry it rather than mint a second one. The success
                            // path clears the ref, so a genuinely new sample always gets a new key.
                            collectionKeyRef.current ??= crypto.randomUUID()
                            setConfirming('collect')
                          }}
                          disabled={mutationState.submitting}
                        >
                          Enregistrer un nouveau prélèvement
                        </Button>
                      )}
                    </div>
                  ) : null}

                  {/*
                    DHEALTH-LAB-BLOCKED-001 §26. Offered only in the two states the server accepts
                    a block from, so the control is never shown where it would be refused. The
                    server refuses a direct API call regardless -- hiding a control is usability,
                    never the boundary (`docs/NATIVE-OPERATING-PRODUCT.md`: "the browser is never
                    the boundary").

                    Deliberately rendered *after* the workflow actions above rather than beside
                    them: at a bench the ordinary action is to continue, and blocking is the
                    exception being reached for. It must be findable, not the first thing a tired
                    hand lands on.
                  */}
                  {panel.order.status === 'ACCEPTED' || panel.order.status === 'IN_PROGRESS' ? (
                    <div
                      style={{
                        marginTop: 'var(--space-4)',
                        paddingTop: 'var(--space-3)',
                        borderTop: '1px solid var(--color-neutral-200)',
                      }}
                    >
                      {confirming === 'block' ? (
                        <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
                          <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                            Indiquez pourquoi le travail ne peut pas continuer. L'ordre reste
                            valable et reprendra après résolution.
                          </p>
                          <label
                            htmlFor="block-reason"
                            style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600 }}
                          >
                            Motif
                          </label>
                          <select
                            id="block-reason"
                            value={blockDraft.reason}
                            onChange={(event) =>
                              setBlockDraft((prev) => ({
                                ...prev,
                                reason: event.target.value as LabOrderBlockReason,
                              }))
                            }
                            disabled={mutationState.submitting}
                            style={{ padding: 'var(--space-2)', fontSize: 'var(--font-size-sm)' }}
                          >
                            {BLOCK_REASONS.map((reason) => (
                              <option key={reason} value={reason}>
                                {formatBlockReason(reason)}
                              </option>
                            ))}
                          </select>
                          <label
                            htmlFor="block-note"
                            style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600 }}
                          >
                            Note (facultative)
                          </label>
                          <textarea
                            id="block-note"
                            value={blockDraft.note}
                            onChange={(event) =>
                              setBlockDraft((prev) => ({ ...prev, note: event.target.value }))
                            }
                            // Matches `LabOrderBlock.note`'s own String(1024) exactly, so an
                            // over-long note is refused here rather than by PostgreSQL.
                            maxLength={1024}
                            rows={3}
                            disabled={mutationState.submitting}
                            style={{ padding: 'var(--space-2)', fontSize: 'var(--font-size-sm)' }}
                          />
                          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                            <Button
                              type="button"
                              onClick={() => void runBlock()}
                              disabled={mutationState.submitting}
                            >
                              {mutationState.submitting && mutationState.action === 'block'
                                ? 'Enregistrement…'
                                : 'Confirmer le blocage'}
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
                          variant="secondary"
                          onClick={() => setConfirming('block')}
                          disabled={mutationState.submitting}
                        >
                          Bloquer l'ordre
                        </Button>
                      )}
                    </div>
                  ) : null}

                  {/*
                    §28: a small "Blocages" section, not an event-sourcing timeline. It answers
                    four questions and stops: was this blocked before, why, by whom, and when was
                    it resolved. Resolved episodes stay -- that is the whole reason this is a
                    table of episodes rather than a column on the order.
                  */}
                  {blockHistory?.orderId === panel.order.order_id &&
                  blockHistory.blocks !== null &&
                  blockHistory.blocks.length > 0 ? (
                    <div style={{ marginTop: 'var(--space-4)' }}>
                      <h2
                        style={{
                          margin: '0 0 var(--space-2)',
                          fontSize: 'var(--font-size-md)',
                          color: 'var(--color-neutral-900)',
                        }}
                      >
                        Blocages
                      </h2>
                      <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: 'var(--space-2)' }}>
                        {blockHistory.blocks.map((block) => (
                          <li
                            key={block.id}
                            style={{
                              border: '1px solid var(--color-neutral-200)',
                              borderRadius: 'var(--radius-md, 6px)',
                              padding: 'var(--space-2)',
                              fontSize: 'var(--font-size-sm)',
                            }}
                          >
                            <p style={{ margin: 0, fontWeight: 600 }}>
                              {formatBlockReason(block.reason)}
                              {block.resolved_at === null ? ' — en cours' : ' — résolu'}
                            </p>
                            <p style={{ margin: 0, color: 'var(--color-neutral-600)' }}>
                              Bloqué {formatDateTime(block.blocked_at)} par{' '}
                              {formatActor(block.blocked_by_identity_id, user?.identity_id ?? null)}
                            </p>
                            {block.resolved_at !== null && block.resolved_by_identity_id !== null ? (
                              <p style={{ margin: 0, color: 'var(--color-neutral-600)' }}>
                                Résolu {formatDateTime(block.resolved_at)} par{' '}
                                {formatActor(block.resolved_by_identity_id, user?.identity_id ?? null)}
                              </p>
                            ) : null}
                            {block.note ? (
                              <p style={{ margin: 0 }}>
                                <strong>Note :</strong> {block.note}
                              </p>
                            ) : null}
                            {block.resolution_note ? (
                              <p style={{ margin: 0 }}>
                                <strong>Note de résolution :</strong> {block.resolution_note}
                              </p>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : blockHistory?.orderId === panel.order.order_id && blockHistory.error ? (
                    <p
                      role="alert"
                      style={{
                        marginTop: 'var(--space-3)',
                        fontSize: 'var(--font-size-sm)',
                        color: 'var(--color-danger, #b91c1c)',
                      }}
                    >
                      {blockHistory.error}
                    </p>
                  ) : null}
                </div>
              </Card>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  )
}
