import { useEffect, useState } from 'react'
import { mapExchangeErrorToCode } from '../../../api/exchangeClient'
import type { LabOrderSummary } from '../../../api/exchangeTypes'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
} from '../../../design-system'
import { useClinic } from '../ClinicContext'
import type { StaffSectionId } from '../../staff/staffSections'

interface ClinicHomePageProps {
  onNavigate: (section: StaffSectionId) => void
}

export function ClinicHomePage({ onNavigate }: ClinicHomePageProps) {
  const { status, activeProvider, client, error, reload } = useClinic()
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
    if (status !== 'ready' || !activeProvider) {
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
            error: err instanceof Error ? err.message : 'Impossible de charger les ordres.',
          })
        }
      })

    return () => {
      isMounted = false
    }
  }, [client, activeProvider, status])

  const { orders, loading: ordersLoading, error: ordersError } = ordersState

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
          Veuillez vous connecter pour accéder à l'application DHealth Clinique.
        </EmptyState>
      </div>
    )
  }

  if (status === 'no_clinics') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <EmptyState title="Aucune organisation clinique active">
          Votre compte n'est rattaché à aucune organisation de soins avec le rôle prescripteur (CLINICIAN).
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

  const activeOrdersCount = orders.filter(
    (o) => o.status !== 'COMPLETED' && o.status !== 'REJECTED' && o.status !== 'CANCELLED',
  ).length

  const availableResultsCount = orders.filter((o) => o.status === 'COMPLETED').length

  // DHEALTH-LAB-BLOCKED-001. `docs/NATIVE-OPERATING-PRODUCT.md` §27 names the ordering clinician
  // as the next owner of a blocked order, and §16's whole case for the feature is that without
  // it a stalled order sits in a queue state claiming progress nobody is making. "Ordres actifs"
  // is exactly that claim, so counting blocked work only there would reproduce the problem on
  // the clinician's own home screen.
  //
  // Deliberately a *subset*, not a fourth mutually-exclusive bucket: a blocked order genuinely is
  // still outstanding, so it keeps counting toward "Ordres actifs" and is called out again here.
  // The alternative -- subtracting it -- would make the active count drop when a laboratory hits
  // a problem, which reads like progress.
  const blockedOrdersCount = orders.filter((o) => o.status === 'BLOCKED').length

  return (
    <div style={{ padding: 'var(--space-5)', maxWidth: '1000px', margin: '0 auto' }}>
      <header style={{ marginBottom: 'var(--space-5)' }}>
        <h1 style={{ margin: 0, fontSize: 'var(--font-size-2xl)', color: 'var(--color-neutral-900)' }}>
          Accueil Clinique
        </h1>
        {activeProvider ? (
          <p style={{ margin: 'var(--space-1) 0 0', color: 'var(--color-neutral-600)' }}>
            Établissement prescripteur : <strong>{activeProvider.organization_name}</strong>
          </p>
        ) : null}
      </header>

      {ordersLoading ? (
        <LoadingSkeleton />
      ) : ordersError ? (
        <ErrorState
          code="NETWORK_ERROR"
          onRetry={() => {
            if (activeProvider) {
              setOrdersState({ orders: [], loading: true, error: null })
              client
                .listOrders(activeProvider.organization_id, { limit: 50 })
                .then((data) => setOrdersState({ orders: data, loading: false, error: null }))
                .catch((e) =>
                  setOrdersState({
                    orders: [],
                    loading: false,
                    error: e instanceof Error ? e.message : 'Erreur',
                  }),
                )
            }
          }}
        />
      ) : (
        <>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
              gap: 'var(--space-4)',
              marginBottom: 'var(--space-6)',
            }}
          >
            {/* Card 1: Ordres actifs */}
            <Card>
              <div style={{ padding: 'var(--space-4)' }}>
                <span
                  style={{
                    fontSize: 'var(--font-size-sm)',
                    fontWeight: 600,
                    color: 'var(--color-neutral-600)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                  }}
                >
                  Ordres actifs
                </span>
                <p
                  style={{
                    fontSize: 'var(--font-size-3xl)',
                    fontWeight: 700,
                    color: 'var(--color-primary-600)',
                    margin: 'var(--space-2) 0',
                  }}
                >
                  {activeOrdersCount}
                </p>
                <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-500)' }}>
                  En attente, acceptés ou en cours d'analyse
                </p>
                <div style={{ marginTop: 'var(--space-3)' }}>
                  <Button variant="secondary" onClick={() => onNavigate('orders')}>
                    Voir les ordres
                  </Button>
                </div>
              </div>
            </Card>

            {/*
              Card 1b: Bloqués au laboratoire — DHEALTH-LAB-BLOCKED-001.

              Rendered only when there is something to act on. That is the opposite of the Lab's
              own "Bloqués" tile, which always shows even at zero, and the difference is
              deliberate: a laboratory worker needs to confirm at the start of a shift that
              nothing is stuck, whereas a clinician has no such duty and a permanent "0" beside
              their real work is noise. The order list is where a clinician confirms a negative.
            */}
            {blockedOrdersCount > 0 ? (
              <Card>
                <div style={{ padding: 'var(--space-4)' }}>
                  <span
                    style={{
                      fontSize: 'var(--font-size-sm)',
                      fontWeight: 600,
                      color: 'var(--color-neutral-600)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                    }}
                  >
                    Bloqués au laboratoire
                  </span>
                  <p
                    style={{
                      fontSize: 'var(--font-size-3xl)',
                      fontWeight: 700,
                      color: 'var(--color-danger, #b91c1c)',
                      margin: 'var(--space-2) 0',
                    }}
                  >
                    {blockedOrdersCount}
                  </p>
                  <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-500)' }}>
                    Le laboratoire ne peut pas poursuivre ces analyses
                  </p>
                  <div style={{ marginTop: 'var(--space-3)' }}>
                    <Button variant="secondary" onClick={() => onNavigate('orders')}>
                      Voir les ordres bloqués
                    </Button>
                  </div>
                </div>
              </Card>
            ) : null}

            {/* Card 2: Résultats disponibles */}
            <Card>
              <div style={{ padding: 'var(--space-4)' }}>
                <span
                  style={{
                    fontSize: 'var(--font-size-sm)',
                    fontWeight: 600,
                    color: 'var(--color-neutral-600)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                  }}
                >
                  Résultats disponibles
                </span>
                <p
                  style={{
                    fontSize: 'var(--font-size-3xl)',
                    fontWeight: 700,
                    color: 'var(--color-success-700)',
                    margin: 'var(--space-2) 0',
                  }}
                >
                  {availableResultsCount}
                </p>
                <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-500)' }}>
                  Analyses libérées par le laboratoire
                </p>
                <div style={{ marginTop: 'var(--space-3)' }}>
                  <Button variant="secondary" onClick={() => onNavigate('results')}>
                    Consulter les résultats
                  </Button>
                </div>
              </div>
            </Card>

            {/* Card 3: À revoir (Finding F-03: Honest card without N+1 query storm) */}
            <Card>
              <div style={{ padding: 'var(--space-4)' }}>
                <span
                  style={{
                    fontSize: 'var(--font-size-sm)',
                    fontWeight: 600,
                    color: 'var(--color-neutral-600)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                  }}
                >
                  À revoir
                </span>
                <p
                  style={{
                    fontSize: 'var(--font-size-base)',
                    color: 'var(--color-neutral-700)',
                    margin: 'var(--space-2) 0',
                    lineHeight: 1.4,
                  }}
                >
                  Revue des versions libérées et suivi clinique requis
                </p>
                <p style={{ margin: 0, fontSize: 'var(--font-size-xs)', color: 'var(--color-neutral-500)' }}>
                  Le statut de revue est validé par ordre libéré dans l'onglet Résultats.
                </p>
                <div style={{ marginTop: 'var(--space-3)' }}>
                  <Button variant="secondary" onClick={() => onNavigate('results')}>
                    Examiner les revues
                  </Button>
                </div>
              </div>
            </Card>
          </div>

          <section>
            <h2 style={{ fontSize: 'var(--font-size-lg)', marginBottom: 'var(--space-3)' }}>
              Accès rapide
            </h2>
            <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
              <Button variant="primary" onClick={() => onNavigate('orders')}>
                Ordres de laboratoire
              </Button>
              <Button variant="secondary" onClick={() => onNavigate('results')}>
                Résultats
              </Button>
              <Button variant="secondary" onClick={() => onNavigate('patients')}>
                Dossiers patients
              </Button>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
