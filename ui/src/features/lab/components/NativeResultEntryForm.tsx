import { useEffect, useRef, useState } from 'react'
import { ExchangeApiError, type ExchangeClient } from '../../../api/exchangeClient'
import type { ResultEntryWorkspaceItem } from '../../../api/exchangeTypes'
import { Button, TextInput } from '../../../design-system'

const SAVE_ERROR = "Impossible d'enregistrer ces valeurs. Vous pouvez réessayer."
const COMPLETE_ERROR = "Impossible de confirmer la fin de la saisie. Vous pouvez réessayer."
const COMPLETE_CONFLICT_ERROR =
  'Cette analyse ne peut pas encore être marquée terminée -- une valeur requise manque toujours.'

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
 * own authoritative `entered_values`) is shown read-only, never as an editable input: this ticket
 * builds no correction UI (DH-LAB-005's own scope). No autosave anywhere -- values are only ever
 * sent on an explicit "Enregistrer les valeurs" click, and unsaved draft state lives in this
 * component's own memory only (never localStorage/sessionStorage), so a refresh may discard an
 * in-progress, not-yet-saved draft.
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
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // A fresh authoritative `item` (after a save/completion re-fetch, or a lab/order switch)
  // clears any leftover draft for a definition that is now saved, and clears mutation errors --
  // never carries stale error/draft state across an authoritative refresh.
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
                <p style={{ margin: '2px 0 0', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
                  {entered.numeric_value ?? entered.text_value}
                  {entered.numeric_value && definition.numeric_unit ? ` ${definition.numeric_unit}` : ''}
                </p>
              </div>
            )
          }
          return (
            <TextInput
              key={definition.definition_id}
              label={`${definition.display_label} (${definition.required ? 'Requis' : 'Facultatif'})${
                definition.result_type === 'NUMERIC' && definition.numeric_unit
                  ? ` — ${definition.numeric_unit}`
                  : ''
              }`}
              inputMode={definition.result_type === 'NUMERIC' ? 'decimal' : 'text'}
              required={definition.required}
              aria-required={definition.required}
              disabled={saveState.submitting || item.entry_completed}
              value={drafts[definition.definition_id] ?? ''}
              onChange={(e) =>
                setDrafts((prev) => ({ ...prev, [definition.definition_id]: e.target.value }))
              }
            />
          )
        })}
      </div>

      {saveState.error ? (
        <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)', fontSize: 'var(--font-size-sm)' }}>
          {saveState.error}
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
            Terminer la saisie pour cette analyse ? Vérifiez que toutes les valeurs sont exactes --
            aucune correction n'est disponible pour le moment.
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
