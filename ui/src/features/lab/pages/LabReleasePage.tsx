import { useCallback, useEffect, useRef, useState } from 'react'
import { ExchangeApiError } from '../../../api/exchangeClient'
import type {
  AmendmentInspection,
  AmendmentQueueEntry,
  LabResultInspection,
  ReleaseQueueEntry,
} from '../../../api/exchangeTypes'
import { Button, Card, EmptyState, ErrorState, LoadingSkeleton, StatusBadge } from '../../../design-system'
import { LabPatientIdentity } from '../components/LabPatientIdentity'
import { ReleaseVersionHistory } from '../components/ReleaseVersionHistory'
import { useLab } from '../LabContext'

const INSPECTION_LOAD_ERROR = 'Impossible de charger les résultats de cet ordre.'
const ORDER_MISMATCH_ERROR = 'Impossible de vérifier de manière sûre cet ordre.'
const RELEASE_UNCONFIRMED_ERROR = "La libération n'a pas pu être confirmée. Vous pouvez réessayer."
const RELEASE_STALE_ERROR =
  'Les résultats ont changé depuis votre vérification. Veuillez les examiner à nouveau avant de les libérer.'

const AMENDMENT_INSPECTION_LOAD_ERROR = 'Impossible de charger cet amendement.'
const AMENDMENT_RELEASE_UNCONFIRMED_ERROR =
  "La libération de l'amendement n'a pas pu être confirmée. Vous pouvez réessayer."
// DH-LAB-005 §42: exact wording -- distinct from the original release's own stale-fingerprint
// message above so a releaser always knows which flow (Version 1 vs. an amendment) refused them.
const AMENDMENT_RELEASE_STALE_ERROR =
  'Les résultats de cet amendement ont changé depuis votre vérification. Veuillez les examiner à nouveau avant de les libérer.'

function formatDateTime(value: string): string {
  return new Date(value).toLocaleDateString('fr-FR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

interface PanelState {
  orderId: string
  loading: boolean
  error: string | null
  inspection: LabResultInspection | null
}

/**
 * DH-LAB-004 §49-62: the dedicated `LAB_RESULT_RELEASER` surface -- a least-privilege release
 * queue plus a release-inspection/confirmation flow. Never the `LAB_TECH` work queue broadened:
 * a releaser-only user (no `LAB_TECH`) reaches this page directly, with no dependency on ever
 * having seen the Ordres tab. Release requires the exact `result_fingerprint` from the inspection
 * currently on screen -- a stale fingerprint (an intervening correction) is refused server-side
 * and this page forces a fresh re-inspection rather than silently retrying.
 */
type ReleaseTab = 'results' | 'amendments'

interface AmendmentPanelState {
  orderId: string
  amendmentId: string
  loading: boolean
  error: string | null
  inspection: AmendmentInspection | null
}

export function LabReleasePage() {
  const { status: authStatus, activeLab, capabilities, client } = useLab()

  // DH-LAB-005 §44-47: extends the existing "Libération" tab with an "Amendements" section --
  // never a disconnected second app/page. Every piece of Version-1 release state below stays
  // completely untouched; the amendment-scoped state that follows is deliberately separate,
  // mirroring the backend's own deliberate non-sharing between the two lock preambles.
  const [tab, setTab] = useState<ReleaseTab>('results')

  const [queueState, setQueueState] = useState<{
    entries: ReleaseQueueEntry[]
    loading: boolean
    error: string | null
  }>({ entries: [], loading: true, error: null })
  const [panel, setPanel] = useState<PanelState | null>(null)
  const [confirmingRelease, setConfirmingRelease] = useState(false)
  const [releaseState, setReleaseState] = useState<{ submitting: boolean; error: string | null }>({
    submitting: false,
    error: null,
  })
  const [releasedAt, setReleasedAt] = useState<string | null>(null)

  const [amendmentQueueState, setAmendmentQueueState] = useState<{
    entries: AmendmentQueueEntry[]
    loading: boolean
    error: string | null
  }>({ entries: [], loading: true, error: null })
  const [amendmentPanel, setAmendmentPanel] = useState<AmendmentPanelState | null>(null)
  const [confirmingAmendmentRelease, setConfirmingAmendmentRelease] = useState(false)
  const [amendmentReleaseState, setAmendmentReleaseState] = useState<{
    submitting: boolean
    error: string | null
  }>({ submitting: false, error: null })
  const [amendmentReleasedVersion, setAmendmentReleasedVersion] = useState<number | null>(null)

  const queueGenerationRef = useRef(0)
  const panelGenerationRef = useRef(0)
  const amendmentQueueGenerationRef = useRef(0)
  const amendmentPanelGenerationRef = useRef(0)
  const mountedRef = useRef(true)
  const currentLabRef = useRef<string | null>(activeLab?.organization_id ?? null)
  currentLabRef.current = activeLab?.organization_id ?? null

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // Same render-phase lab-switch clear as LabOrdersPage's own -- a stale lab's queue/panel must
  // never render under the newly-selected lab's header, even for one paint.
  const [prevLabId, setPrevLabId] = useState<string | null>(activeLab?.organization_id ?? null)
  if (activeLab && activeLab.organization_id !== prevLabId) {
    setPrevLabId(activeLab.organization_id)
    setPanel(null)
    setConfirmingRelease(false)
    setReleaseState({ submitting: false, error: null })
    setReleasedAt(null)
    setQueueState({ entries: [], loading: true, error: null })
    queueGenerationRef.current += 1
    panelGenerationRef.current += 1
    setAmendmentPanel(null)
    setConfirmingAmendmentRelease(false)
    setAmendmentReleaseState({ submitting: false, error: null })
    setAmendmentReleasedVersion(null)
    setAmendmentQueueState({ entries: [], loading: true, error: null })
    amendmentQueueGenerationRef.current += 1
    amendmentPanelGenerationRef.current += 1
  }

  const loadQueue = useCallback(() => {
    if (authStatus !== 'ready' || !activeLab || !capabilities.canReleaseResults) return
    const generation = ++queueGenerationRef.current
    const targetLab = activeLab.organization_id
    const controller = new AbortController()

    setQueueState((prev) => ({ entries: prev.entries, loading: true, error: null }))
    client
      .getReleaseQueue(targetLab, { signal: controller.signal })
      .then((response) => {
        if (!mountedRef.current) return
        if (queueGenerationRef.current !== generation) return
        if (currentLabRef.current !== targetLab) return
        if (response.lab_organization_id !== targetLab) {
          setQueueState({ entries: [], loading: false, error: 'Impossible de charger la file.' })
          return
        }
        setQueueState({ entries: response.orders, loading: false, error: null })
      })
      .catch(() => {
        if (controller.signal.aborted) return
        if (!mountedRef.current) return
        if (queueGenerationRef.current !== generation) return
        if (currentLabRef.current !== targetLab) return
        setQueueState({ entries: [], loading: false, error: 'Erreur lors du chargement de la file.' })
      })
    return () => controller.abort()
  }, [client, activeLab, authStatus, capabilities.canReleaseResults])

  useEffect(() => loadQueue(), [loadQueue])

  const loadInspection = useCallback(
    (orderId: string) => {
      if (!activeLab) return
      const generation = ++panelGenerationRef.current
      const targetLab = activeLab.organization_id
      const controller = new AbortController()

      setPanel({ orderId, loading: true, error: null, inspection: null })
      setConfirmingRelease(false)
      setReleaseState({ submitting: false, error: null })
      setReleasedAt(null)
      client
        .getLabOrderResults(targetLab, orderId, { signal: controller.signal })
        .then((inspection) => {
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          if (inspection.order_id !== orderId) {
            setPanel({ orderId, loading: false, error: ORDER_MISMATCH_ERROR, inspection: null })
            return
          }
          setPanel({ orderId, loading: false, error: null, inspection })
        })
        .catch(() => {
          if (controller.signal.aborted) return
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setPanel({ orderId, loading: false, error: INSPECTION_LOAD_ERROR, inspection: null })
        })
      return () => controller.abort()
    },
    [client, activeLab],
  )

  const handleOpenOrder = (orderId: string) => {
    if (panel?.orderId === orderId) return
    loadInspection(orderId)
  }

  const handleClosePanel = () => {
    panelGenerationRef.current += 1
    setPanel(null)
    setConfirmingRelease(false)
    setReleaseState({ submitting: false, error: null })
    setReleasedAt(null)
  }

  const runRelease = useCallback(async () => {
    if (!activeLab || !panel?.inspection) return
    const orderId = panel.inspection.order_id
    const targetLab = activeLab.organization_id
    const expectedFingerprint = panel.inspection.result_fingerprint
    const mutationGeneration = panelGenerationRef.current
    setReleaseState({ submitting: true, error: null })
    try {
      const response = await client.releaseLabOrderResults(targetLab, orderId, {
        expected_result_fingerprint: expectedFingerprint,
      })
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      if (panelGenerationRef.current !== mutationGeneration) {
        loadQueue()
        return
      }
      if (response.order_id !== orderId) {
        setReleaseState({ submitting: false, error: ORDER_MISMATCH_ERROR })
        setConfirmingRelease(false)
        return
      }
      setConfirmingRelease(false)
      setReleaseState({ submitting: false, error: null })
      setReleasedAt(response.released_at)
      loadQueue()
    } catch (error: unknown) {
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      if (panelGenerationRef.current !== mutationGeneration) {
        loadQueue()
        return
      }
      setConfirmingRelease(false)
      const isConflict = error instanceof ExchangeApiError && error.status === 409
      if (isConflict) {
        // DH-LAB-004 §61: no automatic retry -- force the releaser to re-inspect the current
        // result set before any further release attempt. Re-fetching here (rather than merely
        // showing the message) also updates `panel.inspection.result_fingerprint` to the current
        // value, so the next confirm click is a genuine fresh inspection, not a stale replay.
        // `loadInspection` itself resets `releaseState` to clear any *previous* mutation error --
        // called first, so the stale-fingerprint message set immediately after is the one that
        // actually survives to render, not the reset's `null`.
        loadInspection(orderId)
        setReleaseState({ submitting: false, error: RELEASE_STALE_ERROR })
        return
      }
      // Ambiguous (network/5xx) response -- re-read authoritative state, never blind-retry.
      try {
        const authoritative = await client.getLabOrderResults(targetLab, orderId)
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) {
          loadQueue()
          return
        }
        if (authoritative.order_id !== orderId) {
          setReleaseState({ submitting: false, error: ORDER_MISMATCH_ERROR })
        } else if (authoritative.released) {
          setReleaseState({ submitting: false, error: null })
          setReleasedAt(authoritative.released_at)
        } else {
          setReleaseState({ submitting: false, error: RELEASE_UNCONFIRMED_ERROR })
        }
        if (authoritative.order_id === orderId) {
          setPanel({ orderId, loading: false, error: null, inspection: authoritative })
        }
        loadQueue()
      } catch {
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (panelGenerationRef.current !== mutationGeneration) return
        setReleaseState({ submitting: false, error: RELEASE_UNCONFIRMED_ERROR })
      }
    }
  }, [activeLab, panel, client, loadQueue, loadInspection])

  // -- DH-LAB-005 §44-47: the "Amendements" section --------------------------------------------

  const loadAmendmentQueue = useCallback(() => {
    if (authStatus !== 'ready' || !activeLab || !capabilities.canReleaseResults) return
    const generation = ++amendmentQueueGenerationRef.current
    const targetLab = activeLab.organization_id
    const controller = new AbortController()

    setAmendmentQueueState((prev) => ({ entries: prev.entries, loading: true, error: null }))
    client
      .getAmendmentReleaseQueue(targetLab, { signal: controller.signal })
      .then((response) => {
        if (!mountedRef.current) return
        if (amendmentQueueGenerationRef.current !== generation) return
        if (currentLabRef.current !== targetLab) return
        if (response.lab_organization_id !== targetLab) {
          setAmendmentQueueState({ entries: [], loading: false, error: 'Impossible de charger la file.' })
          return
        }
        setAmendmentQueueState({ entries: response.amendments, loading: false, error: null })
      })
      .catch(() => {
        if (controller.signal.aborted) return
        if (!mountedRef.current) return
        if (amendmentQueueGenerationRef.current !== generation) return
        if (currentLabRef.current !== targetLab) return
        setAmendmentQueueState({ entries: [], loading: false, error: 'Erreur lors du chargement de la file.' })
      })
    return () => controller.abort()
  }, [client, activeLab, authStatus, capabilities.canReleaseResults])

  useEffect(() => {
    if (tab === 'amendments') return loadAmendmentQueue()
  }, [loadAmendmentQueue, tab])

  const loadAmendmentInspection = useCallback(
    (orderId: string, amendmentId: string) => {
      if (!activeLab) return
      const generation = ++amendmentPanelGenerationRef.current
      const targetLab = activeLab.organization_id
      const controller = new AbortController()

      setAmendmentPanel({ orderId, amendmentId, loading: true, error: null, inspection: null })
      setConfirmingAmendmentRelease(false)
      setAmendmentReleaseState({ submitting: false, error: null })
      setAmendmentReleasedVersion(null)
      client
        .getLabOrderAmendment(targetLab, orderId, amendmentId, { signal: controller.signal })
        .then((inspection) => {
          if (!mountedRef.current) return
          if (amendmentPanelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          if (inspection.amendment.amendment_id !== amendmentId) {
            setAmendmentPanel({
              orderId,
              amendmentId,
              loading: false,
              error: ORDER_MISMATCH_ERROR,
              inspection: null,
            })
            return
          }
          setAmendmentPanel({ orderId, amendmentId, loading: false, error: null, inspection })
        })
        .catch(() => {
          if (controller.signal.aborted) return
          if (!mountedRef.current) return
          if (amendmentPanelGenerationRef.current !== generation) return
          if (currentLabRef.current !== targetLab) return
          setAmendmentPanel({
            orderId,
            amendmentId,
            loading: false,
            error: AMENDMENT_INSPECTION_LOAD_ERROR,
            inspection: null,
          })
        })
      return () => controller.abort()
    },
    [client, activeLab],
  )

  const handleOpenAmendment = (orderId: string, amendmentId: string) => {
    if (amendmentPanel?.amendmentId === amendmentId) return
    loadAmendmentInspection(orderId, amendmentId)
  }

  const handleCloseAmendmentPanel = () => {
    amendmentPanelGenerationRef.current += 1
    setAmendmentPanel(null)
    setConfirmingAmendmentRelease(false)
    setAmendmentReleaseState({ submitting: false, error: null })
    setAmendmentReleasedVersion(null)
  }

  const runAmendmentRelease = useCallback(async () => {
    if (!activeLab || !amendmentPanel?.inspection) return
    const { orderId, amendmentId } = amendmentPanel
    const targetLab = activeLab.organization_id
    const expectedFingerprint = amendmentPanel.inspection.result_fingerprint
    const mutationGeneration = amendmentPanelGenerationRef.current
    setAmendmentReleaseState({ submitting: true, error: null })
    try {
      const response = await client.releaseLabOrderAmendment(targetLab, orderId, amendmentId, {
        expected_result_fingerprint: expectedFingerprint,
      })
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      if (amendmentPanelGenerationRef.current !== mutationGeneration) {
        loadAmendmentQueue()
        return
      }
      setConfirmingAmendmentRelease(false)
      setAmendmentReleaseState({ submitting: false, error: null })
      setAmendmentReleasedVersion(response.version_number)
      loadAmendmentQueue()
    } catch (error: unknown) {
      if (!mountedRef.current || currentLabRef.current !== targetLab) return
      if (amendmentPanelGenerationRef.current !== mutationGeneration) {
        loadAmendmentQueue()
        return
      }
      setConfirmingAmendmentRelease(false)
      const isConflict = error instanceof ExchangeApiError && error.status === 409
      if (isConflict) {
        // DH-LAB-005 §42: no automatic retry -- force a fresh re-inspection, exactly mirroring
        // the original release's own stale-fingerprint discipline above.
        loadAmendmentInspection(orderId, amendmentId)
        setAmendmentReleaseState({ submitting: false, error: AMENDMENT_RELEASE_STALE_ERROR })
        return
      }
      try {
        const authoritative = await client.getLabOrderAmendment(targetLab, orderId, amendmentId)
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (amendmentPanelGenerationRef.current !== mutationGeneration) {
          loadAmendmentQueue()
          return
        }
        if (authoritative.amendment.status === 'RELEASED') {
          setAmendmentReleaseState({ submitting: false, error: null })
        } else {
          setAmendmentReleaseState({ submitting: false, error: AMENDMENT_RELEASE_UNCONFIRMED_ERROR })
        }
        setAmendmentPanel({ orderId, amendmentId, loading: false, error: null, inspection: authoritative })
        loadAmendmentQueue()
      } catch {
        if (!mountedRef.current || currentLabRef.current !== targetLab) return
        if (amendmentPanelGenerationRef.current !== mutationGeneration) return
        setAmendmentReleaseState({ submitting: false, error: AMENDMENT_RELEASE_UNCONFIRMED_ERROR })
      }
    }
  }, [activeLab, amendmentPanel, client, loadAmendmentQueue, loadAmendmentInspection])

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

  if (!capabilities.canReleaseResults) {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState title="Libération non disponible">
          Vous n'êtes pas autorisé(e) à libérer des résultats pour ce laboratoire.
        </EmptyState>
      </div>
    )
  }

  return (
    <div style={{ padding: 'var(--space-5)', maxWidth: '1100px', margin: '0 auto' }}>
      <header style={{ marginBottom: 'var(--space-4)' }}>
        <h1 style={{ margin: 0, fontSize: 'var(--font-size-2xl)', color: 'var(--color-neutral-900)' }}>
          Libération des résultats
        </h1>
      </header>

      <div
        role="tablist"
        aria-label="Section de libération"
        style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}
      >
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'results'}
          onClick={() => setTab('results')}
          style={{
            padding: 'var(--space-2) var(--space-3)',
            borderRadius: 'var(--radius-md, 6px)',
            border: '1px solid var(--color-neutral-300)',
            background: tab === 'results' ? 'var(--color-surface-alt, #ecfdf5)' : 'transparent',
            cursor: 'pointer',
          }}
        >
          Résultats
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'amendments'}
          onClick={() => setTab('amendments')}
          style={{
            padding: 'var(--space-2) var(--space-3)',
            borderRadius: 'var(--radius-md, 6px)',
            border: '1px solid var(--color-neutral-300)',
            background: tab === 'amendments' ? 'var(--color-surface-alt, #ecfdf5)' : 'transparent',
            cursor: 'pointer',
          }}
        >
          Amendements
        </button>
      </div>

      {tab === 'results' ? (
      <div style={{ display: 'grid', gap: 'var(--space-4)', gridTemplateColumns: panel ? '1fr 1fr' : '1fr' }}>
        <Card>
          <div style={{ padding: 'var(--space-4)' }}>
            {queueState.loading && queueState.entries.length === 0 ? (
              <LoadingSkeleton />
            ) : queueState.error && queueState.entries.length === 0 ? (
              <ErrorState code="NETWORK_ERROR" onRetry={loadQueue} />
            ) : queueState.entries.length === 0 ? (
              <EmptyState title="Aucun ordre prêt">
                Aucun ordre n'est actuellement prêt pour la libération.
              </EmptyState>
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
                      </div>
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => handleOpenOrder(entry.order_id)}
                        aria-label={`Examiner les résultats de ${entry.patient.display_name?.trim() || 'Nom non renseigné'}`}
                      >
                        Examiner
                      </Button>
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
              <Button variant="secondary" onClick={handleClosePanel} disabled={releaseState.submitting}>
                Fermer le détail
              </Button>
            </div>

            {releaseState.error ? (
              <p role="alert" style={{ color: 'var(--color-danger, #b91c1c)', marginBottom: 'var(--space-3)' }}>
                {releaseState.error}
              </p>
            ) : null}

            {panel.loading ? (
              <LoadingSkeleton />
            ) : panel.error ? (
              <p role="alert" style={{ color: 'var(--color-danger, #b91c1c)' }}>
                {panel.error}
              </p>
            ) : panel.inspection ? (
              <Card>
                <div style={{ padding: 'var(--space-4)' }}>
                  <div style={{ paddingBottom: 'var(--space-3)', marginBottom: 'var(--space-3)', borderBottom: '1px solid var(--color-neutral-200)' }}>
                    <LabPatientIdentity patient={panel.inspection.patient} variant="full" />
                  </div>

                  <p style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--font-size-sm)' }}>
                    <strong>Établissement prescripteur :</strong> {panel.inspection.ordering_provider_name}
                  </p>

                  <div style={{ display: 'grid', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
                    {panel.inspection.results.map((item) => (
                      <div
                        key={item.item_id}
                        style={{
                          border: '1px solid var(--color-neutral-200)',
                          borderRadius: 'var(--radius-md, 6px)',
                          padding: 'var(--space-3)',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-2)' }}>
                          <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
                            {item.catalog_display_name_snapshot}
                          </p>
                          <StatusBadge
                            tone={item.entry_completed ? 'success' : 'warning'}
                            label={item.entry_completed ? 'Saisie terminée' : 'Saisie incomplète'}
                          />
                        </div>
                        <ul style={{ margin: 0, paddingLeft: 'var(--space-4)', display: 'grid', gap: 'var(--space-1)' }}>
                          {item.observations.map((observation) => (
                            <li key={observation.observation_id} style={{ fontSize: 'var(--font-size-sm)' }}>
                              {observation.display_label_snapshot} : {observation.numeric_value ?? observation.text_value}
                              {observation.numeric_value && observation.numeric_unit
                                ? ` ${observation.numeric_unit}`
                                : ''}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>

                  {panel.inspection.released || releasedAt ? (
                    <div style={{ marginBottom: 'var(--space-3)' }}>
                      <ReleaseVersionHistory client={client} labId={activeLab!.organization_id} orderId={panel.inspection.order_id} />
                    </div>
                  ) : null}

                  {panel.inspection.released || releasedAt ? (
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-success, #15803d)' }}>
                      Résultats libérés
                      {panel.inspection.released_at || releasedAt
                        ? ` — ${formatDateTime((panel.inspection.released_at ?? releasedAt) as string)}`
                        : ''}
                      .
                    </p>
                  ) : !panel.inspection.all_items_entry_complete ? (
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                      Au moins une analyse n'a pas encore de saisie terminée -- la libération n'est
                      pas encore possible.
                    </p>
                  ) : confirmingRelease ? (
                    <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
                      <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                        Libérer ces résultats ? Ils deviendront disponibles pour l'établissement
                        prescripteur.
                      </p>
                      <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                        <Button type="button" onClick={() => void runRelease()} disabled={releaseState.submitting}>
                          {releaseState.submitting ? 'Enregistrement…' : 'Confirmer la libération'}
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={() => setConfirmingRelease(false)}
                          disabled={releaseState.submitting}
                        >
                          Annuler
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <Button type="button" onClick={() => setConfirmingRelease(true)}>
                      Libérer les résultats
                    </Button>
                  )}
                </div>
              </Card>
            ) : null}
          </div>
        ) : null}
      </div>
      ) : (
      <div style={{ display: 'grid', gap: 'var(--space-4)', gridTemplateColumns: amendmentPanel ? '1fr 1fr' : '1fr' }}>
        <Card>
          <div style={{ padding: 'var(--space-4)' }}>
            {amendmentQueueState.loading && amendmentQueueState.entries.length === 0 ? (
              <LoadingSkeleton />
            ) : amendmentQueueState.error && amendmentQueueState.entries.length === 0 ? (
              <ErrorState code="NETWORK_ERROR" onRetry={loadAmendmentQueue} />
            ) : amendmentQueueState.entries.length === 0 ? (
              <EmptyState title="Aucun amendement prêt">
                Aucun amendement n'est actuellement prêt pour la libération.
              </EmptyState>
            ) : (
              <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 'var(--space-3)' }}>
                {amendmentQueueState.entries.map((entry) => (
                  <li
                    key={entry.amendment_id}
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
                        <p style={{ margin: '4px 0 0', fontSize: 'var(--font-size-xs)', color: 'var(--color-neutral-500)' }}>
                          Amendement n°{entry.amendment_number}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => handleOpenAmendment(entry.order_id, entry.amendment_id)}
                        aria-label={`Examiner l'amendement de ${entry.patient.display_name?.trim() || 'Nom non renseigné'}`}
                      >
                        Examiner
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>

        {amendmentPanel ? (
          <div>
            <div style={{ marginBottom: 'var(--space-3)' }}>
              <Button variant="secondary" onClick={handleCloseAmendmentPanel} disabled={amendmentReleaseState.submitting}>
                Fermer le détail
              </Button>
            </div>

            {amendmentReleaseState.error ? (
              <p role="alert" style={{ color: 'var(--color-danger, #b91c1c)', marginBottom: 'var(--space-3)' }}>
                {amendmentReleaseState.error}
              </p>
            ) : null}

            {amendmentPanel.loading ? (
              <LoadingSkeleton />
            ) : amendmentPanel.error ? (
              <p role="alert" style={{ color: 'var(--color-danger, #b91c1c)' }}>
                {amendmentPanel.error}
              </p>
            ) : amendmentPanel.inspection ? (
              <Card>
                <div style={{ padding: 'var(--space-4)' }}>
                  <p style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--font-size-sm)' }}>
                    <strong>Amendement</strong> n°{amendmentPanel.inspection.amendment.amendment_number} —{' '}
                    <StatusBadge
                      tone={amendmentPanel.inspection.amendment.status === 'RELEASED' ? 'success' : 'warning'}
                      label={
                        amendmentPanel.inspection.amendment.status === 'RELEASED'
                          ? 'Libéré'
                          : 'En préparation'
                      }
                    />
                  </p>

                  <div style={{ display: 'grid', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
                    {amendmentPanel.inspection.items.map((item) => (
                      <div
                        key={item.item_id}
                        style={{
                          border: '1px solid var(--color-neutral-200)',
                          borderRadius: 'var(--radius-md, 6px)',
                          padding: 'var(--space-3)',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-2)' }}>
                          <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
                            {item.catalog_display_name_snapshot}
                          </p>
                          {item.changed_in_this_amendment ? (
                            <StatusBadge tone="warning" label="Modifié dans cet amendement" />
                          ) : null}
                        </div>
                        <ul style={{ margin: 0, paddingLeft: 'var(--space-4)', display: 'grid', gap: 'var(--space-1)' }}>
                          {item.observations.map((observation) => (
                            <li key={observation.observation_id} style={{ fontSize: 'var(--font-size-sm)' }}>
                              {observation.display_label_snapshot} : {observation.numeric_value ?? observation.text_value}
                              {observation.numeric_value && observation.numeric_unit
                                ? ` ${observation.numeric_unit}`
                                : ''}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>

                  {amendmentPanel.inspection.amendment.status === 'RELEASED' || amendmentReleasedVersion ? (
                    <div style={{ marginBottom: 'var(--space-3)' }}>
                      <ReleaseVersionHistory client={client} labId={activeLab!.organization_id} orderId={amendmentPanel.orderId} />
                    </div>
                  ) : null}

                  {amendmentPanel.inspection.amendment.status === 'RELEASED' || amendmentReleasedVersion ? (
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-success, #15803d)' }}>
                      Amendement libéré
                      {amendmentPanel.inspection.amendment.released_at
                        ? ` — ${formatDateTime(amendmentPanel.inspection.amendment.released_at)}`
                        : ''}
                      {amendmentReleasedVersion ? ` (version ${amendmentReleasedVersion})` : ''}.
                    </p>
                  ) : !amendmentPanel.inspection.ready_to_release ? (
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                      Cet amendement n'a encore aucune modification -- la libération n'est pas
                      encore possible.
                    </p>
                  ) : confirmingAmendmentRelease ? (
                    <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
                      <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                        Libérer cet amendement ? Une nouvelle version des résultats sera créée et
                        deviendra disponible pour l'établissement prescripteur.
                      </p>
                      <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                        <Button
                          type="button"
                          onClick={() => void runAmendmentRelease()}
                          disabled={amendmentReleaseState.submitting}
                        >
                          {amendmentReleaseState.submitting ? 'Enregistrement…' : 'Confirmer la libération'}
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={() => setConfirmingAmendmentRelease(false)}
                          disabled={amendmentReleaseState.submitting}
                        >
                          Annuler
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <Button type="button" onClick={() => setConfirmingAmendmentRelease(true)}>
                      Libérer l'amendement
                    </Button>
                  )}
                </div>
              </Card>
            ) : null}
          </div>
        ) : null}
      </div>
      )}
    </div>
  )
}
