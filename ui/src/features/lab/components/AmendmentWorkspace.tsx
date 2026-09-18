import { useCallback, useEffect, useRef, useState } from 'react'
import { ExchangeApiError, type ExchangeClient } from '../../../api/exchangeClient'
import type { AmendmentInspection } from '../../../api/exchangeTypes'
import { Button, LoadingSkeleton, StatusBadge, TextInput } from '../../../design-system'

const AMENDMENT_LOAD_ERROR = "Impossible de charger le détail de cet amendement."
const CORRECTION_ERROR = "Impossible d'enregistrer cette correction. Vous pouvez réessayer."
const CORRECTION_STALE_ERROR =
  'Cette valeur a déjà été modifiée depuis votre dernière consultation. La valeur actuelle est affichée ci-dessous.'

export interface AmendmentWorkspaceProps {
  client: ExchangeClient
  labId: string
  orderId: string
  amendmentId: string
  /** Called after every successful correction, so a parent that also shows this amendment's
   * fingerprint/release-readiness (e.g. an "Amendements" release panel) can re-read it. */
  onChanged?: () => void
}

/**
 * DH-LAB-005 §30-35/§58: the LAB_TECH-facing amendment workspace -- the currently-proposed
 * (effective) observation set for a PREPARING amendment, with "Modifié dans cet amendement"
 * labeling per item (`changed_in_this_amendment`, a DB-grounded fact, never re-derived
 * client-side) and a Native controlled correction control per template-bound observation. Before
 * the amendment's *first* correction (`ready_to_release === false`, i.e. no revision exists yet),
 * the confirmation carries the durable-revision warning (§58) -- there is no "Annuler
 * l'amendement" control anywhere in this component or this version of DHealth: PREPARING/RELEASED
 * are the only two states an amendment can ever be in.
 */
export function AmendmentWorkspace({ client, labId, orderId, amendmentId, onChanged }: AmendmentWorkspaceProps) {
  const [state, setState] = useState<{
    loading: boolean
    error: string | null
    inspection: AmendmentInspection | null
  }>({ loading: true, error: null, inspection: null })
  const [correctionState, setCorrectionState] = useState<{
    observationId: string | null
    draft: string
    submitting: boolean
    error: string | null
  }>({ observationId: null, draft: '', submitting: false, error: null })
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const load = useCallback(() => {
    setState((prev) => ({ loading: true, error: null, inspection: prev.inspection }))
    client
      .getLabOrderAmendment(labId, orderId, amendmentId)
      .then((inspection) => {
        if (!mountedRef.current) return
        setState({ loading: false, error: null, inspection })
      })
      .catch(() => {
        if (!mountedRef.current) return
        setState({ loading: false, error: AMENDMENT_LOAD_ERROR, inspection: null })
      })
  }, [client, labId, orderId, amendmentId])

  useEffect(() => {
    load()
  }, [load])

  function startCorrection(observationId: string) {
    setCorrectionState({ observationId, draft: '', submitting: false, error: null })
  }

  function cancelCorrection() {
    setCorrectionState({ observationId: null, draft: '', submitting: false, error: null })
  }

  async function handleSubmitCorrection(itemId: string, resultType: 'NUMERIC' | 'TEXT') {
    if (!correctionState.observationId) return
    const value = correctionState.draft.trim()
    if (!value) return
    setCorrectionState((prev) => ({ ...prev, submitting: true, error: null }))
    try {
      await client.correctAmendmentNativeObservation(
        labId,
        orderId,
        amendmentId,
        itemId,
        correctionState.observationId,
        resultType === 'NUMERIC' ? { numeric_value: value } : { text_value: value },
      )
      if (!mountedRef.current) return
      setCorrectionState({ observationId: null, draft: '', submitting: false, error: null })
      load()
      onChanged?.()
    } catch (error: unknown) {
      if (!mountedRef.current) return
      const isConflict = error instanceof ExchangeApiError && error.status === 409
      setCorrectionState({
        observationId: null,
        draft: '',
        submitting: false,
        error: isConflict ? CORRECTION_STALE_ERROR : CORRECTION_ERROR,
      })
      load()
      onChanged?.()
    }
  }

  if (state.loading && !state.inspection) {
    return <LoadingSkeleton />
  }
  if (state.error) {
    return (
      <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)' }}>
        {state.error}
      </p>
    )
  }
  if (!state.inspection) return null
  const { inspection } = state
  const firstCorrectionInThisAmendment = !inspection.ready_to_release

  return (
    <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
      {inspection.items.map((item) => (
        <div
          key={item.item_id}
          style={{
            border: '1px solid var(--color-neutral-200)',
            borderRadius: 'var(--radius-md, 6px)',
            padding: 'var(--space-3)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-2)' }}>
            <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
              {item.catalog_display_name_snapshot}
            </p>
            {item.changed_in_this_amendment ? (
              <StatusBadge tone="warning" label="Modifié dans cet amendement" />
            ) : null}
          </div>

          <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
            {item.observations.map((observation) => {
              const isCorrecting = correctionState.observationId === observation.observation_id
              const correctable = observation.template_observation_definition_id !== null
              return (
                <div
                  key={observation.observation_id}
                  style={{
                    border: '1px solid var(--color-neutral-200)',
                    borderRadius: 'var(--radius-md, 6px)',
                    padding: 'var(--space-2) var(--space-3)',
                  }}
                >
                  <p style={{ margin: 0, fontSize: 'var(--font-size-xs)', color: 'var(--color-neutral-600)' }}>
                    {observation.display_label_snapshot}
                  </p>
                  <p style={{ margin: '2px 0 0', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
                    {observation.numeric_value ?? observation.text_value}
                    {observation.numeric_value && observation.numeric_unit
                      ? ` ${observation.numeric_unit}`
                      : ''}
                  </p>

                  {correctable ? (
                    isCorrecting ? (
                      <div style={{ display: 'grid', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
                        <TextInput
                          label="Nouvelle valeur"
                          inputMode={observation.result_type === 'NUMERIC' ? 'decimal' : 'text'}
                          disabled={correctionState.submitting}
                          value={correctionState.draft}
                          onChange={(e) =>
                            setCorrectionState((prev) => ({ ...prev, draft: e.target.value }))
                          }
                        />
                        <p role="alert" style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                          Cette correction conservera l'ancienne valeur dans l'historique.
                          {firstCorrectionInThisAmendment ? (
                            <>
                              {' '}
                              Cette correction sera conservée dans l'historique de l'amendement. Il
                              n'existe pas d'annulation de l'amendement dans cette version de
                              DHealth.
                            </>
                          ) : null}
                        </p>
                        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                          <Button
                            type="button"
                            onClick={() =>
                              void handleSubmitCorrection(item.item_id, observation.result_type)
                            }
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
                          onClick={() => startCorrection(observation.observation_id)}
                          disabled={correctionState.submitting}
                        >
                          Corriger
                        </Button>
                      </div>
                    )
                  ) : null}
                </div>
              )
            })}
          </div>
        </div>
      ))}

      {correctionState.error ? (
        <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)', fontSize: 'var(--font-size-sm)' }}>
          {correctionState.error}
        </p>
      ) : null}
    </div>
  )
}
