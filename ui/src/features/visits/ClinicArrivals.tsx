/**
 * DHEALTH-FRONT-DESK-001/ADR-069 — `Aujourd'hui`'s arrival and waiting surface.
 *
 * The beginning of an ordinary clinic day, which the product did not have: a patient walks in,
 * somebody identifies or registers them, checks them in, and they wait until a clinician starts the
 * consultation. `docs/NATIVE-OPERATING-PRODUCT.md` calls that **P0-2**, and its absence is why a
 * clinic kept a paper register beside DHealth — double entry, and product law 2 broken.
 *
 * ── It lives on `Aujourd'hui`, and that placement is forced rather than chosen ──
 *
 * §24 asks for the existing Today surface rather than a new "Front Desk" module, and the capability
 * model makes that the only correct answer anyway: a reception-only membership has no `Visites`
 * section at all (that needs `visit.read`/`visit.document`, which reception does not hold), so a
 * waiting list that lived only there would be invisible to the person who runs it. `Aujourd'hui` is
 * the one surface both a receptionist and a clinician reach.
 *
 * ── Every control is capability-driven, and none of them is a security boundary ──
 *
 * `visit.check_in` shows the arrival action. `patient.register` shows `Nouveau patient`.
 * `visit.document` shows `Commencer la consultation`. `visit.read` shows `Ouvrir`. Each is
 * `can(capabilities, …)` against what the **server** resolved (`workspaceCapabilities.ts`) — never a
 * role name, and never a screen permission of its own. Hiding a control keeps somebody from being
 * offered work their membership cannot complete; the server checks every request regardless and
 * would refuse a hand-typed one identically.
 *
 * The consequence worth stating plainly: a reception-only user sees patient identity, the arrival
 * time and the status. There is **no `Ouvrir`** for them, because opening the visit is a clinical
 * read they do not hold — §47. The button's absence is the usability half of a refusal the backend
 * enforces.
 *
 * ── Nothing here derives a waiting time, a priority or a triage order ──
 *
 * The server sends `checked_in_at` and sends no duration. This renders that timestamp and computes
 * nothing from it. `AGENTS.md` (2026-09-27) and §38 of the operating specification both forbid it:
 * a waiting time computed in a browser from two clocks is an operational signal DHealth must not
 * produce, and triage is an explicit non-goal. This ticket's own §16 asked for one; it is recorded as
 * a blocker in `PROJECT_STATE.md` instead of being added here. No colour means urgency, no row is
 * reordered by anything about the patient, and the list is in exactly the order the server returned.
 *
 * ── The one idempotency rule that matters ──
 *
 * One `Idempotency-Key` per *intended* arrival, minted when the patient is chosen and kept until the
 * server answers authoritatively. A double click, a slow connection and a retry after an uncertain
 * response all present the same key, so the waiting list gains one entry rather than two — the same
 * discipline patient registration, order creation and `ClinicVisitsPage`'s own arrival form use.
 *
 * A 409 is the other case and is **not** an error to apologise for: it means this patient already has
 * an active visit, which is what two receptionists doing the same thing looks like. The list is
 * refetched and the truth is shown.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { ExchangeApiError, mapExchangeErrorToCode } from '../../api/exchangeClient'
import type { PatientDirectoryEntry, WaitingArrival } from '../../api/exchangeTypes'
import type { ErrorCode } from '../../api/types'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  SearchInput,
  StatusBadge,
} from '../../design-system'
import { useClinic } from '../clinic/ClinicContext'
import { Capability, can, capabilitiesFor } from '../workspace/workspaceCapabilities'
import styles from './ClinicArrivals.module.css'
import { visitDate, visitPatientName, visitStatusLabel, visitStatusTone, visitTime } from './visitVocabulary'

export interface ClinicArrivalsProps {
  /** Opens the full clinical visit. Only ever called from a control shown to somebody holding
   * `visit.read`, and absent in tests that exercise the reception-only path. */
  onOpenVisit?: (visitId: string) => void
  /** Hands off to patient registration with the arrival flow's intent preserved, so a newly
   * registered patient returns here and is checked in without a second search (§14, law 2). */
  onRegisterPatient?: () => void
}

/** A patient the desk has chosen but not yet confirmed, with the key minted for that one arrival. */
interface PendingArrival {
  patient: PatientDirectoryEntry
  idempotencyKey: string
}

export function ClinicArrivals({ onOpenVisit, onRegisterPatient }: ClinicArrivalsProps) {
  const { activeProvider, client } = useClinic()
  const providerId = activeProvider?.organization_id ?? null
  const capabilities = capabilitiesFor(activeProvider)

  const mayCheckIn = can(capabilities, Capability.VISIT_CHECK_IN)
  const mayRegister = can(capabilities, Capability.PATIENT_REGISTER)
  const maySearch = can(capabilities, Capability.PATIENT_DIRECTORY_READ)
  const mayDocument = can(capabilities, Capability.VISIT_DOCUMENT)
  const mayReadVisit = can(capabilities, Capability.VISIT_READ)

  const mountedRef = useRef(true)
  const generationRef = useRef(0)

  const [arrivals, setArrivals] = useState<WaitingArrival[] | null>(null)
  const [listState, setListState] = useState<'loading' | 'success' | 'error'>('loading')
  const [listErrorCode, setListErrorCode] = useState<ErrorCode>('SERVER_ERROR')

  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<PatientDirectoryEntry[] | null>(null)
  const [searchState, setSearchState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [pending, setPending] = useState<PendingArrival | null>(null)
  const [submitState, setSubmitState] = useState<'idle' | 'submitting'>('idle')
  const [problem, setProblem] = useState<string | null>(null)
  const [busyVisitId, setBusyVisitId] = useState<string | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  /**
   * Refetch, which is how this surface becomes current (§50).
   *
   * A revalidation on mount and after every mutation — deliberately **not** a WebSocket or a new
   * realtime subsystem, which §66 puts out of scope. A colleague checking somebody in elsewhere is
   * reflected the next time this list is fetched, and the honest limitation is that it is not pushed;
   * it is stated in the ticket report rather than implied to be live.
   */
  const reload = useCallback(async () => {
    if (providerId === null) return
    const generation = ++generationRef.current
    setListState((current) => (current === 'success' ? current : 'loading'))
    try {
      const rows = await client.listWaitingArrivals(providerId)
      if (!mountedRef.current || generation !== generationRef.current) return
      setArrivals(rows)
      setListState('success')
    } catch (error) {
      if (!mountedRef.current || generation !== generationRef.current) return
      setListErrorCode(
        error instanceof ExchangeApiError ? mapExchangeErrorToCode(error.code) : 'SERVER_ERROR',
      )
      setListState('error')
    }
  }, [client, providerId])

  useEffect(() => {
    void reload()
  }, [reload])

  const runSearch = useCallback(
    async (text: string) => {
      if (providerId === null || text.trim().length === 0) {
        setResults(null)
        setSearchState('idle')
        return
      }
      setSearchState('loading')
      try {
        const found = await client.searchPatients(providerId, text.trim())
        if (!mountedRef.current) return
        setResults(found)
        setSearchState('idle')
      } catch {
        if (!mountedRef.current) return
        setResults(null)
        setSearchState('error')
      }
    },
    [client, providerId],
  )

  /** Choosing a patient mints the key for this arrival and shows the confirmation (§31). */
  const choose = (patient: PatientDirectoryEntry) => {
    setProblem(null)
    setPending({ patient, idempotencyKey: crypto.randomUUID() })
  }

  const confirm = async () => {
    if (providerId === null || pending === null) return
    setSubmitState('submitting')
    setProblem(null)
    try {
      // No `reason_for_visit`: §15. A chief complaint typed at a reception desk is clinical
      // documentation under another name, and the clinician's own visit workspace owns it.
      await client.startVisitWithOutcome(providerId, pending.idempotencyKey, {
        patient_id: pending.patient.patient_id,
      })
      if (!mountedRef.current) return
      setPending(null)
      setQuery('')
      setResults(null)
      setSearchOpen(false)
      await reload()
    } catch (error) {
      if (!mountedRef.current) return
      // 409 is the ordinary two-receptionists case, not a failure to apologise for: the patient is
      // already waiting. Say so and show the truth rather than offering a retry that will refuse.
      if (error instanceof ExchangeApiError && error.status === 409) {
        setProblem('Ce patient est déjà en attente ou en consultation. La liste a été actualisée.')
        setPending(null)
        await reload()
      } else {
        setProblem("L'accueil n'a pas pu être enregistré. Réessayez — la même arrivée ne sera pas enregistrée deux fois.")
      }
    } finally {
      if (mountedRef.current) setSubmitState('idle')
    }
  }

  const startConsultation = async (visitId: string) => {
    if (providerId === null) return
    setBusyVisitId(visitId)
    setProblem(null)
    try {
      await client.takeVisit(providerId, visitId)
      if (!mountedRef.current) return
      await reload()
      onOpenVisit?.(visitId)
    } catch (error) {
      if (!mountedRef.current) return
      setProblem(
        error instanceof ExchangeApiError && error.status === 409
          ? 'Un autre praticien a déjà pris ce patient en consultation.'
          : "La consultation n'a pas pu être démarrée.",
      )
      await reload()
    } finally {
      if (mountedRef.current) setBusyVisitId(null)
    }
  }

  const cancelArrival = async (visitId: string) => {
    if (providerId === null) return
    setBusyVisitId(visitId)
    setProblem(null)
    try {
      await client.cancelVisit(providerId, visitId)
      if (!mountedRef.current) return
      await reload()
    } catch (error) {
      if (!mountedRef.current) return
      setProblem(
        error instanceof ExchangeApiError && error.status === 409
          ? "La consultation a déjà commencé : cette arrivée ne peut plus être annulée."
          : "L'arrivée n'a pas pu être annulée.",
      )
      await reload()
    } finally {
      if (mountedRef.current) setBusyVisitId(null)
    }
  }

  if (providerId === null) return null

  return (
    <section className={styles.arrivals} aria-labelledby="arrivals-heading">
      <header className={styles.header}>
        <h2 id="arrivals-heading" className={styles.heading}>
          Patients en attente
        </h2>
        {mayCheckIn ? (
          <Button
            variant={searchOpen ? 'secondary' : 'primary'}
            onClick={() => {
              setSearchOpen((open) => !open)
              setPending(null)
              setProblem(null)
            }}
            aria-expanded={searchOpen}
          >
            {searchOpen ? 'Fermer' : 'Accueillir un patient'}
          </Button>
        ) : null}
      </header>

      {problem ? (
        <p className={styles.problem} role="status">
          {problem}
        </p>
      ) : null}

      {/* -- the arrival flow: search, confirm, done (§13) -- */}
      {mayCheckIn && searchOpen ? (
        <Card>
          <div className={styles.arrivalForm}>
            {maySearch ? (
              <SearchInput
                id="arrival-search"
                label="Rechercher un patient"
                value={query}
                placeholder="Nom du patient"
                onChange={(event) => {
                  const value = event.target.value
                  setQuery(value)
                  setPending(null)
                  void runSearch(value)
                }}
              />
            ) : (
              <p className={styles.hint}>
                Vous ne disposez pas de l'autorisation de consulter le répertoire des patients.
              </p>
            )}

            {searchState === 'loading' ? <LoadingSkeleton /> : null}
            {searchState === 'error' ? (
              <p className={styles.problem}>La recherche a échoué. Réessayez.</p>
            ) : null}

            {/* The confirmation step. Identity is on screen before anything is created (§31): a
                click on a search result never silently produces a visit. */}
            {pending ? (
              <div className={styles.confirm}>
                <p className={styles.confirmName}>{visitPatientName(pending.patient.display_name)}</p>
                <p className={styles.confirmDob}>
                  {pending.patient.date_of_birth
                    ? `Née/né le ${visitDate(pending.patient.date_of_birth)}`
                    : 'Date de naissance non enregistrée'}
                </p>
                <div className={styles.confirmActions}>
                  <Button onClick={() => void confirm()} disabled={submitState === 'submitting'}>
                    {submitState === 'submitting' ? 'Enregistrement…' : 'Accueillir'}
                  </Button>
                  <Button variant="secondary" onClick={() => setPending(null)}>
                    Choisir un autre patient
                  </Button>
                </div>
              </div>
            ) : results !== null ? (
              results.length === 0 ? (
                <div className={styles.notFound}>
                  <p className={styles.hint}>Aucun patient trouvé.</p>
                  {/* §14: register, then come straight back here and check in. No second search. */}
                  {mayRegister && onRegisterPatient ? (
                    <Button variant="secondary" onClick={onRegisterPatient}>
                      Nouveau patient
                    </Button>
                  ) : null}
                </div>
              ) : (
                <ul className={styles.results}>
                  {results.map((patient) => (
                    <li key={patient.patient_id}>
                      <button
                        type="button"
                        className={styles.resultButton}
                        onClick={() => choose(patient)}
                      >
                        <span className={styles.resultName}>
                          {visitPatientName(patient.display_name)}
                        </span>
                        <span className={styles.resultDob}>
                          {patient.date_of_birth ? visitDate(patient.date_of_birth) : '—'}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )
            ) : null}
          </div>
        </Card>
      ) : null}

      {/* -- the waiting list itself -- */}
      {listState === 'loading' && arrivals === null ? (
        <LoadingSkeleton />
      ) : listState === 'error' ? (
        <ErrorState code={listErrorCode} onRetry={() => void reload()} />
      ) : arrivals && arrivals.length > 0 ? (
        <ul className={styles.queue}>
          {arrivals.map((arrival) => (
            <li key={arrival.visit_id}>
              <Card>
                <div className={styles.card}>
                  <div className={styles.identity}>
                    <p className={styles.name}>{visitPatientName(arrival.patient_display_name)}</p>
                    <p className={styles.dob}>
                      {arrival.patient_date_of_birth
                        ? `Née/né le ${visitDate(arrival.patient_date_of_birth)}`
                        : 'Date de naissance non enregistrée'}
                    </p>
                  </div>
                  <div className={styles.state}>
                    {/*
                      The recorded arrival time, exactly as the server sent it, and no duration
                      computed from it. The status is a label *and* a tone, never a colour alone --
                      `StatusBadge`'s own rule, and §49's "not colour-only" applied to the one place
                      this screen distinguishes two states.
                    */}
                    <span className={styles.time}>{visitTime(arrival.checked_in_at)}</span>
                    <StatusBadge
                      tone={visitStatusTone(arrival.status)}
                      label={visitStatusLabel(arrival.status)}
                    />
                  </div>
                  <div className={styles.actions}>
                    {/* §47: no `Ouvrir` without the clinical read. Its absence for a
                        reception-only membership is the point, not an oversight. */}
                    {mayReadVisit && onOpenVisit ? (
                      <Button variant="secondary" onClick={() => onOpenVisit(arrival.visit_id)}>
                        Ouvrir
                      </Button>
                    ) : null}
                    {mayDocument && arrival.status === 'CHECKED_IN' ? (
                      <Button
                        onClick={() => void startConsultation(arrival.visit_id)}
                        disabled={busyVisitId === arrival.visit_id}
                      >
                        Commencer la consultation
                      </Button>
                    ) : null}
                    {/* Cancelling is offered only while nobody has begun. Once the visit is
                        `IN_PROGRESS` the server refuses it (§21) and the control is gone. */}
                    {mayCheckIn && arrival.status === 'CHECKED_IN' ? (
                      <Button
                        variant="secondary"
                        onClick={() => void cancelArrival(arrival.visit_id)}
                        disabled={busyVisitId === arrival.visit_id}
                      >
                        Annuler l'arrivée
                      </Button>
                    ) : null}
                  </div>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState title="Personne n'attend">
          {mayCheckIn
            ? "Les patients accueillis apparaissent ici, du plus ancien au plus récent."
            : 'Aucun patient en attente pour le moment.'}
        </EmptyState>
      )}
    </section>
  )
}
