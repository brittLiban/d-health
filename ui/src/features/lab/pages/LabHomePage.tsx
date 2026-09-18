import { useEffect, useState } from 'react'
import { mapExchangeErrorToCode } from '../../../api/exchangeClient'
import type { LabWorkflowOrder } from '../../../api/exchangeTypes'
import { Card, EmptyState, ErrorState, LoadingSkeleton } from '../../../design-system'
import { useLab } from '../LabContext'

export function LabHomePage() {
  const { status, activeLab, capabilities, client, error, reload } = useLab()
  const [queueState, setQueueState] = useState<{
    entries: LabWorkflowOrder[]
    loading: boolean
    error: string | null
  }>({ entries: [], loading: true, error: null })

  useEffect(() => {
    if (status !== 'ready' || !activeLab || !capabilities.canWorkOrders) {
      return
    }
    let isMounted = true
    // DH-LAB-001 §27: counts are derived client-side from the same bounded queue read the
    // Orders tab uses -- no separate KPI endpoint invented, and the 100-item cap this endpoint
    // already enforces is the same honest limit the count is subject to.
    client
      .listLabOrders(activeLab.organization_id, { limit: 100 })
      .then((entries) => {
        if (isMounted) setQueueState({ entries, loading: false, error: null })
      })
      .catch(() => {
        if (isMounted) {
          setQueueState({ entries: [], loading: false, error: 'Impossible de charger les ordres.' })
        }
      })
    return () => {
      isMounted = false
    }
  }, [client, activeLab, status, capabilities.canWorkOrders])

  if (status === 'loading') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <LoadingSkeleton />
      </div>
    )
  }

  if (status === 'unauthenticated') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState title="Session non authentifiée">
          Veuillez vous connecter pour accéder à l'application DHealth Laboratoire.
        </EmptyState>
      </div>
    )
  }

  if (status === 'no_labs') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState title="Aucun laboratoire actif">
          Aucun laboratoire actif n'est associé à votre compte.
        </EmptyState>
      </div>
    )
  }

  if (status === 'error' && error) {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <ErrorState code={mapExchangeErrorToCode(error.code)} onRetry={() => void reload()} />
      </div>
    )
  }

  return (
    <div style={{ padding: 'var(--space-5)', maxWidth: '1000px', margin: '0 auto' }}>
      <header style={{ marginBottom: 'var(--space-5)' }}>
        <h1 style={{ margin: 0, fontSize: 'var(--font-size-2xl)', color: 'var(--color-neutral-900)' }}>
          Accueil Laboratoire
        </h1>
        {activeLab ? (
          <p style={{ margin: 'var(--space-1) 0 0', color: 'var(--color-neutral-600)' }}>
            Laboratoire actif : <strong>{activeLab.organization_name}</strong>
          </p>
        ) : null}
      </header>

      {!capabilities.canWorkOrders ? (
        <EmptyState title="File de travail non disponible">
          Les fonctions de validation/libération des résultats seront disponibles dans la
          prochaine étape.
        </EmptyState>
      ) : queueState.loading ? (
        <LoadingSkeleton />
      ) : queueState.error ? (
        <ErrorState code="NETWORK_ERROR" />
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: 'var(--space-4)',
          }}
        >
          <Card>
            <div style={{ padding: 'var(--space-4)' }}>
              <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                Nouveaux ordres
              </p>
              <p style={{ margin: 0, fontSize: 'var(--font-size-2xl)', fontWeight: 700 }}>
                {queueState.entries.filter((o) => o.status === 'SELECTED').length}
              </p>
            </div>
          </Card>
          <Card>
            <div style={{ padding: 'var(--space-4)' }}>
              <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                Acceptés
              </p>
              <p style={{ margin: 0, fontSize: 'var(--font-size-2xl)', fontWeight: 700 }}>
                {queueState.entries.filter((o) => o.status === 'ACCEPTED').length}
              </p>
            </div>
          </Card>
          <Card>
            <div style={{ padding: 'var(--space-4)' }}>
              <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                En cours
              </p>
              <p style={{ margin: 0, fontSize: 'var(--font-size-2xl)', fontWeight: 700 }}>
                {queueState.entries.filter((o) => o.status === 'IN_PROGRESS').length}
              </p>
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}
