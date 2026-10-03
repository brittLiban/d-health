/**
 * DHEALTH-APPOINTMENTS-001/ADR-073 — `Aujourd'hui`'s schedule.
 *
 * Who is expected, at what time, and have they arrived. Plus booking, moving, cancelling, and
 * recording the arrival that turns a booking into the existing visit workflow.
 *
 * ── It lives on `Aujourd'hui`, beside the arrivals panel, and that is forced rather than chosen ──
 *
 * `ClinicArrivals` is already here for exactly the same reason: a reception-only membership has no
 * `Visites` section at all, so a schedule that lived in its own navigation entry would be invisible
 * to the person who runs it — and ADR-071 refused a second navigation door into a subject an
 * existing surface already owns ("two doors into one room"). A receptionist's day is one screen:
 * who is expected, who has arrived, and the walk-in path, which stays immediately below and
 * unburied.
 *
 * ── Three capabilities, three different controls, none of them a security boundary ──
 *
 * `appointment.manage` **or** `visit.check_in` shows the day at all — the desk has to see who is
 * expected or this surface is useless to it. `appointment.manage` shows `Nouveau rendez-vous`,
 * `Déplacer` and `Annuler`. `visit.check_in` shows `Enregistrer l'arrivée`. Each is
 * `can(capabilities, …)` against what the **server** resolved, never a role name. Hiding a control
 * keeps somebody from being offered work their membership cannot complete; the server checks every
 * request regardless and refuses a hand-typed one identically.
 *
 * ── The browser formats time. It never decides it. ──
 *
 * The server sends a UTC instant and the facility's timezone name; this renders with
 * `Intl.DateTimeFormat('fr-FR', { timeZone })`. When booking, it sends a clinic-local **date and
 * wall clock** and lets the server build the instant. A `new Date(date + 'T' + time)` here would
 * interpret the receptionist's "10:00" in the *viewer's* zone — three hours out on a laptop set to
 * UTC — so the same booking would land on a different instant depending on who made it. The day
 * being viewed is likewise a date string the server resolves, and "today" is requested by sending
 * **no date at all**.
 *
 * ── Nothing here derives a no-show, and nothing derives a waiting time ──
 *
 * A booking whose time has passed with no visit renders a neutral *"heure passée"* note. That is an
 * observation about the clock, and the component stops there: it is not a `NO_SHOW`, it is not
 * styled as a failure, and no state is written anywhere. Who did not attend, and what counts as
 * late, is a policy nobody has decided (ADR-073 Human Decision 4). `AGENTS.md` (2026-09-27) forbids
 * deriving an operational signal like a waiting duration from two clocks, and the same reasoning
 * applies with more force to a judgement about a patient.
 *
 * Arrival state is likewise **projected, never stored**: `visit_id !== null` plus the linked visit's
 * own status. There is no `ARRIVED` appointment state to read.
 *
 * ── One idempotency key per intended booking ──
 *
 * Minted when the form is opened and kept until the server answers authoritatively, so a double
 * click, a slow connection and a retry all present the same key and produce one appointment. The
 * same discipline `ClinicArrivals`, patient registration and order creation already use.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { ExchangeApiError, mapExchangeErrorToCode } from '../../api/exchangeClient'
import type { AppointmentDay, PatientDirectoryEntry } from '../../api/exchangeTypes'
import type { ErrorCode } from '../../api/types'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  StatusBadge,
  TextInput,
} from '../../design-system'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { Capability, can, canAny } from '../workspace/workspaceCapabilities'
import { appointmentPresentation } from './appointmentPresentation'
import styles from './ClinicAppointments.module.css'

const LOAD_ERROR = 'Impossible de charger les rendez-vous.'

/**
 * French-first, and deliberately never a numeric form like `10/11/26`.
 *
 * The month is spelled so a reader cannot mistake the day for the month — the ambiguity the ticket
 * calls out by name. Formatted in the **facility's** timezone, which the server names, so the time
 * shown is the time the clinic means.
 */
function formatTime(instant: string, timeZone: string): string {
  return new Intl.DateTimeFormat('fr-FR', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone,
  }).format(new Date(instant))
}

function formatDayHeading(day: string, timeZone: string): string {
  // `day` is a plain `YYYY-MM-DD` from the server. Parsed as UTC noon rather than midnight so the
  // calendar date cannot slip a day when re-rendered in the facility zone -- the same hazard
  // `patientIdentityFormat.ts` documents for a date of birth.
  const anchor = new Date(`${day}T12:00:00Z`)
  return new Intl.DateTimeFormat('fr-FR', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone,
  }).format(anchor)
}

/** `YYYY-MM-DD` arithmetic on the string, with no `Date` and therefore no timezone surface. */
function shiftDay(day: string, days: number): string {
  const [year, month, date] = day.split('-').map(Number)
  const shifted = new Date(Date.UTC(year, month - 1, date + days))
  return shifted.toISOString().slice(0, 10)
}

interface FormState {
  patientId: string
  patientLabel: string
  date: string
  time: string
  idempotencyKey: string
}

export function ClinicAppointments() {
  const workspace = useWorkspace()
  const client = workspace.client
  const capabilities = workspace.capabilities
  const providerId = workspace.activeOrganization?.organization_id ?? null

  const mayManage = can(capabilities, Capability.APPOINTMENT_MANAGE)
  const mayCheckIn = can(capabilities, Capability.VISIT_CHECK_IN)
  const mayRegister = can(capabilities, Capability.PATIENT_REGISTER)
  const maySee = canAny(capabilities, Capability.APPOINTMENT_MANAGE, Capability.VISIT_CHECK_IN)

  /**
   * What was *asked for*, not what is shown. `null` means "the server's own today" -- the browser
   * never computes a date to send, which is the whole reason the clinic's day cannot drift.
   *
   * The **displayed** day is always `state.data.day`, echoed back by the server. Keeping those two
   * separate is what removes a `setState` from inside the fetch callback: nothing has to copy the
   * resolved day back into local state in order to render it.
   */
  const [requestedDay, setRequestedDay] = useState<string | null>(null)
  const [state, setState] = useState<{
    loading: boolean
    error: ErrorCode | null
    data: AppointmentDay | null
  }>({ loading: false, error: null, data: null })
  const [form, setForm] = useState<FormState | null>(null)
  const [rescheduling, setRescheduling] = useState<{ id: string; date: string; time: string } | null>(
    null,
  )
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [matches, setMatches] = useState<PatientDirectoryEntry[]>([])
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const load = useCallback(() => {
    if (!client || !providerId || !maySee) return
    setState((prev) => ({ ...prev, loading: true, error: null }))
    client
      .listAppointments(providerId, requestedDay === null ? {} : { day: requestedDay })
      .then((data) => {
        if (!mounted.current) return
        setState({ loading: false, error: null, data })
      })
      .catch((error: unknown) => {
        if (!mounted.current) return
        setState({
          loading: false,
          error:
            error instanceof ExchangeApiError
              ? mapExchangeErrorToCode(error.code)
              : 'NETWORK_ERROR',
          data: null,
        })
      })
  }, [client, providerId, requestedDay, maySee])

  useEffect(load, [load])

  const timeZone = state.data?.facility_timezone ?? 'UTC'
  // Read at render time. A `useMemo` keyed on the fetched data was a fake dependency: "now" does not
  // depend on the response, and constructing one Date per render costs nothing.
  const now = new Date()

  if (!maySee || !providerId || !client) return null

  function openForm() {
    setNotice(null)
    setSearch('')
    setMatches([])
    setForm({
      patientId: '',
      patientLabel: '',
      date: state.data?.day ?? '',
      time: '',
      // One key per *intended* booking, minted here and kept across retries.
      idempotencyKey: crypto.randomUUID(),
    })
  }

  async function runSearch(term: string) {
    setSearch(term)
    if (!client || !providerId || term.trim().length < 2) {
      setMatches([])
      return
    }
    try {
      const found = await client.searchPatients(providerId, term.trim())
      if (mounted.current) setMatches(found.slice(0, 5))
    } catch {
      if (mounted.current) setMatches([])
    }
  }

  async function submitBooking() {
    if (!form || !client || !providerId) return
    setBusy(true)
    setNotice(null)
    try {
      await client.createAppointment(providerId, form.idempotencyKey, {
        patient_id: form.patientId,
        // A clinic-local date and wall clock. The server builds the instant.
        scheduled_date: form.date,
        scheduled_time: `${form.time}:00`,
      })
      if (!mounted.current) return
      setForm(null)
      setRequestedDay(form.date)
      setNotice('Rendez-vous enregistré.')
      load()
    } catch (error: unknown) {
      if (!mounted.current) return
      setNotice(
        error instanceof ExchangeApiError && error.status === 404
          ? "Ce patient n'appartient pas à cet établissement."
          : "Le rendez-vous n'a pas pu être enregistré. Vous pouvez réessayer.",
      )
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  async function act(run: () => Promise<unknown>, conflict: string) {
    setBusy(true)
    setNotice(null)
    try {
      await run()
      if (mounted.current) load()
    } catch (error: unknown) {
      if (!mounted.current) return
      setNotice(error instanceof ExchangeApiError && error.status === 409 ? conflict : LOAD_ERROR)
      load()
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const appointments = state.data?.appointments ?? []

  return (
    <Card>
      <div className={styles.panel}>
        <div className={styles.header}>
          <h2 className={styles.heading}>Rendez-vous</h2>
          {mayManage ? (
            <Button type="button" onClick={openForm} disabled={busy}>
              Nouveau rendez-vous
            </Button>
          ) : null}
        </div>

        {state.data ? (
          <div className={styles.dayNav}>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setRequestedDay(shiftDay(state.data!.day, -1))}
              disabled={busy}
            >
              ← Jour précédent
            </Button>
            <p className={styles.dayHeading} aria-live="polite">
              {formatDayHeading(state.data.day, timeZone)}
            </p>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setRequestedDay(shiftDay(state.data!.day, 1))}
              disabled={busy}
            >
              Jour suivant →
            </Button>
            <Button type="button" variant="secondary" onClick={() => setRequestedDay(null)} disabled={busy}>
              Aujourd'hui
            </Button>
          </div>
        ) : null}

        {notice ? (
          <p role="status" className={styles.notice}>
            {notice}
          </p>
        ) : null}

        {form ? (
          <div className={styles.form}>
            <h3 className={styles.heading}>Nouveau rendez-vous</h3>
            {form.patientId === '' ? (
              <>
                <TextInput
                  label="Rechercher un patient"
                  value={search}
                  onChange={(event) => void runSearch(event.target.value)}
                />
                <ul className={styles.list}>
                  {matches.map((match) => (
                    <li key={match.patient_id}>
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() =>
                          setForm({
                            ...form,
                            patientId: match.patient_id,
                            patientLabel: match.display_name ?? 'Nom non renseigné',
                          })
                        }
                      >
                        {match.display_name ?? 'Nom non renseigné'}
                      </Button>
                    </li>
                  ))}
                </ul>
                {mayRegister ? (
                  <p className={styles.notice}>
                    Un patient inconnu se crée depuis <strong>Patients</strong>.
                  </p>
                ) : null}
              </>
            ) : (
              <p className={styles.notice}>
                Patient : <strong>{form.patientLabel}</strong>
              </p>
            )}
            <TextInput
              label="Date"
              type="date"
              value={form.date}
              onChange={(event) => setForm({ ...form, date: event.target.value })}
            />
            <TextInput
              label="Heure"
              type="time"
              value={form.time}
              onChange={(event) => setForm({ ...form, time: event.target.value })}
            />
            <div className={styles.actions}>
              <Button
                type="button"
                onClick={() => void submitBooking()}
                disabled={busy || form.patientId === '' || form.date === '' || form.time === ''}
              >
                {busy ? 'Enregistrement…' : 'Enregistrer le rendez-vous'}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => setForm(null)}
                disabled={busy}
              >
                Annuler
              </Button>
            </div>
          </div>
        ) : null}

        {state.loading && state.data === null ? (
          <LoadingSkeleton />
        ) : state.error ? (
          <ErrorState code={state.error} onRetry={load} />
        ) : appointments.length === 0 ? (
          <EmptyState title="Aucun rendez-vous ce jour">
            Les patients sans rendez-vous sont accueillis depuis la liste d'arrivées ci-dessous.
          </EmptyState>
        ) : (
          <ul className={styles.list}>
            {appointments.map((appointment) => {
              const presentation = appointmentPresentation(appointment, now)
              const arrived = appointment.visit_id !== null
              const cancelled = appointment.status === 'CANCELLED'
              return (
                <li key={appointment.id} className={styles.row}>
                  <span className={styles.rowTime}>
                    {formatTime(appointment.scheduled_at, timeZone)}
                  </span>
                  <span className={styles.rowIdentity}>
                    {appointment.patient.display_name ?? 'Nom non renseigné'}
                  </span>
                  <StatusBadge tone={presentation.tone} label={presentation.label} />
                  <span className={styles.rowActions}>
                    {mayCheckIn && !arrived && !cancelled ? (
                      <Button
                        type="button"
                        onClick={() =>
                          void act(
                            () => client.checkInAppointment(providerId, appointment.id),
                            'Ce patient a déjà une visite en cours.',
                          )
                        }
                        disabled={busy}
                      >
                        Enregistrer l'arrivée
                      </Button>
                    ) : null}
                    {mayManage && !arrived && !cancelled ? (
                      <>
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={() =>
                            setRescheduling({
                              id: appointment.id,
                              date: state.data?.day ?? '',
                              time: formatTime(appointment.scheduled_at, timeZone),
                            })
                          }
                          disabled={busy}
                        >
                          Déplacer
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={() =>
                            void act(
                              () => client.cancelAppointment(providerId, appointment.id),
                              "Ce rendez-vous a déjà donné lieu à une visite et ne peut plus être annulé.",
                            )
                          }
                          disabled={busy}
                        >
                          Annuler
                        </Button>
                      </>
                    ) : null}
                  </span>
                  {rescheduling?.id === appointment.id ? (
                    <div className={styles.form}>
                      <TextInput
                        label="Nouvelle date"
                        type="date"
                        value={rescheduling.date}
                        onChange={(event) =>
                          setRescheduling({ ...rescheduling, date: event.target.value })
                        }
                      />
                      <TextInput
                        label="Nouvelle heure"
                        type="time"
                        value={rescheduling.time}
                        onChange={(event) =>
                          setRescheduling({ ...rescheduling, time: event.target.value })
                        }
                      />
                      <div className={styles.actions}>
                        <Button
                          type="button"
                          onClick={() =>
                            void act(async () => {
                              await client.rescheduleAppointment(providerId, appointment.id, {
                                scheduled_date: rescheduling.date,
                                scheduled_time: `${rescheduling.time}:00`,
                              })
                              if (mounted.current) {
                                setRescheduling(null)
                                setRequestedDay(rescheduling.date)
                              }
                            }, "Ce rendez-vous a déjà donné lieu à une visite et ne peut plus être déplacé.")
                          }
                          disabled={busy || rescheduling.date === '' || rescheduling.time === ''}
                        >
                          Confirmer le déplacement
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={() => setRescheduling(null)}
                          disabled={busy}
                        >
                          Annuler
                        </Button>
                      </div>
                    </div>
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </Card>
  )
}
