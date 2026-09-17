import { useEffect, useRef, useState } from 'react'
import type {
  LabOrderSummary,
  ProviderOrderResults,
  ProviderReleaseVersionHistory,
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

function formatReviewStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'PENDING':
      return 'À examiner'
    case 'NO_FOLLOW_UP_REQUIRED':
      return 'Sans suite requise'
    case 'FOLLOW_UP_REQUIRED':
      return 'Suivi clinique requis'
    default:
      return status || 'Non renseigné'
  }
}

// DH-CLINIC-005 §57: a consistency-check failure must never leak which context disagreed with
// which -- a single fixed, generic message, never interpolated with an order/patient identifier.
const RESULT_CONSISTENCY_ERROR =
  'Impossible de vérifier de manière sûre le résultat demandé.'
const RESULT_LOAD_ERROR = 'Impossible de charger le détail des résultats.'

interface OrderDetail {
  requestedOrderId: string
  providerId: string
  results: ProviderOrderResults
  releaseHistory: ProviderReleaseVersionHistory
}

export function ClinicResultsPage() {
  const { status: authStatus, activeProvider, client, reload } = useClinic()
  const [completedOrdersState, setCompletedOrdersState] = useState<{
    completedOrders: LabOrderSummary[]
    loading: boolean
    error: string | null
  }>({
    completedOrders: [],
    loading: true,
    error: null,
  })

  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null)
  // DH-CLINIC-005 §28/§29: both results-read requests resolve into one atomic state update --
  // `detail` either reflects a single, mutually-consistent (results, releaseHistory) pair for the
  // exact order that was requested, or it is null. There is no intermediate shape where one half
  // is stale and the other is current.
  const [detail, setDetail] = useState<OrderDetail | null>(null)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [detailsError, setDetailsError] = useState<string | null>(null)

  // DH-CLINIC-005 §29: every in-flight detail request is bound to the generation that started it,
  // the provider that started it, and (via `mountedRef`) whether the page is still mounted at all.
  // A response may only update state if all three still match at resolution time.
  const detailGenerationRef = useRef(0)
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
    setSelectedOrderId(null)
    setDetail(null)
    setDetailsError(null)
    // A provider switch invalidates any outstanding detail request -- its resolution must never
    // be allowed to land, even though `currentProviderRef` alone would already reject it.
    detailGenerationRef.current += 1
  }

  useEffect(() => {
    if (authStatus !== 'ready' || !activeProvider) {
      return
    }

    let isMounted = true

    client
      .listOrders(activeProvider.organization_id, { limit: 50 })
      .then((data) => {
        if (isMounted) {
          const completed = data.filter((o) => o.status === 'COMPLETED')
          setCompletedOrdersState({
            completedOrders: completed,
            loading: false,
            error: null,
          })
        }
      })
      .catch((err) => {
        if (isMounted) {
          setCompletedOrdersState({
            completedOrders: [],
            loading: false,
            error: err instanceof Error ? err.message : 'Erreur lors du chargement des résultats.',
          })
        }
      })

    return () => {
      isMounted = false
    }
  }, [client, activeProvider, authStatus])

  const { completedOrders, loading, error } = completedOrdersState

  const handleSelectOrder = (orderId: string) => {
    if (!activeProvider) return
    const targetProviderId = activeProvider.organization_id
    const generation = ++detailGenerationRef.current
    // The list row's own patient identity, resolved independently by the list-read API -- used
    // below as a cross-check (§31) against the detail read's own patient identity, never trusted
    // over it.
    const listRow = completedOrders.find((order) => order.id === orderId) ?? null

    setSelectedOrderId(orderId)
    setDetail(null)
    setDetailsLoading(true)
    setDetailsError(null)

    Promise.all([
      client.getOrderResults(targetProviderId, orderId),
      client.getReleaseVersions(targetProviderId, orderId),
    ])
      .then(([results, releaseHistory]) => {
        if (!mountedRef.current) return
        if (detailGenerationRef.current !== generation) return
        if (currentProviderRef.current !== targetProviderId) return

        // §30: the response itself, never the request that was sent, is the authority on which
        // order it describes -- both reads must agree with what was actually requested.
        if (results.order_id !== orderId || releaseHistory.order_id !== orderId) {
          setDetailsError(RESULT_CONSISTENCY_ERROR)
          setDetailsLoading(false)
          return
        }
        // §31: the detail read's own patient identity must agree with the list row that was
        // clicked -- LabOrder's patient_reference_id is immutable once created, so any
        // disagreement here is a genuine inconsistency, never assembled into one screen.
        if (listRow && results.patient.patient_id !== listRow.patient.patient_id) {
          setDetailsError(RESULT_CONSISTENCY_ERROR)
          setDetailsLoading(false)
          return
        }

        setDetail({ requestedOrderId: orderId, providerId: targetProviderId, results, releaseHistory })
        setDetailsLoading(false)
      })
      .catch(() => {
        if (!mountedRef.current) return
        if (detailGenerationRef.current !== generation) return
        if (currentProviderRef.current !== targetProviderId) return
        setDetailsError(RESULT_LOAD_ERROR)
        setDetailsLoading(false)
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

      {loading ? (
        <LoadingSkeleton />
      ) : error ? (
        <ErrorState code="NETWORK_ERROR" onRetry={() => void reload()} />
      ) : completedOrders.length === 0 ? (
        <EmptyState title="Aucun résultat disponible">
          Aucun ordre de laboratoire avec des résultats validés et libérés n'a été trouvé.
        </EmptyState>
      ) : (
        <div style={{ display: 'grid', gap: 'var(--space-5)' }}>
          {/* The list stays visible and clickable at all times -- selecting a different order
              while one is still loading is a normal interaction, not an edge case (DH-CLINIC-005
              §56 RR1): each click starts its own tracked request, and only the most recent one for
              the current provider can ever update `detail`. */}
          <Card>
            <div style={{ overflowX: 'auto' }}>
              <table
                style={{
                  width: '100%',
                  borderCollapse: 'collapse',
                  textAlign: 'left',
                  fontSize: 'var(--font-size-sm)',
                }}
              >
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--color-neutral-200)' }}>
                    <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>Patient</th>
                    <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>Statut</th>
                    <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>Date d'ordre</th>
                    <th style={{ padding: 'var(--space-3) var(--space-4)', textAlign: 'right' }}>Action</th>
                    <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600, textAlign: 'right' }}>
                      Référence de l'ordre
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {completedOrders.map((order) => (
                    <tr
                      key={order.id}
                      style={{
                        borderBottom: '1px solid var(--color-neutral-100)',
                        background:
                          order.id === selectedOrderId ? 'var(--color-surface-alt, #f9fafb)' : undefined,
                      }}
                    >
                      <td style={{ padding: 'var(--space-3) var(--space-4)' }}>
                        <PatientIdentity patient={order.patient} />
                      </td>
                      <td style={{ padding: 'var(--space-3) var(--space-4)' }}>
                        <StatusBadge tone="success" label="Résultats libérés" />
                      </td>
                      <td style={{ padding: 'var(--space-3) var(--space-4)', color: 'var(--color-neutral-600)' }}>
                        {new Date(order.created_at).toLocaleDateString('fr-FR', {
                          year: 'numeric',
                          month: 'short',
                          day: 'numeric',
                        })}
                      </td>
                      <td style={{ padding: 'var(--space-3) var(--space-4)', textAlign: 'right' }}>
                        <Button
                          variant="secondary"
                          onClick={() => handleSelectOrder(order.id)}
                          aria-label={`Consulter les résultats de ${order.patient.display_name?.trim() || 'Nom non renseigné'}`}
                        >
                          Consulter
                        </Button>
                      </td>
                      <td
                        style={{
                          padding: 'var(--space-3) var(--space-4)',
                          textAlign: 'right',
                          fontFamily: 'monospace',
                          fontSize: '0.8rem',
                          color: 'var(--color-neutral-500)',
                        }}
                      >
                        {order.id.slice(0, 8)}…
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {selectedOrderId ? (
            <div>
              <div style={{ marginBottom: 'var(--space-4)' }}>
                <Button variant="secondary" onClick={() => setSelectedOrderId(null)}>
                  Fermer le détail
                </Button>
              </div>

              {detail ? (
                <Card>
                  <div style={{ padding: 'var(--space-4)' }}>
                    {/* DH-CLINIC-005 §24: the patient identity is shown before any clinical
                        content -- a clinician must never have to scroll back or remember whose
                        result this is. */}
                    <div
                      style={{
                        paddingBottom: 'var(--space-3)',
                        marginBottom: 'var(--space-3)',
                        borderBottom: '1px solid var(--color-neutral-200)',
                      }}
                    >
                      <PatientIdentity patient={detail.results.patient} variant="full" />
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-3)' }}>
                      <h2 style={{ margin: 0, fontSize: 'var(--font-size-lg)' }}>
                        Ordre{' '}
                        <span style={{ fontFamily: 'monospace', fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-500)' }}>
                          {detail.results.order_id}
                        </span>
                      </h2>
                      <StatusBadge
                        tone="success"
                        label={
                          detail.results.release_version_number !== null
                            ? `Version ${detail.results.release_version_number}${detail.results.is_amendment ? ' (Amendée)' : ''}`
                            : 'Résultats initiaux'
                        }
                      />
                    </div>

                    {(() => {
                      const currentVersion = detail.results.release_version_id
                        ? detail.releaseHistory.versions.find(
                            (v) => v.release_version_id === detail.results.release_version_id,
                          ) ?? null
                        : detail.releaseHistory.versions[0] ?? null
                      const reviewStatus = currentVersion?.review_status

                      return (
                        <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)', margin: '0 0 var(--space-4)' }}>
                          Statut de revue clinique : <strong>{formatReviewStatusLabel(reviewStatus)}</strong>
                          {detail.releaseHistory.versions.length > 1 ? (
                            <span> ({detail.releaseHistory.versions.length} versions disponibles)</span>
                          ) : null}
                        </p>
                      )
                    })()}

                    {detail.results.results.length === 0 ? (
                      <p style={{ color: 'var(--color-neutral-500)', fontStyle: 'italic' }}>
                        Aucun élément d'observation contenu dans cette version de libération.
                      </p>
                    ) : (
                      detail.results.results.map((item) => (
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
                            style={{
                              width: '100%',
                              borderCollapse: 'collapse',
                              fontSize: 'var(--font-size-sm)',
                            }}
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
                                    {obs.numeric_value !== null
                                      ? obs.numeric_value
                                      : obs.text_value ?? '-'}
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
                  </div>
                </Card>
              ) : detailsLoading ? (
                <LoadingSkeleton />
              ) : detailsError ? (
                // DH-CLINIC-005 §57: the exact, fixed fail-closed string this page produced --
                // never a generic ErrorState code, which would lose the distinction between
                // "network failed" and "the response failed an identity consistency check."
                // Neither ever names an order/patient identifier.
                <p role="alert" style={{ color: 'var(--color-danger, #b91c1c)' }}>
                  {detailsError}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
