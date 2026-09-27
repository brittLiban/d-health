/**
 * DHEALTH-CLINIC-VISIT-001 — Visites: the clinic's live state, and one consultation at a time.
 *
 * Before this screen, DHealth could record that a diagnostic order existed but not that a
 * consultation happened. A clinician's own day had no representation in the product: no waiting
 * list, no "who am I seeing now", nowhere to write down why the patient came.
 * `docs/NATIVE-OPERATING-PRODUCT.md` §12/§13 named that the first gap between the Native product and
 * a clinic that could stop using its current software.
 *
 * ── Four states, and the one refusal that will look like a bug ──
 *
 * `En attente` → `En consultation` → `Terminée`, plus `Annulée` **from `En attente` only**. A visit
 * already in consultation cannot be cancelled: §12 records that as a completed-with-a-note, because
 * contact with the patient happened and no status may erase it. So this screen stops offering
 * "Annuler" the moment a visit is taken, and the server refuses it with a 409 even if somebody
 * reaches the endpoint by hand. That is deliberate, and it is the first thing to read before
 * "why can I not cancel this".
 *
 * `PLANNED` and `NO_SHOW` do not exist here. Both need a calendar to be reachable, DHealth has no
 * appointment surface, and a state nothing can enter is decoration.
 *
 * ── Two columns, and why the worklist never leaves ──
 *
 * The waiting list stays on screen while a clinician works inside one visit. A clinic's most common
 * interruption is "who else is waiting", and answering it by navigating away and back loses the note
 * being written. Below 1100px it collapses to one column and the list moves under the visit.
 *
 * ── Nothing on this screen interprets anything ──
 *
 * No waiting-time badge, no "patient en retard", no triage order, no priority, no colour that means
 * urgency. A waiting time computed in a browser from two clocks is a number a clinic would act on,
 * and DHealth records and routes — it does not produce clinical or operational signals of its own
 * (`AGENTS.md`). The list is in the order the server returned it, newest first, and the times shown
 * are the exact timestamps the server recorded.
 *
 * ── Ordering from a visit does not live here ──
 *
 * "Demander des analyses" hands off to `Patients`, which already owns the whole catalogue,
 * routing and recovery flow (`ClinicPatientsPage`/`LabRoutingStep`). This screen passes the patient
 * and the visit and gets out of the way. Forking that wizard to put a copy inside the visit would
 * duplicate the most safety-critical interaction in the product — the one with durable intents,
 * uncertain-write recovery and lab routing — and the two copies would drift.
 *
 * ── This screen is not a security boundary ──
 *
 * Every control is shown or hidden for usability only. The server checks CLINICIAN membership of
 * this exact organization on every one of these requests, refuses a hand-typed request from an
 * administrator or a technician, and enforces the transition table regardless of what was rendered.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'

import { ExchangeApiError, mapExchangeErrorToCode } from '../../api/exchangeClient'
import type {
  PatientDirectoryEntry,
  Visit,
  VisitDetail,
  VisitStatus,
} from '../../api/exchangeTypes'
import type { ErrorCode } from '../../api/types'
import { Button, Card, EmptyState, ErrorState, LoadingSkeleton, StatusBadge } from '../../design-system'
import { useClinic } from '../clinic/ClinicContext'
import styles from './Visits.module.css'
import { VisitWorkspace } from './VisitWorkspace'
import { visitPatientName, visitStatusLabel, visitStatusTone, visitTime } from './visitVocabulary'

function safeErrorCode(error: unknown): ErrorCode {
  return error instanceof ExchangeApiError ? mapExchangeErrorToCode(error.code) : 'SERVER_ERROR'
}

/**
 * The two filters a clinic actually asks for, and no "Tous".
 *
 * `En cours` is the working default: the live state of the building. `Terminées` is the review
 * question, asked deliberately. A combined "everything" view would grow without bound and would
 * bury the handful of people currently waiting, which is the one thing this screen exists to show.
 */
const FILTERS: ReadonlyArray<{ id: 'live' | 'closed'; label: string; statuses: VisitStatus[] }> = [
  { id: 'live', label: 'En cours', statuses: ['CHECKED_IN', 'IN_PROGRESS'] },
  { id: 'closed', label: 'Terminées et annulées', statuses: ['COMPLETED', 'CANCELLED'] },
]

export interface ClinicVisitsPageProps {
  /**
   * Hands the clinician to the ordering flow with this patient and this visit already in context.
   * Absent in tests that only exercise the visit lifecycle.
   */
  onOrderForVisit?: (context: { patientId: string; visitId: string }) => void
}

export function ClinicVisitsPage({ onOrderForVisit }: ClinicVisitsPageProps) {
  const { activeProvider, client } = useClinic()
  const providerId = activeProvider?.organization_id ?? null
  const mountedRef = useRef(true)
  const listGenerationRef = useRef(0)
  const detailGenerationRef = useRef(0)

  const [filter, setFilter] = useState<'live' | 'closed'>('live')
  const [visits, setVisits] = useState<Visit[] | null>(null)
  const [listState, setListState] = useState<'loading' | 'success' | 'error'>('loading')
  const [listErrorCode, setListErrorCode] = useState<ErrorCode>('SERVER_ERROR')

  const [selectedVisitId, setSelectedVisitId] = useState<string | null>(null)
  const [detail, setDetail] = useState<VisitDetail | null>(null)
  const [detailState, setDetailState] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [detailErrorCode, setDetailErrorCode] = useState<ErrorCode>('SERVER_ERROR')

  const [showArrival, setShowArrival] = useState(false)
  const [patientQuery, setPatientQuery] = useState('')
  const [patientResults, setPatientResults] = useState<PatientDirectoryEntry[] | null>(null)
  const [patientSearchState, setPatientSearchState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [chosenPatient, setChosenPatient] = useState<PatientDirectoryEntry | null>(null)
  const [reason, setReason] = useState('')
  const [arrivalState, setArrivalState] = useState<'idle' | 'submitting' | 'uncertain'>('idle')
  const [arrivalProblem, setArrivalProblem] = useState<string | null>(null)
  /**
   * One key per *intended* arrival, minted when the form opens and kept until the server
   * authoritatively answers. A double click, a slow connection, or a retry after an uncertain
   * response all present the same key, so the waiting list gains one entry rather than two — the
   * identical discipline patient registration and order creation already use.
   */
  const [arrivalKey, setArrivalKey] = useState<string | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const loadVisits = useCallback(
    async (targetProviderId: string, which: 'live' | 'closed') => {
      const generation = ++listGenerationRef.current
      setListState('loading')
      const statuses = FILTERS.find((entry) => entry.id === which)?.statuses ?? []
      try {
        const values = await client.listVisits(targetProviderId, { statuses, limit: 100 })
        if (mountedRef.current && listGenerationRef.current === generation) {
          setVisits(values)
          setListState('success')
        }
      } catch (error) {
        if (mountedRef.current && listGenerationRef.current === generation) {
          setVisits([])
          setListErrorCode(safeErrorCode(error))
          setListState('error')
        }
      }
    },
    [client],
  )

  const loadDetail = useCallback(
    async (targetProviderId: string, visitId: string) => {
      const generation = ++detailGenerationRef.current
      setDetailState('loading')
      try {
        const value = await client.getVisit(targetProviderId, visitId)
        if (mountedRef.current && detailGenerationRef.current === generation) {
          setDetail(value)
          setDetailState('success')
        }
      } catch (error) {
        if (mountedRef.current && detailGenerationRef.current === generation) {
          setDetail(null)
          setDetailErrorCode(safeErrorCode(error))
          setDetailState('error')
        }
      }
    },
    [client],
  )

  // The organization is the scope of everything on this screen, so switching it discards all of it
  // rather than re-filtering. A visit id from the previous organization would 404 at best.
  useEffect(() => {
    setVisits(null)
    setSelectedVisitId(null)
    setDetail(null)
    setDetailState('idle')
    setShowArrival(false)
    if (providerId) void loadVisits(providerId, filter)
  }, [providerId, filter, loadVisits])

  useEffect(() => {
    if (providerId && selectedVisitId) void loadDetail(providerId, selectedVisitId)
  }, [providerId, selectedVisitId, loadDetail])

  const handleOpenArrival = () => {
    setShowArrival(true)
    setChosenPatient(null)
    setPatientQuery('')
    setPatientResults(null)
    setPatientSearchState('idle')
    setReason('')
    setArrivalProblem(null)
    setArrivalState('idle')
    setArrivalKey(crypto.randomUUID())
  }

  const handleSearchPatients = async (event: FormEvent) => {
    event.preventDefault()
    if (!providerId) return
    const query = patientQuery.trim()
    setPatientSearchState('loading')
    try {
      const values = query
        ? await client.searchPatients(providerId, query, { limit: 20 })
        : await client.listPatients(providerId, { limit: 20 })
      if (!mountedRef.current) return
      setPatientResults(values)
      setPatientSearchState('idle')
    } catch {
      if (!mountedRef.current) return
      setPatientResults([])
      setPatientSearchState('error')
    }
  }

  const handleRegisterArrival = async (event: FormEvent) => {
    event.preventDefault()
    if (!providerId || !chosenPatient || !arrivalKey) return
    const trimmedReason = reason.trim()
    if (!trimmedReason) {
      setArrivalProblem('Indiquez le motif de la visite.')
      return
    }

    setArrivalState('submitting')
    setArrivalProblem(null)
    try {
      const outcome = await client.startVisitWithOutcome(providerId, arrivalKey, {
        patient_id: chosenPatient.patient_id,
        reason_for_visit: trimmedReason,
      })
      if (!mountedRef.current) return
      setShowArrival(false)
      setArrivalState('idle')
      setArrivalKey(null)
      // The authoritative row, prepended locally so the list is right immediately, then refreshed
      // in the background. A failed refresh does not invalidate a committed arrival.
      setVisits((current) => {
        if (!current) return [outcome.visit]
        if (current.some((visit) => visit.id === outcome.visit.id)) return current
        return [outcome.visit, ...current]
      })
      setFilter('live')
      setSelectedVisitId(outcome.visit.id)
      void loadVisits(providerId, 'live')
    } catch (error) {
      if (!mountedRef.current) return
      if (error instanceof ExchangeApiError && error.status === 409) {
        // The key was already used for a *different* patient or reason. Never silently returning
        // somebody else's visit is the point; the clinician is told to start again cleanly.
        setArrivalProblem(
          "Cette tentative d'arrivée a déjà été utilisée pour un autre patient ou un autre motif. " +
            'Fermez ce formulaire et recommencez.',
        )
        setArrivalState('idle')
        return
      }
      if (error instanceof ExchangeApiError && error.code === 'NETWORK_ERROR') {
        // The request may or may not have committed. The key is kept, so pressing the button again
        // is safe and cannot create a second visit.
        setArrivalProblem(
          "L'enregistrement n'a pas pu être confirmé. Réessayez : la même tentative sera reprise, " +
            "sans créer de doublon.",
        )
        setArrivalState('uncertain')
        return
      }
      setArrivalProblem("L'arrivée n'a pas pu être enregistrée.")
      setArrivalState('idle')
    }
  }

  const handleVisitChanged = (updated: Visit) => {
    setVisits((current) =>
      current ? current.map((visit) => (visit.id === updated.id ? updated : visit)) : current,
    )
    if (providerId) {
      void loadDetail(providerId, updated.id)
      // A transition can move a visit out of the current filter (completing one under `En cours`),
      // so the list is re-read rather than patched.
      void loadVisits(providerId, filter)
    }
  }

  if (!providerId) {
    return (
      <div className={styles.page}>
        <EmptyState title="Aucun établissement actif">
          Sélectionnez un établissement pour consulter ses visites.
        </EmptyState>
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Visites</h1>
          <p className={styles.subtitle}>
            Qui est présent, qui est en consultation, et ce qui a été consigné. Les heures affichées
            sont celles enregistrées par DHealth.
          </p>
        </div>
        {!showArrival ? (
          <Button onClick={handleOpenArrival}>Enregistrer une arrivée</Button>
        ) : null}
      </header>

      <div className={styles.columns}>
        <Card className={styles.panel}>
          <h2 className={styles.panelTitle}>Liste</h2>
          <div className={styles.filters} role="group" aria-label="Filtrer les visites">
            {FILTERS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                className={styles.filterButton}
                aria-pressed={filter === entry.id}
                onClick={() => setFilter(entry.id)}
              >
                {entry.label}
              </button>
            ))}
          </div>

          {/* `aria-label` on the form below is load-bearing twice over: assistive technology
              announces which form this is when it appears beside a worklist that already lists
              patients, and it gives the form an accessible name so a caller can scope to it rather
              than matching a patient's name in the form and in the worklist at the same time. */}
          {showArrival ? (
            <form
              className={styles.form}
              aria-label="Nouvelle arrivée"
              onSubmit={handleRegisterArrival}
            >
              <h3 className={styles.sectionTitle}>Nouvelle arrivée</h3>

              {chosenPatient === null ? (
                <>
                  {/* A nested form is invalid HTML, so patient search is a plain field with its own
                      button rather than a second <form>. */}
                  <label className={styles.hint} htmlFor="visit-patient-search">
                    Rechercher le patient dans le répertoire de l'établissement
                  </label>
                  <input
                    id="visit-patient-search"
                    className={styles.noteArea}
                    style={{ minHeight: 'auto' }}
                    value={patientQuery}
                    onChange={(event) => setPatientQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void handleSearchPatients(event)
                    }}
                  />
                  <div className={styles.formActions}>
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={(event) => void handleSearchPatients(event)}
                    >
                      Rechercher
                    </Button>
                    <Button type="button" variant="secondary" onClick={() => setShowArrival(false)}>
                      Annuler
                    </Button>
                  </div>
                  {patientSearchState === 'loading' ? <LoadingSkeleton /> : null}
                  {patientSearchState === 'error' ? (
                    <p className={styles.problem}>Le répertoire n'a pas pu être consulté.</p>
                  ) : null}
                  {patientResults !== null && patientResults.length === 0 ? (
                    <p className={styles.hint}>
                      Aucun patient trouvé. Un patient doit exister dans le répertoire avant de
                      pouvoir enregistrer son arrivée.
                    </p>
                  ) : null}
                  <ul className={styles.list}>
                    {(patientResults ?? []).map((patient) => (
                      <li key={patient.patient_id} className={styles.listItem}>
                        <button
                          type="button"
                          className={styles.visitButton}
                          onClick={() => setChosenPatient(patient)}
                        >
                          <span className={styles.patientName}>
                            {visitPatientName(patient.display_name)}
                          </span>
                          <span className={styles.meta}>
                            <span>{patient.date_of_birth ?? 'Date de naissance non enregistrée'}</span>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <>
                  <p className={styles.hint}>
                    Patient : <strong>{visitPatientName(chosenPatient.display_name)}</strong>{' '}
                    <button
                      type="button"
                      className={styles.filterButton}
                      onClick={() => setChosenPatient(null)}
                    >
                      Changer
                    </button>
                  </p>
                  <label className={styles.hint} htmlFor="visit-reason">
                    Motif de la visite, tel qu'énoncé par le patient
                  </label>
                  <textarea
                    id="visit-reason"
                    className={styles.noteArea}
                    style={{ minHeight: '4rem' }}
                    maxLength={500}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                  {arrivalProblem ? (
                    <p className={styles.problem} role="alert">
                      {arrivalProblem}
                    </p>
                  ) : null}
                  <div className={styles.formActions}>
                    <Button type="submit" disabled={arrivalState === 'submitting'}>
                      {arrivalState === 'submitting'
                        ? 'Enregistrement…'
                        : arrivalState === 'uncertain'
                          ? 'Réessayer'
                          : "Enregistrer l'arrivée"}
                    </Button>
                    <Button type="button" variant="secondary" onClick={() => setShowArrival(false)}>
                      Annuler
                    </Button>
                  </div>
                </>
              )}
            </form>
          ) : null}

          {listState === 'loading' ? <LoadingSkeleton /> : null}
          {listState === 'error' ? (
            <ErrorState code={listErrorCode} onRetry={() => void loadVisits(providerId, filter)} />
          ) : null}
          {listState === 'success' && (visits ?? []).length === 0 ? (
            <EmptyState
              title={
                filter === 'live'
                  ? "Personne n'est enregistré comme présent"
                  : 'Aucune visite terminée ou annulée'
              }
            >
              {filter === 'live'
                ? "Enregistrez une arrivée lorsqu'un patient se présente."
                : 'Les visites terminées apparaîtront ici.'}
            </EmptyState>
          ) : null}

          <ul className={styles.list}>
            {(visits ?? []).map((visit) => (
              <li key={visit.id} className={styles.listItem}>
                <button
                  type="button"
                  className={styles.visitButton}
                  aria-current={selectedVisitId === visit.id}
                  onClick={() => setSelectedVisitId(visit.id)}
                >
                  <span className={styles.visitRowTop}>
                    <span className={styles.patientName}>
                      {visitPatientName(visit.patient_display_name)}
                    </span>
                    <StatusBadge
                      label={visitStatusLabel(visit.status)}
                      tone={visitStatusTone(visit.status)}
                    />
                  </span>
                  <span className={styles.reason}>{visit.reason_for_visit}</span>
                  <span className={styles.meta}>
                    <span>Arrivée {visitTime(visit.created_at)}</span>
                    {visit.started_at ? <span>Prise en charge {visitTime(visit.started_at)}</span> : null}
                    {visit.completed_at ? <span>Terminée {visitTime(visit.completed_at)}</span> : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Card>

        <Card className={styles.panel}>
          {selectedVisitId === null ? (
            <EmptyState title="Aucune visite sélectionnée">
              Choisissez une visite dans la liste pour la prendre en charge, écrire une note ou la
              terminer.
            </EmptyState>
          ) : detailState === 'loading' ? (
            <LoadingSkeleton />
          ) : detailState === 'error' ? (
            <ErrorState
              code={detailErrorCode}
              onRetry={() => void loadDetail(providerId, selectedVisitId)}
            />
          ) : detail ? (
            <VisitWorkspace
              providerId={providerId}
              detail={detail}
              onVisitChanged={handleVisitChanged}
              onReloadDetail={() => void loadDetail(providerId, selectedVisitId)}
              onOrderForVisit={onOrderForVisit}
            />
          ) : null}
        </Card>
      </div>
    </div>
  )
}
