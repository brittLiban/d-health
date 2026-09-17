import { useEffect, useState } from 'react'
import type { LabOrderStatus, LabOrderSummary } from '../../../api/exchangeTypes'
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  StatusBadge,
  type StatusTone,
} from '../../../design-system'
import { PatientIdentity } from '../components/PatientIdentity'
import { useClinic } from '../ClinicContext'

function mapOrderStatusTone(status: LabOrderStatus): StatusTone {
  switch (status) {
    case 'COMPLETED':
      return 'success'
    case 'IN_PROGRESS':
    case 'ACCEPTED':
      return 'warning'
    case 'SELECTED':
    case 'AWAITING_LAB_SELECTION':
      return 'neutral'
    case 'REJECTED':
    case 'CANCELLED':
      return 'danger'
    default:
      return 'neutral'
  }
}

function formatOrderStatusLabel(status: LabOrderStatus): string {
  switch (status) {
    case 'AWAITING_LAB_SELECTION':
      return 'En attente de choix labo'
    case 'SELECTED':
      return 'Laboratoire sélectionné'
    case 'ACCEPTED':
      return 'Accepté'
    case 'IN_PROGRESS':
      return 'En cours d\'analyse'
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

export function ClinicOrdersPage() {
  const { status: authStatus, activeProvider, client, reload } = useClinic()
  const [ordersState, setOrdersState] = useState<{
    orders: LabOrderSummary[]
    loading: boolean
    error: string | null
  }>({
    orders: [],
    loading: true,
    error: null,
  })

  useEffect(() => {
    if (authStatus !== 'ready' || !activeProvider) {
      return
    }

    let isMounted = true

    client
      .listOrders(activeProvider.organization_id, { limit: 50 })
      .then((data) => {
        if (isMounted) {
          setOrdersState({ orders: data, loading: false, error: null })
        }
      })
      .catch((err) => {
        if (isMounted) {
          setOrdersState({
            orders: [],
            loading: false,
            error: err instanceof Error ? err.message : 'Erreur lors du chargement des ordres.',
          })
        }
      })

    return () => {
      isMounted = false
    }
  }, [client, activeProvider, authStatus])

  const { orders, loading, error } = ordersState

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
          Veuillez vous connecter pour consulter les ordres de laboratoire.
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
          Ordres de laboratoire
        </h1>
        {activeProvider ? (
          <p style={{ margin: 'var(--space-1) 0 0', color: 'var(--color-neutral-600)' }}>
            Prescriptions de l'établissement : <strong>{activeProvider.organization_name}</strong>
          </p>
        ) : null}
      </header>

      {loading ? (
        <LoadingSkeleton />
      ) : error ? (
        <ErrorState code="NETWORK_ERROR" onRetry={() => void reload()} />
      ) : orders.length === 0 ? (
        <EmptyState title="Aucun ordre de laboratoire">
          Aucune prescription diagnostique n'a été trouvée pour cet établissement.
        </EmptyState>
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
                  <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>Patient</th>
                  <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600, textAlign: 'center' }}>
                    Analyses
                  </th>
                  <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>Statut</th>
                  <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>Date de création</th>
                  <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600, textAlign: 'right' }}>
                    Référence de l'ordre
                  </th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr
                    key={order.id}
                    style={{ borderBottom: '1px solid var(--color-neutral-100)' }}
                  >
                    <td style={{ padding: 'var(--space-3) var(--space-4)' }}>
                      <PatientIdentity patient={order.patient} />
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)', textAlign: 'center' }}>
                      {order.item_count}
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)' }}>
                      <StatusBadge
                        tone={mapOrderStatusTone(order.status)}
                        label={formatOrderStatusLabel(order.status)}
                      />
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)', color: 'var(--color-neutral-600)' }}>
                      {new Date(order.created_at).toLocaleDateString('fr-FR', {
                        year: 'numeric',
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
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
      )}
    </div>
  )
}
