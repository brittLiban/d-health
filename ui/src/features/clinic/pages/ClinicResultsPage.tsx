import { useEffect, useState } from 'react'
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

export function ClinicResultsPage() {
  const { status: authStatus, activeProvider, client, authToken, reload } = useClinic()
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
  const [orderDetails, setOrderDetails] = useState<ProviderOrderResults | null>(null)
  const [releaseHistory, setReleaseHistory] = useState<ProviderReleaseVersionHistory | null>(null)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [detailsError, setDetailsError] = useState<string | null>(null)

  const [prevOrgId, setPrevOrgId] = useState<string | null>(activeProvider?.organization_id ?? null)
  if (activeProvider && activeProvider.organization_id !== prevOrgId) {
    setPrevOrgId(activeProvider.organization_id)
    setSelectedOrderId(null)
    setOrderDetails(null)
    setReleaseHistory(null)
  }

  useEffect(() => {
    if (authStatus !== 'ready' || !activeProvider) {
      return
    }

    let isMounted = true

    client
      .listOrders(activeProvider.organization_id, { limit: 50, token: authToken ?? undefined })
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
  }, [client, activeProvider, authStatus, authToken])

  const { completedOrders, loading, error } = completedOrdersState

  const handleSelectOrder = (orderId: string) => {
    if (!activeProvider) return
    setSelectedOrderId(orderId)
    setDetailsLoading(true)
    setDetailsError(null)

    Promise.all([
      client.getOrderResults(activeProvider.organization_id, orderId, authToken ?? undefined),
      client.getReleaseVersions(activeProvider.organization_id, orderId, authToken ?? undefined),
    ])
      .then(([res, history]) => {
        setOrderDetails(res)
        setReleaseHistory(history)
        setDetailsLoading(false)
      })
      .catch((err) => {
        setDetailsError(
          err instanceof Error ? err.message : 'Impossible de charger le détail des résultats.',
        )
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
      ) : selectedOrderId && orderDetails ? (
        <div>
          <div style={{ marginBottom: 'var(--space-4)' }}>
            <Button variant="secondary" onClick={() => setSelectedOrderId(null)}>
              ← Retour à la liste des résultats
            </Button>
          </div>

          <Card>
            <div style={{ padding: 'var(--space-4)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-3)' }}>
                <h2 style={{ margin: 0, fontSize: 'var(--font-size-lg)' }}>
                  Ordre {orderDetails.order_id.slice(0, 8)}…
                </h2>
                <StatusBadge
                  tone="success"
                  label={
                    orderDetails.release_version_number !== null
                      ? `Version ${orderDetails.release_version_number}${orderDetails.is_amendment ? ' (Amendée)' : ''}`
                      : 'Résultats initiaux'
                  }
                />
              </div>

              {(() => {
                const currentVersion =
                  orderDetails.release_version_id
                    ? releaseHistory?.versions.find(
                        (v) => v.release_version_id === orderDetails.release_version_id,
                      ) ?? null
                    : releaseHistory?.versions[0] ?? null
                const reviewStatus = currentVersion?.review_status

                return (
                  <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)', margin: '0 0 var(--space-4)' }}>
                    Statut de revue clinique : <strong>{formatReviewStatusLabel(reviewStatus)}</strong>
                    {releaseHistory && releaseHistory.versions.length > 1 ? (
                      <span> ({releaseHistory.versions.length} versions disponibles)</span>
                    ) : null}
                  </p>
                )
              })()}

              {orderDetails.results.length === 0 ? (
                <p style={{ color: 'var(--color-neutral-500)', fontStyle: 'italic' }}>
                  Aucun élément d'observation contenu dans cette version de libération.
                </p>
              ) : (
                orderDetails.results.map((item) => (
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
        </div>
      ) : detailsLoading ? (
        <LoadingSkeleton />
      ) : detailsError ? (
        <div>
          <div style={{ marginBottom: 'var(--space-4)' }}>
            <Button variant="secondary" onClick={() => setSelectedOrderId(null)}>
              ← Retour à la liste des résultats
            </Button>
          </div>
          <ErrorState code="SERVER_ERROR" />
        </div>
      ) : (
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
                  <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>ID Ordre</th>
                  <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>Réf. Patient</th>
                  <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>Statut</th>
                  <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>Date d'ordre</th>
                  <th style={{ padding: 'var(--space-3) var(--space-4)', textAlign: 'right' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {completedOrders.map((order) => (
                  <tr key={order.id} style={{ borderBottom: '1px solid var(--color-neutral-100)' }}>
                    <td style={{ padding: 'var(--space-3) var(--space-4)', fontFamily: 'monospace' }}>
                      {order.id.slice(0, 8)}…
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)', fontFamily: 'monospace' }}>
                      {order.patient_reference_id.slice(0, 8)}…
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
                      <Button variant="secondary" onClick={() => handleSelectOrder(order.id)}>
                        Consulter
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
