import { useEffect, useState } from 'react'
import type {
  LabAssignmentSummary,
  LabOrder,
  LabOrderStatus,
  LabOrderSummary,
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
import { LabRoutingStep } from '../components/LabRoutingStep'
import { PatientAccessHandoff } from '../components/PatientAccessHandoff'
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
      // DH-CLINIC-006 §35: distinct from ACCEPTED's own meaning (the lab explicitly accepting) --
      // never implies the lab has accepted, collected a specimen, or started analysis.
      return 'Laboratoire destinataire sélectionné'
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

  // DH-CLINIC-006 §37: an AWAITING_LAB_SELECTION order is routable directly from this page --
  // closes the recovery path for an interrupted wizard/reload/failed assignment/navigation-away,
  // without forcing the clinician back to the Patient screen. `LabRoutingStep` needs the order's
  // full item list (the list row above only carries `item_count`), so opening the panel fetches
  // the full order first; `routingState.orderId` doubles as the stale-response guard -- a late
  // response for an order the clinician has since closed or switched away from is discarded by
  // the functional-update identity check below, never rendered.
  const [routingState, setRoutingState] = useState<{
    orderId: string | null
    order: LabOrder | null
    loading: boolean
    error: string | null
  }>({ orderId: null, order: null, loading: false, error: null })

  const handleOpenRouting = (orderId: string) => {
    if (!activeProvider) return
    const targetProvider = activeProvider.organization_id
    setRoutingState({ orderId, order: null, loading: true, error: null })
    client
      .getOrder(targetProvider, orderId)
      .then((order) => {
        setRoutingState((prev) => (prev.orderId === orderId ? { orderId, order, loading: false, error: null } : prev))
      })
      .catch(() => {
        setRoutingState((prev) =>
          prev.orderId === orderId
            ? { orderId, order: null, loading: false, error: "Impossible de charger cet ordre." }
            : prev,
        )
      })
  }

  const handleCloseRouting = () => {
    setRoutingState({ orderId: null, order: null, loading: false, error: null })
  }

  // DH-PATIENT-001 §11/§57: the patient handoff panel opens against one specific order and needs
  // that order's full item list (the list row carries only `item_count`), so opening it fetches
  // the order first -- the same shape, and the same stale-response discipline, as the routing
  // panel above: `handoffState.orderId` is the identity check, so a late response for an order
  // the clinician has since closed or switched away from is discarded rather than rendered under
  // a different patient (§49).
  const [handoffState, setHandoffState] = useState<{
    orderId: string | null
    order: LabOrder | null
    loading: boolean
    error: string | null
  }>({ orderId: null, order: null, loading: false, error: null })

  const handleOpenHandoff = (orderId: string) => {
    if (!activeProvider) return
    const targetProvider = activeProvider.organization_id
    setRoutingState({ orderId: null, order: null, loading: false, error: null })
    setHandoffState({ orderId, order: null, loading: true, error: null })
    client
      .getOrder(targetProvider, orderId)
      .then((order) => {
        setHandoffState((prev) =>
          prev.orderId === orderId ? { orderId, order, loading: false, error: null } : prev,
        )
      })
      .catch(() => {
        setHandoffState((prev) =>
          prev.orderId === orderId
            ? { orderId, order: null, loading: false, error: 'Impossible de charger cet ordre.' }
            : prev,
        )
      })
  }

  const handleCloseHandoff = () => {
    setHandoffState({ orderId: null, order: null, loading: false, error: null })
  }

  const handleAssigned = (orderId: string, assignment: LabAssignmentSummary) => {
    setOrdersState((prev) => ({
      ...prev,
      orders: prev.orders.map((o) =>
        o.id === orderId ? { ...o, lab_assignment: assignment, status: 'SELECTED' } : o,
      ),
    }))
    handleCloseRouting()
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
        <div style={{ display: 'grid', gap: 'var(--space-5)' }}>
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
                    <th style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: 600 }}>
                      Laboratoire destinataire
                    </th>
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
                      style={{
                        borderBottom: '1px solid var(--color-neutral-100)',
                        background:
                          order.id === routingState.orderId ? 'var(--color-surface-alt, #f9fafb)' : undefined,
                      }}
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
                      <td style={{ padding: 'var(--space-3) var(--space-4)' }}>
                        {order.lab_assignment ? (
                          <div style={{ display: 'grid', gap: 'var(--space-1)' }}>
                            <span>{order.lab_assignment.lab_organization_name}</span>
                            {/*
                              DH-PATIENT-001 §7/CH1/CH2: offered only once a laboratory is
                              assigned. Before that the Exchange would issue a LAB_SELECTION
                              (marketplace-capable) grant, which this launch flow never hands to a
                              patient -- so the control simply does not exist in that state.
                            */}
                            <Button
                              type="button"
                              variant="secondary"
                              onClick={() => handleOpenHandoff(order.id)}
                              aria-label={`Suivi patient pour ${order.patient.display_name?.trim() || 'Nom non renseigné'}`}
                            >
                              Suivi patient
                            </Button>
                          </div>
                        ) : order.status === 'AWAITING_LAB_SELECTION' ? (
                          <div style={{ display: 'grid', gap: 'var(--space-1)' }}>
                            <span style={{ color: 'var(--color-neutral-600)' }}>Non attribué</span>
                            <Button
                              type="button"
                              variant="secondary"
                              onClick={() => handleOpenRouting(order.id)}
                              aria-label={`Choisir un laboratoire pour ${order.patient.display_name?.trim() || 'Nom non renseigné'}`}
                            >
                              Choisir un laboratoire
                            </Button>
                          </div>
                        ) : (
                          <span style={{ color: 'var(--color-neutral-500)' }}>—</span>
                        )}
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

          {handoffState.orderId ? (
            <div>
              <div style={{ marginBottom: 'var(--space-3)' }}>
                <Button type="button" variant="secondary" onClick={handleCloseHandoff}>
                  Fermer
                </Button>
              </div>
              {handoffState.loading ? (
                <LoadingSkeleton label="Chargement de l'ordre…" />
              ) : handoffState.error ? (
                <ErrorState
                  code="NETWORK_ERROR"
                  onRetry={() => handleOpenHandoff(handoffState.orderId as string)}
                />
              ) : handoffState.order && activeProvider ? (
                (() => {
                  const handoffOrder = handoffState.order
                  return (
                    <PatientAccessHandoff
                      key={handoffOrder.id}
                      client={client}
                      providerId={activeProvider.organization_id}
                      orderId={handoffOrder.id}
                      providerName={activeProvider.organization_name}
                      items={handoffOrder.items}
                      labName={handoffOrder.lab_assignment?.lab_organization_name ?? null}
                    />
                  )
                })()
              ) : null}
            </div>
          ) : null}

          {routingState.orderId ? (
            <div>
              <div style={{ marginBottom: 'var(--space-3)' }}>
                <Button type="button" variant="secondary" onClick={handleCloseRouting}>
                  Fermer
                </Button>
              </div>
              {routingState.loading ? (
                <LoadingSkeleton label="Chargement de l'ordre…" />
              ) : routingState.error ? (
                <ErrorState
                  code="NETWORK_ERROR"
                  onRetry={() => handleOpenRouting(routingState.orderId as string)}
                />
              ) : routingState.order && activeProvider ? (
                (() => {
                  const routedOrder = routingState.order
                  return (
                    <LabRoutingStep
                      key={routedOrder.id}
                      client={client}
                      providerId={activeProvider.organization_id}
                      orderId={routedOrder.id}
                      patient={routedOrder.patient}
                      items={routedOrder.items}
                      onAssigned={(assignment) => handleAssigned(routedOrder.id, assignment)}
                    />
                  )
                })()
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
