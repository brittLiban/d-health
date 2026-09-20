import { useEffect, useRef, useState } from 'react'
import { ExchangeApiError, type ExchangeClient } from '../../../api/exchangeClient'
import type { LabObservation, ResultEntryWorkspaceItem } from '../../../api/exchangeTypes'
import { Button, TextInput } from '../../../design-system'

const SAVE_ERROR = "Impossible d'enregistrer ces valeurs. Vous pouvez réessayer."
const COMPLETE_ERROR = "Impossible de confirmer la fin de la saisie. Vous pouvez réessayer."
const COMPLETE_CONFLICT_ERROR =
  'Cette analyse ne peut pas encore être marquée terminée -- une valeur requise manque toujours.'
const CORRECTION_ERROR = "Impossible d'enregistrer cette correction. Vous pouvez réessayer."
const CORRECTION_STALE_ERROR =
  'Cette valeur a déjà été modifiée depuis votre dernière consultation. La valeur actuelle est affichée ci-dessous.'
const HISTORY_LOAD_ERROR = "Impossible de charger l'historique de cette analyse."

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString('fr-FR')
}

export interface NativeResultEntryFormProps {
  client: ExchangeClient
  labId: string
  orderId: string
  item: ResultEntryWorkspaceItem
  /** Called after a save or a completion succeeds -- the parent re-fetches the whole workspace
   * from the server and passes the new `item` back down; this component never assumes its own
   * optimistic state is authoritative (DH-LAB-004 §71: saved values are always shown from an
   * authoritative re-read, never from the value the form itself just sent). */
  onChanged: () => void
}

/**
 * DH-LAB-004: turns LAB-003's read-only structure preview into a controlled Native entry form.
 * Renders exactly the definitions the pinned/active template actually configures -- never a
 * hardcoded example field. A definition that already has an effective value (per the workspace's
 * own authoritative `entered_values`) is shown read-only, never as an editable input.
 *
 * DH-LAB-005 §8-16/§20: a `NATIVE_TEMPLATE` item's already-entered value offers "Corriger" --
 * the browser submits only the new value; every structural field is derived server-side from the
 * predecessor's own pinned template definition. Correction is available regardless of whether
 * entry is already marked complete (entry completion is not release -- correction stays a
 * pre-release action all the way up to the moment this order's results are actually released).
 * §H1-6: "Voir l'historique" exposes the full correction chain via the existing history read,
 * with the superseded predecessor and the effective successor always labeled unambiguously,
 * never shown as equally current. No correction-reason field of any kind exists anywhere here
 * (ADR-045's MVP decision, unchanged by this ticket).
 *
 * No autosave anywhere -- values are only ever sent on an explicit "Enregistrer les valeurs"
 * click, and unsaved draft state lives in this component's own memory only (never localStorage/
 * sessionStorage), so a refresh may discard an in-progress, not-yet-saved draft.
 */
export function NativeResultEntryForm({ client, labId, orderId, item, onChanged }: NativeResultEntryFormProps) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [saveState, setSaveState] = useState<{ submitting: boolean; error: string | null }>({
    submitting: false,
    error: null,
  })
  const [confirmingComplete, setConfirmingComplete] = useState(false)
  const [completeState, setCompleteState] = useState<{ submitting: boolean; error: string | null }>({
    submitting: false,
    error: null,
  })
  const [correctionState, setCorrectionState] = useState<{
    observationId: string | null
    draft: string
    submitting: boolean
    error: string | null
  }>({ observationId: null, draft: '', submitting: false, error: null })
  const [historyState, setHistoryState] = useState<{
    open: boolean
    loading: boolean
    error: string | null
    observations: LabObservation[] | null
  }>({ open: false, loading: false, error: null, observations: null })
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // A fresh authoritative `item` (after a save/completion/correction re-fetch, or a lab/order
  // switch) clears any leftover draft for a definition that is now saved, and clears mutation
  // errors -- never carries stale error/draft state across an authoritative refresh.
  useEffect(() => {
    setDrafts((prev) => {
      const next: Record<string, string> = {}
      for (const [definitionId, value] of Object.entries(prev)) {
        const alreadySaved = item.entered_values.some((v) => v.definition_id === definitionId)
        if (!alreadySaved) next[definitionId] = value
      }
      return next
    })
    setSaveState({ submitting: false, error: null })
    setConfirmingComplete(false)
    setCompleteState({ submitting: false, error: null })
    setCorrectionState({ observationId: null, draft: '', submitting: false, error: null })
    setHistoryState({ open: false, loading: false, error: null, observations: null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.item_id, item.entered_values.length, item.entry_completed])

  if (item.entry_mode === 'UNSTARTED_NO_TEMPLATE') {
    return (
      <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
        Aucune structure de résultat n'est configurée pour cette analyse -- la saisie native n'est
        pas disponible.
      </p>
    )
  }

  if (item.entry_mode === 'LEGACY_OR_EXTERNAL') {
    return (
      <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
        Cette analyse a déjà une valeur enregistrée par une autre voie -- la saisie contrôlée
        native n'est pas disponible pour cet élément.
      </p>
    )
  }

  const template = item.template
  if (!template) return null

  const enteredByDefinition = new Map(
    item.entered_values.filter((v) => v.definition_id).map((v) => [v.definition_id as string, v]),
  )
  const requiredMissing = template.observations.filter(
    (definition) => definition.required && !enteredByDefinition.has(definition.definition_id),
  )
  const canComplete = !item.entry_completed && requiredMissing.length === 0
  const pendingDrafts = Object.entries(drafts).filter(([, value]) => value.trim() !== '')

  async function handleSave() {
    if (pendingDrafts.length === 0 || !template) return
    setSaveState({ submitting: true, error: null })
    try {
      const values = pendingDrafts.map(([definitionId, value]) => {
        const definition = template.observations.find((o) => o.definition_id === definitionId)
        if (definition?.result_type === 'NUMERIC') {
          return { definition_id: definitionId, numeric_value: value.trim() }
        }
        return { definition_id: definitionId, text_value: value.trim() }
      })
      await client.recordNativeResultValues(labId, orderId, item.item_id, {
        template_id: template.template_id,
        values,
      })
      if (!mountedRef.current) return
      setSaveState({ submitting: false, error: null })
      onChanged()
    } catch {
      if (!mountedRef.current) return
      setSaveState({ submitting: false, error: SAVE_ERROR })
    }
  }

  async function handleComplete() {
    setCompleteState({ submitting: true, error: null })
    try {
      await client.completeResultEntry(labId, orderId, item.item_id)
      if (!mountedRef.current) return
      setCompleteState({ submitting: false, error: null })
      setConfirmingComplete(false)
      onChanged()
    } catch (error: unknown) {
      if (!mountedRef.current) return
      setConfirmingComplete(false)
      // DH-LAB-004 §71: lost-response recovery is authoritative re-read, never a blind retry --
      // the parent's `onChanged` re-fetches the workspace regardless of outcome below, so a
      // genuinely-successful-but-unconfirmed completion still shows correctly afterward.
      const isConflict = error instanceof ExchangeApiError && error.status === 409
      setCompleteState({
        submitting: false,
        error: isConflict ? COMPLETE_CONFLICT_ERROR : COMPLETE_ERROR,
      })
      onChanged()
    }
  }

  function startCorrection(observationId: string) {
    setCorrectionState({ observationId, draft: '', submitting: false, error: null })
  }

  function cancelCorrection() {
    setCorrectionState({ observationId: null, draft: '', submitting: false, error: null })
  }

  async function handleSubmitCorrection(definitionId: string) {
    if (!correctionState.observationId || !template) return
    const definition = template.observations.find((o) => o.definition_id === definitionId)
    if (!definition) return
    const value = correctionState.draft.trim()
    if (!value) return
    setCorrectionState((prev) => ({ ...prev, submitting: true, error: null }))
    try {
      await client.correctNativeObservation(
        labId,
        orderId,
        item.item_id,
        correctionState.observationId,
        definition.result_type === 'NUMERIC' ? { numeric_value: value } : { text_value: value },
      )
      if (!mountedRef.current) return
      setCorrectionState({ observationId: null, draft: '', submitting: false, error: null })
      onChanged()
    } catch (error: unknown) {
      if (!mountedRef.current) return
      // DH-LAB-005 §11: a stale predecessor (already corrected elsewhere in the meantime) is a
      // real, expected conflict, not a generic failure -- re-read authoritative state either way,
      // never a blind retry.
      const isConflict = error instanceof ExchangeApiError && error.status === 409
      setCorrectionState({
        observationId: null,
        draft: '',
        submitting: false,
        error: isConflict ? CORRECTION_STALE_ERROR : CORRECTION_ERROR,
      })
      onChanged()
    }
  }

  async function handleToggleHistory() {
    if (historyState.open) {
      setHistoryState({ open: false, loading: false, error: null, observations: null })
      return
    }
    setHistoryState({ open: true, loading: true, error: null, observations: null })
    try {
      const history = await client.getLabObservationHistory(labId, orderId, item.item_id)
      if (!mountedRef.current) return
      setHistoryState({ open: true, loading: false, error: null, observations: history.observations })
    } catch {
      if (!mountedRef.current) return
      setHistoryState({ open: true, loading: false, error: HISTORY_LOAD_ERROR, observations: null })
    }
  }

  return (
    <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
      {template.is_synthetic ? (
        <p
          role="alert"
          style={{
            margin: 0,
            fontSize: 'var(--font-size-xs)',
            color: 'var(--color-danger, #b91c1c)',
          }}
        >
          Configuration de démonstration — ne pas utiliser pour des soins réels.
        </p>
      ) : null}

      <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
        {template.observations.map((definition) => {
          const entered = enteredByDefinition.get(definition.definition_id)
          if (entered) {
            // DH-LAB-005: a NATIVE_TEMPLATE item's entered value always came through the Native
            // batch write, so it always answers a controlled template definition -- the Native
            // controlled correction control always applies here (never the raw, now-hardened
            // free-structure endpoint).
            const isCorrectingThis = correctionState.observationId === entered.observation_id
            return (
              <div
                key={definition.definition_id}
                style={{
                  border: '1px solid var(--color-neutral-200)',
                  borderRadius: 'var(--radius-md, 6px)',
                  padding: 'var(--space-2) var(--space-3)',
                }}
              >
                <p style={{ margin: 0, fontSize: 'var(--font-size-xs)', color: 'var(--color-neutral-600)' }}>
                  {definition.display_label} ({definition.required ? 'Requis' : 'Facultatif'})
                </p>
                <p style={{ margin: '2px 0 0', fontSize: 'var(--font-size-xs)', color: 'var(--color-neutral-600)' }}>
                  Valeur actuelle
                </p>
                <p style={{ margin: '2px 0 0', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
                  {entered.numeric_value ?? entered.text_value}
                  {entered.numeric_value && definition.numeric_unit ? ` ${definition.numeric_unit}` : ''}
                </p>

                {isCorrectingThis ? (
                  <div style={{ display: 'grid', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
                    <TextInput
                      label="Nouvelle valeur"
                      inputMode={definition.result_type === 'NUMERIC' ? 'decimal' : 'text'}
                      disabled={correctionState.submitting}
                      value={correctionState.draft}
                      onChange={(e) =>
                        setCorrectionState((prev) => ({ ...prev, draft: e.target.value }))
                      }
                    />
                    <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                      Cette correction conservera l'ancienne valeur dans l'historique.
                    </p>
                    <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                      <Button
                        type="button"
                        onClick={() => void handleSubmitCorrection(definition.definition_id)}
                        disabled={correctionState.submitting || correctionState.draft.trim() === ''}
                      >
                        {correctionState.submitting ? 'Enregistrement…' : 'Confirmer la correction'}
                      </Button>
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={cancelCorrection}
                        disabled={correctionState.submitting}
                      >
                        Annuler
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div style={{ marginTop: 'var(--space-2)' }}>
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() => startCorrection(entered.observation_id)}
                      disabled={correctionState.submitting}
                    >
                      Corriger
                    </Button>
                  </div>
                )}
              </div>
            )
          }
          const draft = drafts[definition.definition_id] ?? ''
          const unsaved = draft.trim() !== ''
          return (
            <div key={definition.definition_id} style={{ display: 'grid', gap: 'var(--space-1)' }}>
              <TextInput
                label={`${definition.display_label} (${definition.required ? 'Requis' : 'Facultatif'})${
                  definition.result_type === 'NUMERIC' && definition.numeric_unit
                    ? ` — ${definition.numeric_unit}`
                    : ''
                }`}
                inputMode={definition.result_type === 'NUMERIC' ? 'decimal' : 'text'}
                required={definition.required}
                aria-required={definition.required}
                disabled={saveState.submitting || item.entry_completed}
                value={draft}
                onChange={(e) =>
                  setDrafts((prev) => ({ ...prev, [definition.definition_id]: e.target.value }))
                }
              />
              {/* DH-MVP-POLISH-001 — a typed value is not a recorded value.
                  There is no autosave here, deliberately: a half-typed
                  haemoglobin must never reach the exchange. The cost of that
                  choice is that a technician can believe they have recorded
                  something they have only typed, so each field that holds an
                  unsaved value says so, field by field, rather than relying on a
                  single banner at the bottom of a long panel.
                  These values live in this component's memory only — never
                  localStorage, sessionStorage or IndexedDB — so the warning also
                  tells the truth about what loses them.

                  Two things do. A refresh, obviously. And a failed token refresh:
                  ExchangeAuthProvider's `onAuthRefreshError` sets status `error`,
                  ExchangeAuthGate then renders its own notice instead of the tree
                  below it, and this component unmounts with the drafts in it. A
                  *successful* silent refresh costs nothing — the tree never
                  unmounts, which is the ordinary case for a token expiring while
                  the panel sits open. The failure path cannot be bridged without
                  persisting clinical values outside this component, which is
                  exactly what is forbidden, so the marker names it instead of
                  pretending otherwise. */}
              {unsaved ? (
                <p
                  style={{
                    margin: 0,
                    fontSize: 'var(--font-size-xs)',
                    color: 'var(--color-waiting)',
                  }}
                >
                  Non enregistré — cette valeur sera perdue si vous actualisez la page ou si
                  votre session expire.
                </p>
              ) : null}
            </div>
          )
        })}
      </div>

      {saveState.error ? (
        <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)', fontSize: 'var(--font-size-sm)' }}>
          {saveState.error}
        </p>
      ) : null}

      {correctionState.error ? (
        <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)', fontSize: 'var(--font-size-sm)' }}>
          {correctionState.error}
        </p>
      ) : null}

      {enteredByDefinition.size > 0 ? (
        <div>
          <Button type="button" variant="secondary" onClick={() => void handleToggleHistory()}>
            {historyState.open ? "Masquer l'historique" : "Voir l'historique"}
          </Button>
          {historyState.open ? (
            <div style={{ marginTop: 'var(--space-2)' }}>
              {historyState.loading ? (
                <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-neutral-600)' }}>
                  Chargement de l'historique…
                </p>
              ) : historyState.error ? (
                <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)', fontSize: 'var(--font-size-sm)' }}>
                  {historyState.error}
                </p>
              ) : historyState.observations ? (
                <ul style={{ margin: 0, paddingLeft: 'var(--space-4)', display: 'grid', gap: 'var(--space-1)' }}>
                  {historyState.observations.map((observation) => (
                    <li
                      key={observation.observation_id}
                      style={{
                        fontSize: 'var(--font-size-sm)',
                        color: observation.superseded_at
                          ? 'var(--color-neutral-500)'
                          : 'var(--color-neutral-900)',
                      }}
                    >
                      <strong>{observation.superseded_at ? 'Historique' : 'Valeur actuelle'}</strong>
                      {' — '}
                      {observation.display_label_snapshot} :{' '}
                      {observation.numeric_value ?? observation.text_value}
                      {observation.superseded_at
                        ? ` (remplacée le ${formatDateTime(observation.superseded_at)})`
                        : ''}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {!item.entry_completed && pendingDrafts.length > 0 ? (
        // The count, so the state of the whole panel is answerable without
        // scrolling every field. `role="status"` rather than an alert: nothing has
        // gone wrong, there is simply work not yet committed.
        <p
          role="status"
          style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-waiting)' }}
        >
          {pendingDrafts.length === 1
            ? '1 valeur saisie n’est pas encore enregistrée.'
            : `${pendingDrafts.length} valeurs saisies ne sont pas encore enregistrées.`}
        </p>
      ) : null}

      {!item.entry_completed ? (
        <Button
          type="button"
          variant="secondary"
          onClick={() => void handleSave()}
          disabled={saveState.submitting || pendingDrafts.length === 0}
        >
          {saveState.submitting ? 'Enregistrement…' : 'Enregistrer les valeurs'}
        </Button>
      ) : null}

      {completeState.error ? (
        <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)', fontSize: 'var(--font-size-sm)' }}>
          {completeState.error}
        </p>
      ) : null}

      {item.entry_completed ? (
        <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-success, #15803d)' }}>
          Saisie terminée{item.entry_completed_at ? ` — ${new Date(item.entry_completed_at).toLocaleString('fr-FR')}` : ''}.
        </p>
      ) : confirmingComplete ? (
        <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
          <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
            Terminer la saisie pour cette analyse ? Vérifiez que toutes les valeurs sont exactes.
          </p>
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <Button type="button" onClick={() => void handleComplete()} disabled={completeState.submitting}>
              {completeState.submitting ? 'Enregistrement…' : 'Confirmer la fin de la saisie'}
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setConfirmingComplete(false)}
              disabled={completeState.submitting}
            >
              Annuler
            </Button>
          </div>
        </div>
      ) : (
        <Button type="button" onClick={() => setConfirmingComplete(true)} disabled={!canComplete}>
          Terminer la saisie
        </Button>
      )}
    </div>
  )
}
