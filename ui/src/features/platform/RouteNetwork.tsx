/**
 * DH-MVP-ONBOARD-001 §62 — the network relationships of one provider, and the
 * one place production routing is switched on.
 *
 * This surface exists to make three facts visible as three facts, because the
 * whole ticket turns on not merging them:
 *
 *   Relation configurée        a platform admin said this pair may exchange work
 *   Disponibilité opérationnelle  the laboratory can actually do that work today
 *   Destination éligible       both of the above, for a given order, right now
 *
 * A laboratory can therefore show "Relation configurée · Routage indisponible",
 * and that is not a contradiction: the relationship is intact and the lab cannot
 * currently take work. Nothing here rewrites a configured route when staffing
 * changes.
 *
 * Every verdict is the server's. This component decides whether to *offer* the
 * control, which is a courtesy; the backend decides whether the activation may
 * happen, which is the rule. When the two disagree — readiness changed between
 * the screen being drawn and the button being pressed — the server's 409 wins and
 * its reasons are what the operator reads.
 */

import { useCallback, useEffect, useState } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type {
  FacilityEnrollment,
  FacilityReadiness,
  ProviderLabRoute,
  RouteActivationRefusal,
} from '../../api/exchangeTypes'
import { Button, LoadingSkeleton } from '../../design-system'
import { readinessFindingLabel, routeSideLabel } from './onboardingVocabulary'
import styles from './Platform.module.css'

export interface RouteNetworkProps {
  provider: FacilityEnrollment
  /** Every facility on the network; the labs are picked out here. */
  facilities: FacilityEnrollment[]
  providerReadiness: FacilityReadiness | null
  client: ExchangeClient
  onChanged: () => void
}

/** What this component can say about one laboratory, all of it server-derived. */
interface LabRow {
  lab: FacilityEnrollment
  route: ProviderLabRoute | null
  readiness: FacilityReadiness | null
  /** Readiness could not be read. Not the same as "not ready" and never shown as it. */
  readinessUnknown: boolean
}

function refusalReasons(error: ExchangeApiError): string[] {
  const body = error.body
  if (!body || typeof body !== 'object') return []
  const detail = (body as { detail?: unknown }).detail
  if (!detail || typeof detail !== 'object') return []
  const reasons = (detail as Partial<RouteActivationRefusal>).blocking_reasons
  if (!Array.isArray(reasons)) return []
  return reasons.map((reason) => {
    const side = routeSideLabel(reason.side ?? null)
    // `satisfied: false` — these are the conditions that failed, so they must be
    // worded as absences. The satisfied phrasing under a refusal heading states
    // the opposite of what is wrong.
    const label = readinessFindingLabel(reason.code, false)
    return side ? `${side} — ${label}` : label
  })
}

export function RouteNetwork({
  provider,
  facilities,
  providerReadiness,
  client,
  onChanged,
}: RouteNetworkProps) {
  const [rows, setRows] = useState<LabRow[] | null>(null)
  const [loadError, setLoadError] = useState<ExchangeApiError | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<{ labId: string; reasons: string[] } | null>(null)

  const labs = facilities.filter((f) => f.organization_type === 'LAB')

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const routes = await client.listPlatformProviderLabRoutes(provider.organization_id)
      const labFacilities = facilities.filter((f) => f.organization_type === 'LAB')
      // One readiness read per laboratory. A fan-out, deliberately: readiness is
      // evaluated from live rows and there is no batch endpoint, and the honest
      // alternative — offering "Activer" on every lab and letting the server
      // refuse — would show an operator a control that cannot work. Failures are
      // tolerated individually so one unreadable lab does not blank the section.
      const readiness = await Promise.allSettled(
        labFacilities.map((lab) => client.getFacilityReadiness(lab.organization_id)),
      )
      setRows(
        labFacilities.map((lab, index) => {
          const settled = readiness[index]
          return {
            lab,
            route: routes.find((r) => r.lab_organization_id === lab.organization_id) ?? null,
            readiness: settled.status === 'fulfilled' ? settled.value : null,
            readinessUnknown: settled.status !== 'fulfilled',
          }
        }),
      )
    } catch (error) {
      setLoadError(error as ExchangeApiError)
    }
  }, [client, facilities, provider.organization_id])

  useEffect(() => {
    void load()
  }, [load])

  // A provider with nobody who may order cannot route anywhere, however ready the
  // laboratory is. Said once, at the top, rather than repeated on every row.
  const providerBlocking = (providerReadiness?.findings ?? []).filter(
    (f) => f.blocking && !f.satisfied,
  )
  const providerReady = providerReadiness?.production_routing_allowed ?? false

  const submit = async (labId: string, active: boolean) => {
    setPending(labId)
    setRefusal(null)
    try {
      await client.setProviderLabRoute(provider.organization_id, labId, active)
      setConfirming(null)
      await load()
      onChanged()
    } catch (error) {
      const apiError = error as ExchangeApiError
      const reasons = refusalReasons(apiError)
      setRefusal({
        labId,
        reasons:
          reasons.length > 0
            ? reasons
            : [apiError.detail || "L'activation a été refusée par le serveur."],
      })
      setConfirming(null)
      // The screen was drawn against a readiness that no longer holds. Re-read it
      // so what is displayed matches what the server just enforced.
      await load()
    } finally {
      setPending(null)
    }
  }

  return (
    <section aria-labelledby="facility-routes-heading">
      <h2 id="facility-routes-heading" className={styles.sectionTitle}>
        Relations de routage
      </h2>
      <p className={styles.sectionLede}>
        Les laboratoires vers lesquels cet établissement peut adresser des ordres. Une relation
        configurée n'est pas une disponibilité : un laboratoire configuré mais sans personnel
        habilité reste inaccessible aux nouveaux ordres, et sa relation n'est pas supprimée pour
        autant.
      </p>

      {!providerReady && providerBlocking.length > 0 ? (
        <div className={styles.stoppedPanel} role="note">
          <p>
            Aucun routage ne peut être activé depuis cet établissement tant que ce qui suit n'est
            pas résolu :
          </p>
          <ul className={styles.findingList}>
            {providerBlocking.map((finding) => (
              <li key={finding.code} className={styles.findingBlocking}>
                {readinessFindingLabel(finding.code, false)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {loadError ? (
        <p role="alert">
          Les relations de routage n'ont pas pu être lues.{' '}
          <Button variant="secondary" onClick={() => void load()}>
            Réessayer
          </Button>
        </p>
      ) : rows === null ? (
        <LoadingSkeleton />
      ) : labs.length === 0 ? (
        <p>Aucun laboratoire n'est enregistré sur le réseau.</p>
      ) : (
        <ul className={styles.routeList}>
          {rows.map((row) => {
            const labId = row.lab.organization_id
            const isConnect = row.lab.participation_mode === 'CONNECT'
            const labBlocking = (row.readiness?.findings ?? []).filter(
              (f) => f.blocking && !f.satisfied,
            )
            const labReady = row.readiness?.production_routing_allowed ?? false
            const active = row.route?.active ?? false
            // Why this laboratory cannot be switched on, in the order that answers
            // the operator's question soonest. Connect first: no amount of staffing
            // changes it, because no connector exists.
            const blockedBecause = isConnect
              ? 'Aucun connecteur DHealth (mode Connect)'
              : row.readinessUnknown
                ? "Disponibilité non lue — l'état de ce laboratoire n'a pas pu être évalué"
                : !labReady
                  ? labBlocking.length > 0
                    ? readinessFindingLabel(labBlocking[0].code, false)
                    : 'Routage de production indisponible'
                  : !providerReady
                    ? "L'établissement de soins n'est pas opérationnel"
                    : null
            const canActivate = blockedBecause === null && !active
            const restatementId = `route-confirm-${labId}`

            return (
              <li key={labId} className={styles.routeRow}>
                <div className={styles.routeIdentity}>
                  <span className={styles.routeLabName}>{row.lab.organization_name}</span>
                  <span className={styles.routeState}>
                    {active ? (
                      <>
                        <strong className={styles.done}>Relation configurée</strong>
                        {' · '}
                        {blockedBecause === null ? (
                          <span className={styles.done}>Prêt</span>
                        ) : (
                          // The relationship stands and the routing does not. Both
                          // are true at once, and saying only one of them would be
                          // the conflation this ticket exists to prevent.
                          <span className={styles.owed}>
                            Routage indisponible — {blockedBecause}
                          </span>
                        )}
                      </>
                    ) : blockedBecause === null ? (
                      <span>Non connecté</span>
                    ) : (
                      <span className={styles.owed}>Non connecté — {blockedBecause}</span>
                    )}
                  </span>
                </div>

                {refusal?.labId === labId ? (
                  <div className={styles.stoppedPanel} role="alert">
                    <p>
                      <strong>Activation refusée par le serveur.</strong> La disponibilité a changé
                      depuis l'affichage de cet écran.
                    </p>
                    <ul className={styles.findingList}>
                      {refusal.reasons.map((reason) => (
                        <li key={reason} className={styles.findingBlocking}>
                          {reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {confirming === labId ? (
                  <div className={styles.confirmPanel}>
                    {/* Restated rather than assumed: the operator reads what this
                        will permit, in full, before the second gesture. */}
                    <p id={restatementId}>
                      Activer le routage de <strong>{provider.organization_name}</strong> vers{' '}
                      <strong>{row.lab.organization_name}</strong> autorisera les nouveaux ordres
                      éligibles de cet établissement à être adressés à ce laboratoire. Les ordres
                      déjà en cours ne sont pas modifiés.
                    </p>
                    <div className={styles.confirmActions}>
                      <Button variant="secondary" onClick={() => setConfirming(null)}>
                        Annuler
                      </Button>
                      <Button
                        aria-describedby={restatementId}
                        disabled={pending === labId}
                        onClick={() => void submit(labId, true)}
                      >
                        Activer
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className={styles.routeActions}>
                    {active ? (
                      // Never gated, on this screen or on the server: switching
                      // routing off must stay available exactly when a facility is
                      // in no condition to receive work.
                      <Button
                        variant="secondary"
                        disabled={pending === labId}
                        onClick={() => void submit(labId, false)}
                      >
                        Désactiver
                      </Button>
                    ) : canActivate ? (
                      <Button onClick={() => setConfirming(labId)}>Activer</Button>
                    ) : null}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
