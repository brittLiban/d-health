import { useCallback, useEffect, useRef, useState } from 'react'
import type { LabOrderStatus, LabWorkflowOrder } from '../../../api/exchangeTypes'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  StatusBadge,
  type StatusTone,
} from '../../../design-system'
import { LabPatientIdentity } from '../components/LabPatientIdentity'
import { useLab } from '../LabContext'

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
const MUTATION_UNCONFIRMED_ERROR = "L'action n'a pas pu être confirmée. Vous pouvez réessayer."
const MUTATION_CONFLICT_ERROR =
  "Cet ordre a déjà été traité différemment -- l'état actuel est affiché ci-dessous."

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

export function LabOrdersPage() {
  const { status: authStatus, activeLab, capabilities, client } = useLab()

  const [filter, setFilter] = useState<QueueFilter>('SELECTED')
  const [queueState, setQueueState] = useState<{
    entries: LabWorkflowOrder[]
    loading: boolean
    error: string | null
  }>({ entries: [], loading: true, error: null })
  const [panel, setPanel] = useState<PanelState | null>(null)
  const [mutationState, setMutationState] = useState<{
    action: 'accept' | 'reject' | null
    submitting: boolean
    error: string | null
  }>({ action: null, submitting: false, error: null })
  const [confirmingReject, setConfirmingReject] = useState(false)

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
    setMutationState({ action: null, submitting: false, error: null })
    setConfirmingReject(false)
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

  const handleOpenOrder = (orderId: string) => {
    if (panel?.orderId === orderId) return
    setMutationState({ action: null, submitting: false, error: null })
    setConfirmingReject(false)
    loadPanel(orderId)
  }

  const handleClosePanel = () => {
    // §41: bump the generation even though loadPanel isn't called here -- otherwise a mutation
    // started before this close (see runMutation below) would still find its captured generation
    // current and resurrect the just-closed panel once its response/recheck resolves.
    panelGenerationRef.current += 1
    setPanel(null)
    setMutationState({ action: null, submitting: false, error: null })
    setConfirmingReject(false)
  }

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
        setConfirmingReject(false)
        loadPanel(orderId, { isRecheck: true })
        loadQueue(filter)
      } catch {
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) {
          loadQueue(filter)
          return
        }
        setConfirmingReject(false)
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

      <div style={{ display: 'grid', gap: 'var(--space-4)', gridTemplateColumns: panel ? '1fr 1fr' : '1fr' }}>
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
                      <strong>Collecte :</strong>{' '}
                      {panel.order.collection_recorded_at
                        ? formatDateTime(panel.order.collection_recorded_at)
                        : 'Collecte non enregistrée dans DHealth'}
                    </p>
                    <p style={{ margin: 0, fontSize: 'var(--font-size-xs)', color: 'var(--color-neutral-500)', fontFamily: 'monospace' }}>
                      Référence de l'ordre : <code>{panel.order.order_id}</code>
                    </p>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
                    <StatusBadge tone={orderStatusTone(panel.order.status)} label={formatOrderStatusLabel(panel.order.status)} />
                  </div>

                  {panel.order.status === 'SELECTED' ? (
                    confirmingReject ? (
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
                            onClick={() => setConfirmingReject(false)}
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
                          onClick={() => setConfirmingReject(true)}
                          disabled={mutationState.submitting}
                        >
                          Refuser l'ordre
                        </Button>
                      </div>
                    )
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
