import { useCallback, useEffect, useRef, useState } from 'react'
import { ExchangeApiError } from '../../../api/exchangeClient'
import type {
  DoctorReviewResponse,
  DoctorReviewStatus,
  PatientIdentitySummary,
  ProviderOrderResults,
  ProviderReleaseVersionContent,
  ProviderReleaseVersionSummary,
  ProviderResultReviewQueueEntry,
} from '../../../api/exchangeTypes'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  StatusBadge,
} from '../../../design-system'
import { PatientIdentity } from '../components/PatientIdentity'
import { useClinic } from '../ClinicContext'

// DH-CLINIC-007 §64: distinct from ACCEPTED's own meaning elsewhere in this app -- these three
// labels are the exact, already-existing DoctorReviewStatus values, never a fourth invented state.
function formatReviewStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'PENDING':
      return 'À examiner'
    case 'NO_FOLLOW_UP_REQUIRED':
      return 'Revu — aucun suivi requis'
    case 'FOLLOW_UP_REQUIRED':
      return 'Revu — suivi requis'
    default:
      return status || 'Non renseigné'
  }
}

function reviewStatusTone(status: string | null | undefined): 'neutral' | 'success' | 'warning' {
  if (status === 'PENDING') return 'neutral'
  if (status === 'FOLLOW_UP_REQUIRED') return 'warning'
  if (status === 'NO_FOLLOW_UP_REQUIRED') return 'success'
  return 'neutral'
}

/**
 * DH-MVP-POLISH-001 — what a corrected result is called.
 *
 * "Version 2 — Amendée" made the clinician do the arithmetic: it says a number
 * and leaves them to work out that an earlier result they may have acted on has
 * been superseded. "Rectificatif" says the thing itself.
 *
 * Version identity is deliberately not erased, only demoted. It is how this
 * record is discussed with a laboratory over the phone and how it is matched to
 * the backend's own `version_number`, so the number stays — in parentheses here,
 * and on its own under the detailed history — rather than being dropped in
 * favour of a friendlier word.
 */
function formatVersionLabel(versionNumber: number, isAmendment: boolean): string {
  return isAmendment ? `Rectificatif (version ${versionNumber})` : `Version ${versionNumber}`
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

// DH-CLINIC-007 §57: a consistency-check failure must never leak which context disagreed with
// which -- a single fixed, generic message, never interpolated with an order/patient/version id.
const RESULT_CONSISTENCY_ERROR = 'Impossible de vérifier de manière sûre le résultat demandé.'
const RESULT_LOAD_ERROR = 'Impossible de charger le détail de ce résultat.'
const REVIEW_UNCONFIRMED_ERROR = "La revue n'a pas pu être confirmée. Vous pouvez réessayer."
const REVIEW_ALREADY_FINALIZED_NOTICE =
  'Cette version a déjà été revue -- la décision enregistrée est affichée ci-dessous.'

// DH-CLINIC-007 §25/§26: shown once, next to the disposition choice -- the follow-up disposition
// is a workflow record, never a scheduling/contact/notification action.
const FOLLOW_UP_SCOPE_DISCLOSURE =
  "DHealth enregistre ici qu'un suivi est requis. Cette action ne planifie pas automatiquement " +
  'un rendez-vous ni un contact patient.'
const IMMUTABILITY_DISCLOSURE =
  'Cette décision sera enregistrée pour cette version du résultat et ne peut actuellement pas ' +
  'être modifiée dans DHealth une fois confirmée.'

type ReviewTarget =
  | { kind: 'version'; orderId: string; versionId: string }
  | { kind: 'legacy'; orderId: string }

function targetsEqual(a: ReviewTarget, b: ReviewTarget): boolean {
  if (a.kind !== b.kind || a.orderId !== b.orderId) return false
  if (a.kind === 'version' && b.kind === 'version') return a.versionId === b.versionId
  return true
}

interface PanelState {
  target: ReviewTarget
  // True only until the very first content (version-specific or legacy) has loaded for this
  // order -- the whole panel (patient banner, version strip) is not shown yet.
  initializing: boolean
  // True while fetching the exact content for `target` specifically -- during a version switch
  // or a post-mutation recheck, the patient banner and version strip stay on screen and only the
  // content area itself shows a transient loading message (§16/§35: the version strip must
  // remain visible and interactive while switching, never disappear mid-switch). `content` is
  // always null while this is true -- the previous version's content is never shown under a
  // different version's selected tab, even transiently.
  contentLoading: boolean
  // True only while `contentLoading` is specifically a re-read after an ambiguous/conflicting
  // mutation (§70/§71) -- lets the content area say "Vérification..." instead of a generic one.
  checking: boolean
  error: string | null
  // Known once any content has loaded for this order; persists across version switches (the
  // patient never changes between versions of the same order) so the banner never disappears.
  patient: PatientIdentitySummary | null
  // `null` means "not yet known"; `[]` means "checked, and this order is genuinely
  // legacy/unversioned" -- never confused with "not yet known".
  versions: ProviderReleaseVersionSummary[] | null
  content: ProviderReleaseVersionContent | null
  legacyContent: ProviderOrderResults | null
}

export function ClinicResultsPage() {
  const { status: authStatus, activeProvider, client, reload } = useClinic()

  const [queueState, setQueueState] = useState<{
    entries: ProviderResultReviewQueueEntry[]
    loading: boolean
    error: string | null
  }>({ entries: [], loading: true, error: null })

  const [panel, setPanel] = useState<PanelState | null>(null)
  const [disposition, setDisposition] = useState<DoctorReviewStatus | null>(null)
  const [submitState, setSubmitState] = useState<{ submitting: boolean; error: string | null }>({
    submitting: false,
    error: null,
  })

  // DH-CLINIC-007 §45-48: every in-flight request (queue load, panel load, review mutation) is
  // bound to the generation that started it, the provider that started it, and (via `mountedRef`)
  // whether the page is still mounted -- mirrors DH-CLINIC-005's own established discipline.
  const queueGenerationRef = useRef(0)
  const panelGenerationRef = useRef(0)
  const mountedRef = useRef(true)
  const currentProviderRef = useRef<string | null>(activeProvider?.organization_id ?? null)
  currentProviderRef.current = activeProvider?.organization_id ?? null

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const [prevOrgId, setPrevOrgId] = useState<string | null>(activeProvider?.organization_id ?? null)
  if (activeProvider && activeProvider.organization_id !== prevOrgId) {
    setPrevOrgId(activeProvider.organization_id)
    setPanel(null)
    setDisposition(null)
    setSubmitState({ submitting: false, error: null })
    // §47/§73: the outgoing provider's queue entries must never render, even transiently, under
    // the new provider's own heading -- clearing here (not just setting `loading: true`) means
    // the top-level skeleton gate (`loading && entries.length === 0`) still fires for a provider
    // switch, not just a genuine first load.
    setQueueState({ entries: [], loading: true, error: null })
    // A provider switch invalidates every outstanding request -- their resolution must never be
    // allowed to land, even though `currentProviderRef` alone would already reject them.
    queueGenerationRef.current += 1
    panelGenerationRef.current += 1
  }

  // DH-CLINIC-007 §32: the pending count/queue must never silently undercount. A single
  // `status=ALL` fetch orders oldest-first across *every* status, so once a provider has more than
  // the API's own page size of total obligations, newer PENDING entries could fall off the page
  // while older reviewed ones fill it. Fetching PENDING on its own (its own page of up to 100) is
  // therefore authoritative for the queue/count; a separate ALL fetch (same cap) supplies history.
  // History beyond 100 combined obligations is not yet shown -- an accepted, disclosed limit, not a
  // silent one.
  const QUEUE_PAGE_LIMIT = 100

  const loadQueue = useCallback(() => {
    if (authStatus !== 'ready' || !activeProvider) return
    const generation = ++queueGenerationRef.current
    const targetProvider = activeProvider.organization_id
    const controller = new AbortController()

    setQueueState((prev) => ({ entries: prev.entries, loading: true, error: null }))
    Promise.all([
      client.listResultReviews(targetProvider, {
        status: 'PENDING',
        limit: QUEUE_PAGE_LIMIT,
        signal: controller.signal,
      }),
      client.listResultReviews(targetProvider, {
        status: 'ALL',
        limit: QUEUE_PAGE_LIMIT,
        signal: controller.signal,
      }),
    ])
      .then(([pendingResponse, allResponse]) => {
        if (!mountedRef.current) return
        if (queueGenerationRef.current !== generation) return
        if (currentProviderRef.current !== targetProvider) return
        // The two fetches are not atomic -- an entry can flip PENDING -> terminal between them.
        // Trust the PENDING fetch for membership (it is the one that must never undercount) and
        // exclude by id from the ALL side, rather than trusting each response's own status label,
        // so a flipped entry is never rendered twice under the same review_id.
        const pendingIds = new Set(pendingResponse.entries.map((entry) => entry.review_id))
        const reviewed = allResponse.entries.filter(
          (entry) => entry.review_status !== 'PENDING' && !pendingIds.has(entry.review_id),
        )
        setQueueState({
          entries: [...pendingResponse.entries, ...reviewed],
          loading: false,
          error: null,
        })
      })
      .catch(() => {
        if (controller.signal.aborted) return
        if (!mountedRef.current) return
        if (queueGenerationRef.current !== generation) return
        if (currentProviderRef.current !== targetProvider) return
        setQueueState({
          entries: [],
          loading: false,
          error: 'Erreur lors du chargement des résultats à examiner.',
        })
      })
    return () => controller.abort()
  }, [client, activeProvider, authStatus])

  useEffect(() => {
    return loadQueue()
  }, [loadQueue])

  const pendingEntries = queueState.entries.filter((e) => e.review_status === 'PENDING')
  const reviewedEntries = queueState.entries.filter((e) => e.review_status !== 'PENDING')

  const loadPanel = useCallback(
    (
      target: ReviewTarget,
      expectedPatientId: string | null,
      options: {
        isRecheck?: boolean
        // Fast path: switching between already-known versions (§35-37) never needs to re-fetch
        // the version history itself, so the strip never flickers away mid-switch. Never passed
        // for a recheck -- a recheck always re-derives history fresh (see below) so its labels
        // (e.g. a version's own review-status badge) reflect what the mutation actually recorded.
        knownVersions?: ProviderReleaseVersionSummary[] | null
        // Keep the patient banner and version strip mounted (as a placeholder, pending the fresh
        // fetch) instead of falling back to the whole-panel skeleton -- true for a version switch
        // (implied by `knownVersions`) and explicitly for a post-mutation recheck (§70/§71).
        preserveContext?: boolean
      } = {},
    ) => {
      if (!activeProvider) return
      const targetProvider = activeProvider.organization_id
      const generation = ++panelGenerationRef.current
      const isRecheck = options.isRecheck ?? false
      const knownVersions = options.knownVersions ?? null
      const preserveContext = options.preserveContext ?? knownVersions !== null

      setPanel((prev) => ({
        target,
        initializing: !preserveContext,
        contentLoading: true,
        checking: isRecheck,
        error: null,
        patient: preserveContext ? (prev?.patient ?? null) : null,
        versions: knownVersions ?? (preserveContext ? (prev?.versions ?? null) : null),
        content: null,
        legacyContent: null,
      }))
      // A recheck after an ambiguous/conflicting mutation must keep the explanatory message on
      // screen (§70/§71) -- only a genuine fresh open resets the disposition/submit state.
      if (!isRecheck) {
        setDisposition(null)
        setSubmitState({ submitting: false, error: null })
      }

      const fetchVersionContent = (versions: ProviderReleaseVersionSummary[]) => {
        const resolvedVersionId =
          target.kind === 'version' ? target.versionId : versions[versions.length - 1].release_version_id

        client
          .getReleaseVersion(targetProvider, target.orderId, resolvedVersionId)
          .then((content) => {
            if (!mountedRef.current) return
            if (panelGenerationRef.current !== generation) return
            if (currentProviderRef.current !== targetProvider) return
            if (
              content.order_id !== target.orderId ||
              content.release_version_id !== resolvedVersionId ||
              (expectedPatientId !== null && content.patient.patient_id !== expectedPatientId)
            ) {
              setPanel((prev) =>
                prev && targetsEqual(prev.target, target)
                  ? { ...prev, initializing: false, contentLoading: false, error: RESULT_CONSISTENCY_ERROR }
                  : prev,
              )
              return
            }
            setPanel((prev) =>
              prev && targetsEqual(prev.target, target)
                ? {
                    ...prev,
                    target: { kind: 'version', orderId: target.orderId, versionId: resolvedVersionId },
                    initializing: false,
                    contentLoading: false,
                    error: null,
                    patient: content.patient,
                    versions,
                    content,
                  }
                : prev,
            )
          })
          .catch(() => {
            if (!mountedRef.current) return
            if (panelGenerationRef.current !== generation) return
            if (currentProviderRef.current !== targetProvider) return
            setPanel((prev) =>
              prev && targetsEqual(prev.target, target)
                ? { ...prev, initializing: false, contentLoading: false, error: RESULT_LOAD_ERROR }
                : prev,
            )
          })
      }

      const fetchLegacyContent = () => {
        client
          .getOrderResults(targetProvider, target.orderId)
          .then((legacy) => {
            if (!mountedRef.current) return
            if (panelGenerationRef.current !== generation) return
            if (currentProviderRef.current !== targetProvider) return
            if (
              legacy.order_id !== target.orderId ||
              (expectedPatientId !== null && legacy.patient.patient_id !== expectedPatientId)
            ) {
              setPanel((prev) =>
                prev && targetsEqual(prev.target, target)
                  ? { ...prev, initializing: false, contentLoading: false, error: RESULT_CONSISTENCY_ERROR }
                  : prev,
              )
              return
            }
            setPanel((prev) =>
              prev && targetsEqual(prev.target, target)
                ? {
                    ...prev,
                    initializing: false,
                    contentLoading: false,
                    error: null,
                    patient: legacy.patient,
                    versions: [],
                    legacyContent: legacy,
                  }
                : prev,
            )
          })
          .catch(() => {
            if (!mountedRef.current) return
            if (panelGenerationRef.current !== generation) return
            if (currentProviderRef.current !== targetProvider) return
            setPanel((prev) =>
              prev && targetsEqual(prev.target, target)
                ? { ...prev, initializing: false, contentLoading: false, error: RESULT_LOAD_ERROR }
                : prev,
            )
          })
      }

      if (knownVersions !== null) {
        if (knownVersions.length > 0) {
          fetchVersionContent(knownVersions)
        } else {
          fetchLegacyContent()
        }
        return
      }

      client
        .getReleaseVersions(targetProvider, target.orderId)
        .then((history) => {
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentProviderRef.current !== targetProvider) return
          if (history.order_id !== target.orderId) {
            setPanel((prev) =>
              prev && targetsEqual(prev.target, target)
                ? { ...prev, initializing: false, contentLoading: false, error: RESULT_CONSISTENCY_ERROR }
                : prev,
            )
            return
          }

          if (history.versions.length === 0) {
            if (target.kind === 'legacy') {
              // Genuinely legacy/unversioned (§7/§51) -- fall back to the live/legacy read.
              fetchLegacyContent()
            } else {
              // A versioned target whose freshly-read history now shows no versions at all is a
              // genuine consistency failure -- never silently reinterpreted as legacy.
              setPanel((prev) =>
                prev && targetsEqual(prev.target, target)
                  ? { ...prev, initializing: false, contentLoading: false, error: RESULT_CONSISTENCY_ERROR }
                  : prev,
              )
            }
            return
          }

          if (target.kind === 'legacy') {
            // The caller believed this order was legacy/unversioned, but fresh history disagrees
            // -- fail closed (§5) rather than silently rendering whichever version is "latest".
            setPanel((prev) =>
              prev && targetsEqual(prev.target, target)
                ? { ...prev, initializing: false, contentLoading: false, error: RESULT_CONSISTENCY_ERROR }
                : prev,
            )
            return
          }

          fetchVersionContent(history.versions)
        })
        .catch(() => {
          if (!mountedRef.current) return
          if (panelGenerationRef.current !== generation) return
          if (currentProviderRef.current !== targetProvider) return
          setPanel((prev) =>
            prev && targetsEqual(prev.target, target)
              ? { ...prev, initializing: false, contentLoading: false, error: RESULT_LOAD_ERROR }
              : prev,
          )
        })
    },
    [client, activeProvider],
  )

  const handleOpenFromQueueEntry = (entry: ProviderResultReviewQueueEntry) => {
    const target: ReviewTarget = entry.legacy_unversioned
      ? { kind: 'legacy', orderId: entry.order_id }
      : { kind: 'version', orderId: entry.order_id, versionId: entry.release_version_id as string }
    loadPanel(target, entry.patient.patient_id)
  }

  const handleSwitchVersion = (versionId: string) => {
    if (!panel || !panel.versions) return
    loadPanel(
      { kind: 'version', orderId: panel.target.orderId, versionId },
      panel.patient?.patient_id ?? null,
      { knownVersions: panel.versions },
    )
  }

  const handleClosePanel = () => {
    panelGenerationRef.current += 1
    setPanel(null)
    setDisposition(null)
    setSubmitState({ submitting: false, error: null })
  }

  const reReadPanelAfterAmbiguousMutation = (target: ReviewTarget) => {
    // DH-CLINIC-007 §70/§71: never assume success or blindly re-POST -- re-read the authoritative
    // state and let it speak for itself, whether that turns out to be terminal or still PENDING.
    // Always re-derives the version history fresh (never the `knownVersions` fast path) so a
    // version's own review-status label reflects what the mutation actually recorded; the patient
    // banner and version strip still stay mounted throughout via `preserveContext`.
    loadPanel(target, panel?.patient?.patient_id ?? null, { isRecheck: true, preserveContext: true })
  }

  const handleSubmitReview = () => {
    if (!panel || !disposition || !activeProvider) return
    const targetProvider = activeProvider.organization_id
    const target = panel.target
    const generation = panelGenerationRef.current

    setSubmitState({ submitting: true, error: null })

    const call: Promise<DoctorReviewResponse> =
      target.kind === 'version'
        ? disposition === 'NO_FOLLOW_UP_REQUIRED'
          ? client.reviewReleaseVersionNoFollowUp(targetProvider, target.orderId, target.versionId)
          : client.reviewReleaseVersionFollowUp(targetProvider, target.orderId, target.versionId)
        : disposition === 'NO_FOLLOW_UP_REQUIRED'
          ? client.reviewLegacyOrderNoFollowUp(targetProvider, target.orderId)
          : client.reviewLegacyOrderFollowUp(targetProvider, target.orderId)

    call
      .then((response) => {
        if (!mountedRef.current) return
        if (panelGenerationRef.current !== generation) return
        if (currentProviderRef.current !== targetProvider) return
        // §43: validate the response matches what was requested -- order, version, and the exact
        // disposition -- before treating the mutation as a success. Never display success on a
        // response that doesn't provably confirm what was asked for.
        const expectedVersionId = target.kind === 'version' ? target.versionId : null
        if (
          response.order_id !== target.orderId ||
          response.release_version_id !== expectedVersionId ||
          response.status !== disposition
        ) {
          setSubmitState({ submitting: false, error: RESULT_CONSISTENCY_ERROR })
          loadQueue()
          reReadPanelAfterAmbiguousMutation(target)
          return
        }
        setSubmitState({ submitting: false, error: null })
        setDisposition(null)
        loadQueue()
        reReadPanelAfterAmbiguousMutation(target)
      })
      .catch((err: unknown) => {
        if (!mountedRef.current) return
        if (panelGenerationRef.current !== generation) return
        if (currentProviderRef.current !== targetProvider) return
        if (err instanceof ExchangeApiError && err.status === 409) {
          // Already finalized -- by this same clinician's own lost-earlier-response, or by a
          // concurrent second clinician (§38-40). Never overwritten; re-read and show the truth.
          setSubmitState({ submitting: false, error: REVIEW_ALREADY_FINALIZED_NOTICE })
        } else {
          setSubmitState({ submitting: false, error: REVIEW_UNCONFIRMED_ERROR })
        }
        loadQueue()
        reReadPanelAfterAmbiguousMutation(target)
      })
  }

  if (authStatus === 'loading') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <LoadingSkeleton />
      </div>
    )
  }

  if (authStatus === 'unauthenticated') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState title="Session non authentifiée">
          Veuillez vous connecter pour consulter les résultats libérés.
        </EmptyState>
      </div>
    )
  }

  if (authStatus === 'no_clinics') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState title="Aucune organisation clinique active">
          Votre compte n'a pas accès à un établissement de soins prescripteur.
        </EmptyState>
      </div>
    )
  }

  const displayedContent =
    panel?.content ?? (panel?.legacyContent
      ? {
          order_id: panel.legacyContent.order_id,
          release_version_id: null,
          release_version_number: null,
          is_amendment: false,
          patient: panel.legacyContent.patient,
          review_status: null as string | null,
          reviewed_at: null as string | null,
          results: panel.legacyContent.results,
        }
      : null)
  const displayedReviewStatus =
    panel?.content?.review_status ??
    queueState.entries.find(
      (e) => e.order_id === panel?.target.orderId && e.legacy_unversioned,
    )?.review_status ??
    null
  const displayedReviewedAt =
    panel?.content?.reviewed_at ??
    queueState.entries.find(
      (e) => e.order_id === panel?.target.orderId && e.legacy_unversioned,
    )?.reviewed_at ??
    null

  return (
    <div style={{ padding: 'var(--space-5)', maxWidth: '1000px', margin: '0 auto' }}>
      <header style={{ marginBottom: 'var(--space-5)' }}>
        <h1 style={{ margin: 0, fontSize: 'var(--font-size-2xl)', color: 'var(--color-neutral-900)' }}>
          Résultats de laboratoire
        </h1>
        {activeProvider ? (
          <p style={{ margin: 'var(--space-1) 0 0', color: 'var(--color-neutral-600)' }}>
            Résultats libérés pour : <strong>{activeProvider.organization_name}</strong>
          </p>
        ) : null}
      </header>

      {queueState.loading && queueState.entries.length === 0 ? (
        <LoadingSkeleton />
      ) : queueState.error && queueState.entries.length === 0 ? (
        <ErrorState code="NETWORK_ERROR" onRetry={() => void reload()} />
      ) : queueState.entries.length === 0 ? (
        <EmptyState title="Aucun résultat disponible">
          Aucun ordre de laboratoire avec des résultats validés et libérés n'a été trouvé.
        </EmptyState>
      ) : (
        <div style={{ display: 'grid', gap: 'var(--space-5)' }}>
          <Card>
            <div style={{ padding: 'var(--space-4)' }}>
              <h2 style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--font-size-lg)' }}>
                Résultats à examiner ({pendingEntries.length})
              </h2>
              {pendingEntries.length === 0 ? (
                <p style={{ color: 'var(--color-neutral-600)', margin: 0 }}>
                  Aucun résultat en attente de revue.
                </p>
              ) : (
                <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 'var(--space-2)' }}>
                  {pendingEntries.map((entry) => {
                    const isOpenEntry =
                      panel !== null &&
                      panel.target.orderId === entry.order_id &&
                      (panel.target.kind !== 'version' || panel.target.versionId === entry.release_version_id)
                    return (
                    <li
                      key={entry.review_id}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        gap: 'var(--space-3)',
                        padding: 'var(--space-3)',
                        border: '1px solid var(--color-neutral-200)',
                        borderRadius: 'var(--radius-md, 6px)',
                        background: isOpenEntry ? 'var(--color-surface-alt, #f9fafb)' : undefined,
                      }}
                    >
                      <div>
                        <PatientIdentity patient={entry.patient} />
                        <p style={{ margin: 'var(--space-1) 0 0', fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                          <span>
                            {entry.legacy_unversioned
                              ? 'Résultat historique — aucune version de libération enregistrée'
                              : formatVersionLabel(entry.release_version_number as number, entry.is_amendment)}
                          </span>
                          {entry.released_at ? <> · Libéré le {formatDateTime(entry.released_at)}</> : null}
                        </p>
                        <p style={{ margin: 'var(--space-1) 0 0', fontSize: 'var(--font-size-xs, 0.75rem)', color: 'var(--color-neutral-500)' }}>
                          Référence de l'ordre : <code>{entry.order_id}</code>
                        </p>
                      </div>
                      <Button
                        type="button"
                        onClick={() => handleOpenFromQueueEntry(entry)}
                        aria-label={`Examiner le résultat de ${entry.patient.display_name?.trim() || 'Nom non renseigné'}`}
                      >
                        Examiner
                      </Button>
                    </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </Card>

          <Card>
            <div style={{ padding: 'var(--space-4)' }}>
              <h2 style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--font-size-lg)' }}>
                Historique ({reviewedEntries.length})
              </h2>
              {reviewedEntries.length === 0 ? (
                <p style={{ color: 'var(--color-neutral-600)', margin: 0 }}>Aucune revue enregistrée.</p>
              ) : (
                <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 'var(--space-2)' }}>
                  {reviewedEntries.map((entry) => (
                    <li
                      key={entry.review_id}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        gap: 'var(--space-3)',
                        padding: 'var(--space-3)',
                        border: '1px solid var(--color-neutral-100)',
                        borderRadius: 'var(--radius-md, 6px)',
                      }}
                    >
                      <div>
                        <PatientIdentity patient={entry.patient} />
                        <p style={{ margin: 'var(--space-1) 0 0', fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                          <span>
                            {entry.legacy_unversioned
                              ? 'Résultat historique'
                              : formatVersionLabel(entry.release_version_number as number, entry.is_amendment)}
                          </span>
                          {entry.reviewed_at ? <> · Revu le {formatDateTime(entry.reviewed_at)}</> : null}
                        </p>
                        <p style={{ margin: 'var(--space-1) 0 0', fontSize: 'var(--font-size-xs, 0.75rem)', color: 'var(--color-neutral-500)' }}>
                          Référence de l'ordre : <code>{entry.order_id}</code>
                        </p>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                        <StatusBadge
                          tone={reviewStatusTone(entry.review_status)}
                          label={formatReviewStatusLabel(entry.review_status)}
                        />
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={() => handleOpenFromQueueEntry(entry)}
                          aria-label={`Consulter le résultat de ${entry.patient.display_name?.trim() || 'Nom non renseigné'}`}
                        >
                          Consulter
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
                <Button variant="secondary" onClick={handleClosePanel} disabled={submitState.submitting}>
                  Fermer le détail
                </Button>
              </div>

              {submitState.error ? (
                <p role="alert" style={{ color: 'var(--color-danger, #b91c1c)', marginBottom: 'var(--space-3)' }}>
                  {submitState.error}
                </p>
              ) : null}

              {panel.initializing ? (
                <LoadingSkeleton />
              ) : panel.error ? (
                <p role="alert" style={{ color: 'var(--color-danger, #b91c1c)' }}>
                  {panel.error}
                </p>
              ) : panel.patient ? (
                <Card>
                  <div style={{ padding: 'var(--space-4)' }}>
                    {/* §21/§38: patient, then version identity, then result content, then the
                        review decision -- always in this order, never the lab/version alone. The
                        patient banner and version strip below stay mounted across a version
                        switch or a post-mutation recheck -- only the content area beneath them
                        toggles to a loading/checking message (§16/§35). */}
                    <div
                      style={{
                        paddingBottom: 'var(--space-3)',
                        marginBottom: 'var(--space-3)',
                        borderBottom: '1px solid var(--color-neutral-200)',
                      }}
                    >
                      <PatientIdentity patient={panel.patient} variant="full" />
                    </div>

                    {panel.versions && panel.versions.length > 0 ? (
                      <div
                        role="tablist"
                        aria-label="Historique des versions de résultat"
                        style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}
                      >
                        {panel.versions.map((version) => (
                          <button
                            key={version.release_version_id}
                            type="button"
                            role="tab"
                            aria-selected={
                              panel.target.kind === 'version' &&
                              panel.target.versionId === version.release_version_id
                            }
                            onClick={() => handleSwitchVersion(version.release_version_id)}
                            style={{
                              padding: 'var(--space-2) var(--space-3)',
                              borderRadius: 'var(--radius-md, 6px)',
                              border: '1px solid var(--color-neutral-300)',
                              background:
                                panel.target.kind === 'version' &&
                                panel.target.versionId === version.release_version_id
                                  ? 'var(--color-surface-alt, #eef2ff)'
                                  : 'transparent',
                              cursor: 'pointer',
                            }}
                          >
                            {formatVersionLabel(version.release_version_number, version.is_amendment)}
                            {' — '}
                            {formatReviewStatusLabel(version.review_status)}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                        Résultat historique — aucune version de libération enregistrée.
                      </p>
                    )}

                    {panel.contentLoading ? (
                      panel.checking ? (
                        <p role="status">Vérification de l'état de la revue…</p>
                      ) : (
                        <p role="status">Chargement de la version…</p>
                      )
                    ) : displayedContent ? (
                      <>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-3)' }}>
                          <h2 style={{ margin: 0, fontSize: 'var(--font-size-lg)' }}>
                            Ordre{' '}
                            <span style={{ fontFamily: 'monospace', fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-500)' }}>
                              {displayedContent.order_id}
                            </span>
                          </h2>
                          <StatusBadge
                            tone={reviewStatusTone(displayedReviewStatus)}
                            label={formatReviewStatusLabel(displayedReviewStatus)}
                          />
                        </div>

                        {displayedContent.results.length === 0 ? (
                          <p style={{ color: 'var(--color-neutral-500)', fontStyle: 'italic' }}>
                            Aucun élément d'observation contenu dans cette version de libération.
                          </p>
                        ) : (
                          displayedContent.results.map((item) => (
                            <div
                              key={item.item_id}
                              style={{
                                borderTop: '1px solid var(--color-neutral-200)',
                                paddingTop: 'var(--space-3)',
                                marginTop: 'var(--space-3)',
                              }}
                            >
                              <h3 style={{ fontSize: 'var(--font-size-base)', margin: '0 0 var(--space-2)', color: 'var(--color-neutral-800)' }}>
                                {item.catalog_display_name_snapshot} ({item.catalog_code_snapshot})
                              </h3>

                              <table
                                style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--font-size-sm)' }}
                              >
                                <thead>
                                  <tr style={{ borderBottom: '1px solid var(--color-neutral-200)', textAlign: 'left' }}>
                                    <th style={{ padding: 'var(--space-2) var(--space-3)', fontWeight: 600 }}>Observation</th>
                                    <th style={{ padding: 'var(--space-2) var(--space-3)', fontWeight: 600 }}>Valeur</th>
                                    <th style={{ padding: 'var(--space-2) var(--space-3)', fontWeight: 600 }}>Unité</th>
                                    <th style={{ padding: 'var(--space-2) var(--space-3)', fontWeight: 600 }}>Normes</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {item.observations.map((obs) => (
                                    <tr key={obs.observation_id} style={{ borderBottom: '1px solid var(--color-neutral-100)' }}>
                                      <td style={{ padding: 'var(--space-2) var(--space-3)' }}>{obs.display_label_snapshot}</td>
                                      <td style={{ padding: 'var(--space-2) var(--space-3)', fontWeight: 600 }}>
                                        {obs.numeric_value !== null ? obs.numeric_value : obs.text_value ?? '-'}
                                      </td>
                                      <td style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--color-neutral-600)' }}>
                                        {obs.numeric_unit ?? '-'}
                                      </td>
                                      <td style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--color-neutral-600)' }}>
                                        {obs.reference_range_low !== null && obs.reference_range_high !== null
                                          ? `${obs.reference_range_low} - ${obs.reference_range_high}`
                                          : '-'}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          ))
                        )}

                        <div
                          style={{
                            marginTop: 'var(--space-4)',
                            paddingTop: 'var(--space-4)',
                            borderTop: '1px solid var(--color-neutral-200)',
                          }}
                        >
                          {displayedReviewStatus === null ? (
                            // §5/§57: a queue entry this order/version should have is missing from
                            // the reloaded queue (transient reload failure, or beyond the page
                            // cap) -- render neither a fabricated "PENDING" nor a fabricated
                            // "recorded" state, and show no review controls until it is known.
                            <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                              État de la revue indisponible pour le moment.
                            </p>
                          ) : displayedReviewStatus !== 'PENDING' ? (
                            <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                              Revue enregistrée{displayedReviewedAt ? ` le ${formatDateTime(displayedReviewedAt)}` : ''} —{' '}
                              <strong>{formatReviewStatusLabel(displayedReviewStatus)}</strong>.
                            </p>
                          ) : (
                            <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
                              <h3 style={{ margin: 0, fontSize: 'var(--font-size-base)' }}>
                                Décision de revue clinique
                              </h3>
                              <div
                                role="radiogroup"
                                aria-label="Décision de revue"
                                style={{ display: 'grid', gap: 'var(--space-2)' }}
                              >
                                <label style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                                  <input
                                    type="radio"
                                    name="review-disposition"
                                    value="NO_FOLLOW_UP_REQUIRED"
                                    checked={disposition === 'NO_FOLLOW_UP_REQUIRED'}
                                    onChange={() => setDisposition('NO_FOLLOW_UP_REQUIRED')}
                                    disabled={submitState.submitting}
                                  />
                                  Aucun suivi requis
                                </label>
                                <label style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                                  <input
                                    type="radio"
                                    name="review-disposition"
                                    value="FOLLOW_UP_REQUIRED"
                                    checked={disposition === 'FOLLOW_UP_REQUIRED'}
                                    onChange={() => setDisposition('FOLLOW_UP_REQUIRED')}
                                    disabled={submitState.submitting}
                                  />
                                  Suivi requis
                                </label>
                              </div>

                              {disposition ? (
                                <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                                  {disposition === 'FOLLOW_UP_REQUIRED'
                                    ? FOLLOW_UP_SCOPE_DISCLOSURE + ' '
                                    : ''}
                                  {IMMUTABILITY_DISCLOSURE}
                                </p>
                              ) : null}

                              {/* DH-MVP-POLISH-001 — two gestures, and no modal.
                                  A review records a durable clinical disposition, so it must
                                  not be reachable by one stray click. It is not: the
                                  disposition must be chosen first, and until it is, the
                                  confirm control below is inert. That is the whole
                                  protection — deliberately not a dialog, because a clinician
                                  reviewing a queue of results would dismiss the twentieth
                                  one without reading it, which protects nobody.
                                  What the second gesture adds is a restatement of its own
                                  subject: which decision, about which patient, on which
                                  released version. It is tied to the button with
                                  `aria-describedby`, so it is announced as part of pressing
                                  it rather than being left for a sighted reader to notice. */}
                              {disposition ? (
                                <p
                                  id="review-subject-restatement"
                                  style={{
                                    margin: 0,
                                    padding: 'var(--space-3)',
                                    fontSize: 'var(--font-size-sm)',
                                    color: 'var(--color-text)',
                                    background: 'var(--color-surface-alt)',
                                    border: '1px solid var(--color-border)',
                                    borderRadius: 'var(--radius-sm)',
                                  }}
                                >
                                  Vous allez enregistrer{' '}
                                  <strong>
                                    {disposition === 'FOLLOW_UP_REQUIRED'
                                      ? 'Suivi requis'
                                      : 'Aucun suivi requis'}
                                  </strong>{' '}
                                  pour{' '}
                                  <strong>
                                    {displayedContent?.patient.display_name?.trim() ||
                                      'Nom non renseigné'}
                                  </strong>
                                  {/* The clinician-facing word is "rectificatif"; the version
                                      number is kept, as a precise reference rather than a label. */}
                                  {typeof displayedContent?.release_version_number === 'number'
                                    ? displayedContent.is_amendment
                                      ? `, rectificatif (version ${displayedContent.release_version_number})`
                                      : `, version ${displayedContent.release_version_number}`
                                    : ''}
                                  .
                                </p>
                              ) : null}

                              <div>
                                <Button
                                  type="button"
                                  onClick={handleSubmitReview}
                                  disabled={!disposition || submitState.submitting}
                                  aria-describedby={
                                    disposition ? 'review-subject-restatement' : undefined
                                  }
                                >
                                  {submitState.submitting ? 'Enregistrement…' : 'Confirmer la revue'}
                                </Button>
                              </div>
                            </div>
                          )}
                        </div>
                      </>
                    ) : null}
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
