/**
 * DHEALTH-CLINIC-VISIT-001 — one visit: its state, its note history, and what was ordered during it.
 *
 * ── The note is append-only, and the interface says so ──
 *
 * Every save writes a new revision. Earlier revisions stay on screen, dimmed but never hidden and
 * never collapsed behind a "voir l'historique" control. A clinician reading a corrected note is
 * entitled to see that it was corrected and by whom — the discipline ADR-045/048 already established
 * for corrected and amended lab results, applied to the other place in this product where a clinician
 * makes a statement that may need correcting.
 *
 * The editor is deliberately empty on open rather than pre-filled with the current text. Pre-filling
 * would make an append look like an edit, and a clinician who meant to add one line would silently
 * re-author the whole note under their own name.
 *
 * ── What this screen refuses to offer ──
 *
 * *No "Annuler" once a visit is in consultation.* §12: that is a completed-with-a-note, because
 * contact with the patient happened. Offering it and then failing would be worse than not offering it.
 *
 * *No note field before the visit is taken.* A `En attente` visit has no clinician yet by
 * construction, so there is nobody for a note to be attributed to.
 *
 * *No amendment of a completed visit's note.* This is a real clinical need and it is deliberately
 * **not** built here: the codebase already has an explicit, audited, versioned amendment workflow
 * (ADR-048), and inventing a second weaker one inside a lifecycle ticket would be worse than leaving
 * the gap named. The interface says plainly that the note is closed rather than offering a field that
 * the server would refuse.
 *
 * *No result values.* Orders are listed with their status and open in `Patients`, where the existing
 * read paths apply ADR-044's entry-is-not-release filtering. Inlining observations here would be a
 * second result-reading path whose release filtering nobody had qualified.
 *
 * *No completion blocking on laboratory work.* A visit with an outstanding order completes normally.
 * Holding the visit open until a laboratory replies would make the waiting list lie about who is
 * still in the building, and results are followed up from the order and the patient chart.
 */

import { useEffect, useState } from 'react'

import { ExchangeApiError } from '../../api/exchangeClient'
import type { Visit, VisitDetail } from '../../api/exchangeTypes'
import { Button, StatusBadge } from '../../design-system'
import { useClinic } from '../clinic/ClinicContext'
import { formatOrderStatus } from '../clinic/orderStatusVocabulary'
import styles from './Visits.module.css'
import {
  visitDate,
  visitPatientName,
  visitStatusLabel,
  visitStatusTone,
  visitTime,
} from './visitVocabulary'

export interface VisitWorkspaceProps {
  providerId: string
  detail: VisitDetail
  onVisitChanged: (visit: Visit) => void
  onReloadDetail: () => void
  onOrderForVisit?: (context: { patientId: string; visitId: string }) => void
}

type Pending = 'take' | 'complete' | 'cancel' | 'note' | 'reason' | null

export function VisitWorkspace({
  providerId,
  detail,
  onVisitChanged,
  onReloadDetail,
  onOrderForVisit,
}: VisitWorkspaceProps) {
  const { client } = useClinic()
  const { visit, notes, orders } = detail

  const [pending, setPending] = useState<Pending>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [noteDraft, setNoteDraft] = useState('')
  const [reasonDraft, setReasonDraft] = useState<string | null>(null)
  const [confirmingCancel, setConfirmingCancel] = useState(false)

  // Moving to another visit must not carry a half-written note or an open confirmation with it.
  useEffect(() => {
    setNoteDraft('')
    setReasonDraft(null)
    setProblem(null)
    setPending(null)
    setConfirmingCancel(false)
  }, [visit.id])

  const describe = (error: unknown, fallback: string): string => {
    if (error instanceof ExchangeApiError) {
      if (error.status === 409) {
        // The server's 409 bodies are specific and worth surfacing: "already in progress under
        // another clinician", "cannot go from COMPLETED to ...". A generic message here would hide
        // the one fact the clinician needs, which is usually that a colleague acted first.
        return `${fallback} L'état de la visite a changé entre-temps ; la vue a été actualisée.`
      }
      if (error.status === 404) return 'Cette visite n’est plus accessible.'
      if (error.code === 'NETWORK_ERROR') return 'Le réseau n’a pas répondu. Réessayez.'
    }
    return fallback
  }

  const run = async (action: Pending, operation: () => Promise<Visit>, fallback: string) => {
    setPending(action)
    setProblem(null)
    try {
      const updated = await operation()
      onVisitChanged(updated)
    } catch (error) {
      setProblem(describe(error, fallback))
      // A refused transition means this view is stale. Re-reading is how the clinician sees what
      // actually happened instead of arguing with a button.
      if (error instanceof ExchangeApiError && (error.status === 409 || error.status === 404)) {
        onReloadDetail()
      }
    } finally {
      setPending(null)
    }
  }

  const handleAppendNote = async () => {
    const body = noteDraft.trim()
    if (!body) {
      setProblem('La note est vide.')
      return
    }
    setPending('note')
    setProblem(null)
    try {
      await client.appendVisitNote(providerId, visit.id, { body })
      setNoteDraft('')
      onReloadDetail()
    } catch (error) {
      setProblem(describe(error, "La note n'a pas pu être enregistrée."))
    } finally {
      setPending(null)
    }
  }

  const handleSaveReason = async () => {
    if (reasonDraft === null) return
    const next = reasonDraft.trim()
    if (!next) {
      setProblem('Le motif ne peut pas être vide.')
      return
    }
    await run(
      'reason',
      () => client.updateVisitReason(providerId, visit.id, { reason_for_visit: next }),
      "Le motif n'a pas pu être corrigé.",
    )
    setReasonDraft(null)
  }

  const isWaiting = visit.status === 'CHECKED_IN'
  const isInProgress = visit.status === 'IN_PROGRESS'
  const isTerminal = visit.status === 'COMPLETED' || visit.status === 'CANCELLED'
  const currentRevision = notes.length > 0 ? notes[notes.length - 1].revision_number : 0

  return (
    <>
      <div className={styles.detailHeader}>
        <div>
          <h2 className={styles.panelTitle}>{visitPatientName(visit.patient_display_name)}</h2>
          <p className={styles.meta}>
            <span>
              {visit.patient_date_of_birth
                ? `Né(e) le ${visit.patient_date_of_birth}`
                : 'Date de naissance non enregistrée'}
            </span>
            <span>Visite du {visitDate(visit.created_at)}</span>
            <span>Arrivée {visitTime(visit.created_at)}</span>
          </p>
        </div>
        <StatusBadge label={visitStatusLabel(visit.status)} tone={visitStatusTone(visit.status)} />
      </div>

      {problem ? (
        <p className={styles.problem} role="alert">
          {problem}
        </p>
      ) : null}

      <div className={styles.detailActions}>
        {isWaiting ? (
          <Button
            disabled={pending !== null}
            onClick={() =>
              void run(
                'take',
                () => client.takeVisit(providerId, visit.id),
                "La prise en charge n'a pas abouti.",
              )
            }
          >
            {pending === 'take' ? 'Prise en charge…' : 'Prendre en charge'}
          </Button>
        ) : null}

        {isInProgress ? (
          <Button
            disabled={pending !== null}
            onClick={() =>
              void run(
                'complete',
                () => client.completeVisit(providerId, visit.id),
                "La visite n'a pas pu être terminée.",
              )
            }
          >
            {pending === 'complete' ? 'Fin de la visite…' : 'Terminer la visite'}
          </Button>
        ) : null}

        {isInProgress && onOrderForVisit ? (
          <Button
            variant="secondary"
            onClick={() => onOrderForVisit({ patientId: visit.patient_id, visitId: visit.id })}
          >
            Demander des analyses
          </Button>
        ) : null}

        {/* Cancellation exists only before the visit is taken. See this file's header: §12 makes an
            in-progress visit a completed-with-a-note, never a cancellation. */}
        {isWaiting && !confirmingCancel ? (
          <Button variant="secondary" disabled={pending !== null} onClick={() => setConfirmingCancel(true)}>
            Annuler la visite
          </Button>
        ) : null}
      </div>

      {confirmingCancel ? (
        <div className={styles.section}>
          <p className={styles.hint}>
            Annuler la visite de <strong>{visitPatientName(visit.patient_display_name)}</strong> ? La
            visite restera visible dans l'historique comme annulée. Une visite déjà prise en charge ne
            peut plus être annulée : elle se termine avec une note.
          </p>
          <div className={styles.formActions}>
            <Button
              disabled={pending !== null}
              onClick={() => {
                setConfirmingCancel(false)
                void run(
                  'cancel',
                  () => client.cancelVisit(providerId, visit.id),
                  "La visite n'a pas pu être annulée.",
                )
              }}
            >
              Confirmer l'annulation
            </Button>
            <Button variant="secondary" onClick={() => setConfirmingCancel(false)}>
              Revenir
            </Button>
          </div>
        </div>
      ) : null}

      <div className={styles.section}>
        <h3 className={styles.sectionTitle}>Motif</h3>
        {reasonDraft === null ? (
          <>
            <p className={styles.revisionBody}>{visit.reason_for_visit}</p>
            {!isTerminal ? (
              <button
                type="button"
                className={styles.filterButton}
                onClick={() => setReasonDraft(visit.reason_for_visit)}
              >
                Corriger le motif
              </button>
            ) : (
              <p className={styles.hint}>
                Le motif n'est plus modifiable : la visite est terminée ou annulée.
              </p>
            )}
          </>
        ) : (
          <>
            <textarea
              className={styles.noteArea}
              style={{ minHeight: '4rem' }}
              maxLength={500}
              value={reasonDraft}
              onChange={(event) => setReasonDraft(event.target.value)}
              aria-label="Motif de la visite"
            />
            <div className={styles.formActions}>
              <Button disabled={pending !== null} onClick={() => void handleSaveReason()}>
                Enregistrer le motif
              </Button>
              <Button variant="secondary" onClick={() => setReasonDraft(null)}>
                Annuler
              </Button>
            </div>
          </>
        )}
      </div>

      <div className={styles.section}>
        <h3 className={styles.sectionTitle}>
          Note clinique{currentRevision > 0 ? ` — révision ${currentRevision}` : ''}
        </h3>

        {isInProgress ? (
          <>
            <label className={styles.hint} htmlFor="visit-note-draft">
              Chaque enregistrement ajoute une révision. Les révisions précédentes restent visibles et
              ne sont jamais remplacées.
            </label>
            <textarea
              id="visit-note-draft"
              className={styles.noteArea}
              maxLength={20000}
              value={noteDraft}
              onChange={(event) => setNoteDraft(event.target.value)}
            />
            <div className={styles.formActions}>
              <Button disabled={pending !== null} onClick={() => void handleAppendNote()}>
                {pending === 'note' ? 'Enregistrement…' : 'Enregistrer la note'}
              </Button>
            </div>
          </>
        ) : isWaiting ? (
          <p className={styles.hint}>
            Prenez la visite en charge pour écrire une note. Une visite en attente n'a pas encore de
            clinicien à qui attribuer ce qui est écrit.
          </p>
        ) : (
          <p className={styles.hint}>
            La visite est terminée ou annulée : la note n'est plus modifiable ici. Une correction
            après la fin de la visite relève d'une procédure de rectification distincte, qui n'est
            pas encore disponible dans DHealth.
          </p>
        )}

        {notes.length === 0 ? (
          <p className={styles.hint}>Aucune note n'a été consignée.</p>
        ) : (
          <ul className={styles.revisions}>
            {notes.map((note) => {
              const superseded = note.revision_number !== currentRevision
              return (
                <li
                  key={note.id}
                  className={[styles.revision, superseded ? styles.revisionSuperseded : '']
                    .filter(Boolean)
                    .join(' ')}
                >
                  <p className={styles.revisionMeta}>
                    <span>Révision {note.revision_number}</span>
                    <span>
                      {visitDate(note.created_at)} à {visitTime(note.created_at)}
                    </span>
                    {superseded ? <span>Remplacée par une révision ultérieure</span> : <span>Actuelle</span>}
                  </p>
                  <p className={styles.revisionBody}>{note.body}</p>
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <div className={styles.section}>
        <h3 className={styles.sectionTitle}>Analyses demandées pendant cette visite</h3>
        {orders.length === 0 ? (
          <p className={styles.hint}>
            Aucune analyse n'a été demandée pendant cette visite.
            {isInProgress ? " Utilisez « Demander des analyses » ci-dessus." : ''}
          </p>
        ) : (
          <ul className={styles.orderList}>
            {orders.map((order) => (
              <li key={order.id} className={styles.orderRow}>
                <span>
                  {order.item_count} analyse{order.item_count > 1 ? 's' : ''} — demandée{' '}
                  {visitTime(order.created_at)}
                </span>
                {/* The provider's own wording, shared with `Patients` rather than duplicated here.
                    Rendering `order.status` raw would have put `AWAITING_LAB_SELECTION` on a
                    French-first screen. */}
                <StatusBadge {...formatOrderStatus(order.status)} />
              </li>
            ))}
          </ul>
        )}
        <p className={styles.hint}>
          Les résultats se consultent depuis l'ordre lui-même, dans « Patients » et « Résultats ».
          Terminer la visite n'attend pas le laboratoire.
        </p>
      </div>
    </>
  )
}
