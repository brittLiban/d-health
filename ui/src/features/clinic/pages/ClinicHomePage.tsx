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
import { ClinicAppointments } from '../../visits/ClinicAppointments'
import { ClinicArrivals } from '../../visits/ClinicArrivals'
import { Capability, can, capabilitiesFor } from '../../workspace/workspaceCapabilities'

interface ClinicHomePageProps {
  onNavigate: (section: StaffSectionId) => void
  /** DHEALTH-FRONT-DESK-001: opens one visit in the clinical workspace. Passed through to
   * `ClinicArrivals`, which only ever offers the control to somebody holding `visit.read`. */
  onOpenVisit?: (visitId: string) => void
}

export function ClinicHomePage({ onNavigate, onOpenVisit }: ClinicHomePageProps) {
  const { status, activeProvider, client, error, reload } = useClinic()
  const capabilities = capabilitiesFor(activeProvider)
  /**
   * DHEALTH-FRONT-DESK-001/ADR-069. The order tiles below need `order.manage`, and a reception-only
   * membership does not hold it. Fetching anyway would 403 and this page would render `ErrorState`
   * for its whole body -- hiding the arrival surface that is the only reason that person opened the
   * application. So the fetch is conditional, and `Aujourd'hui` shows each person the part of itself
   * their authority can answer.
   */
  const mayManageOrders = can(capabilities, Capability.ORDER_MANAGE)
  const mayWorkArrivals =
    can(capabilities, Capability.VISIT_CHECK_IN) || can(capabilities, Capability.VISIT_DOCUMENT)
  /**
   * DHEALTH-APPOINTMENTS-001/ADR-073. `appointment.manage` **or** `visit.check_in`: a scheduler must
   * see what they maintain, and the desk that records arrivals must see who is expected or the
   * schedule is useless to the one person who most needs it.
   *
   * `visit.document` is deliberately absent. A clinician who should also run the diary is granted
   * `appointment.manage` explicitly -- no role template carries it, because §7 gives clinicians only
   * "LTD -- own schedule" and widening a shipped role past the specification is the decision ADR-073
   * declined to make quietly.
   */
  const maySeeSchedule =
    can(capabilities, Capability.APPOINTMENT_MANAGE) || can(capabilities, Capability.VISIT_CHECK_IN)
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
    if (!mayManageOrders) {
      // Nothing to load and nothing to report: the tiles this would fill are not rendered for a
      // membership without `order.manage`, so a "loading" state that never resolves would be a lie.
      setOrdersState({ orders: [], loading: false, error: null })
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
  }, [client, activeProvider, status, mayManageOrders])

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
        {/* DHEALTH-FRONT-DESK-001/ADR-069: the old wording named the CLINICIAN *role*, which has not
            been the authority since ADR-067 and is actively misleading now that a reception
            membership with no role at all is a supported way to work here. `ClinicProvider` filters
            on organization type, never on a role. */}
        <EmptyState title="Aucune organisation clinique active">
          Votre compte n'est rattaché à aucun établissement de soins. Demandez à un administrateur
          de votre établissement de vous donner accès.
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

      {/*
        DHEALTH-FRONT-DESK-001/ADR-069 — the arrival and waiting surface, and it comes **first.**

        A clinic day begins with who is in the building, so it is above the order tiles rather than
        below them (§24/§46). It is also rendered independently of the order fetch: for a
        reception-only membership that fetch does not happen at all, and a layout that nested the
        waiting list inside the orders block would have hidden it behind a state that never resolves.

        Shown for `visit.check_in` **or** `visit.document`: the desk to run it, the clinician to see
        who is waiting for them. Somebody with neither -- an administrator, a cashier -- sees nothing
        here, which is the honest rendering of having no part in the arrival workflow.
      */}
      {/*
        DHEALTH-APPOINTMENTS-001: the schedule sits **above** the arrivals list, because a clinic day
        is planned before it is attended -- a receptionist opening the morning looks at who is
        expected first. Walk-ins stay immediately below and are never buried behind it, which is the
        ticket's own requirement: in this market arriving without a booking is the common case, not
        the exception.

        Its own capability pair, so an appointment-only scheduler sees the diary and no waiting list,
        and a `visit.document`-only clinician sees the waiting list and no diary. Neither is shown a
        surface their membership cannot act on.
      */}
      {maySeeSchedule ? (
        <div style={{ marginBottom: 'var(--space-6)' }}>
          <ClinicAppointments />
        </div>
      ) : null}
      {mayWorkArrivals ? (
        <div style={{ marginBottom: 'var(--space-6)' }}>
          <ClinicArrivals
            onOpenVisit={onOpenVisit}
            onRegisterPatient={() => onNavigate('patients')}
          />
        </div>
      ) : null}

      {!mayManageOrders ? null : ordersLoading ? (
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
