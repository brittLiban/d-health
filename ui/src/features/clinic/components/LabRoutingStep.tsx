import { useCallback, useEffect, useRef, useState } from 'react'
import { ExchangeApiError, type ExchangeClient } from '../../../api/exchangeClient'
import type {
  LabAssignmentSummary,
  LabOrderItem,
  PatientIdentitySummary,
  ProviderEligibleLab,
} from '../../../api/exchangeTypes'
import { Button, Card, LoadingSkeleton } from '../../../design-system'
import { PatientIdentity } from './PatientIdentity'
import styles from './LabRoutingStep.module.css'

interface LabRoutingStepProps {
  client: ExchangeClient
  providerId: string
  orderId: string
  patient: PatientIdentitySummary
  items: LabOrderItem[]
  onAssigned: (assignment: LabAssignmentSummary) => void
}

// DH-CLINIC-006 §15: the exact truthful French message -- zero eligible labs is a real, valid
// state, never an error and never a fabricated fallback lab.
const ZERO_ELIGIBLE_MESSAGE =
  'Aucun laboratoire configuré ne peut actuellement réaliser toutes les analyses de cet ordre.'
const ELIGIBLE_LABS_LOAD_ERROR =
  'Impossible de charger les laboratoires disponibles pour cet ordre.'
const ASSIGN_ERROR_FALLBACK = "Impossible d'envoyer cet ordre au laboratoire sélectionné."
const ASSIGN_CONFLICT_MESSAGE =
  "Ce laboratoire n'est plus disponible pour cet ordre. La liste a été actualisée -- veuillez choisir à nouveau."

/**
 * DH-CLINIC-006: the shared provider-lab-routing UI -- an order's currently-eligible,
 * provider-configured labs (§13), an explicit destination confirmation (§33), and the patient +
 * ordered-tests context shown alongside the lab choice so confirming a lab also re-confirms the
 * right patient and the right tests (§38), never the lab alone. Used both from the new-order
 * wizard and directly from the Orders page for an already-committed, still-unassigned order
 * (§36/§37) -- both call sites share this exact component so the stale-response discipline (§39-
 * 42) and the zero-eligible-labs/error/retry states are never implemented twice.
 *
 * Deliberately shows only name/address/phone per lab -- no price, no ranking, no "best"/cheapest/
 * star-rating/distance/sponsored badge of any kind (§32). Never calls the public marketplace
 * (`listLabs`), the marketplace lab-options endpoint, any price endpoint, or the patient lab
 * selection endpoint (§26) -- only `getEligibleLabs`/`assignLab`. This is structurally guaranteed,
 * not merely a convention: the Clinic-facing `ExchangeClient` has no such methods to call at all.
 *
 * Callers should render this with `key={orderId}` (or a key combining provider+order) whenever it
 * can be shown for more than one order across its lifetime (e.g. the Orders page's expandable
 * per-row panel) -- a fresh key gives a clean remount with no assignment-error carryover from a
 * previous order, rather than relying on internal prop-diffing to reset that cosmetic state. The
 * generation-ref/provider-ref/order-ref discipline inside this component still independently
 * guards against a stale response landing after a rapid double order switch within one mounted
 * instance (§39-42) -- the `key` recommendation above is about UX freshness, not correctness.
 */
export function LabRoutingStep({
  client,
  providerId,
  orderId,
  patient,
  items,
  onAssigned,
}: LabRoutingStepProps) {
  const [labsState, setLabsState] = useState<{
    labs: ProviderEligibleLab[]
    loading: boolean
    error: string | null
  }>({ labs: [], loading: true, error: null })
  const [selectedLabId, setSelectedLabId] = useState<string | null>(null)
  const [assignState, setAssignState] = useState<{ submitting: boolean; error: string | null }>({
    submitting: false,
    error: null,
  })

  const mountedRef = useRef(true)
  const listGenerationRef = useRef(0)
  const assignGenerationRef = useRef(0)
  const currentProviderRef = useRef(providerId)
  currentProviderRef.current = providerId
  const currentOrderRef = useRef(orderId)
  currentOrderRef.current = orderId

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // Deliberately touches only `labsState`/`selectedLabId`, never `assignState` -- this is also
  // called from the 409-conflict recovery path (§19), which needs the conflict message it just
  // set to stay visible while the list silently refreshes underneath it, not be clobbered by a
  // reset happening in the same synchronous batch.
  const loadEligibleLabs = useCallback(() => {
    const generation = ++listGenerationRef.current
    const targetProvider = providerId
    const targetOrder = orderId
    const controller = new AbortController()

    setLabsState({ labs: [], loading: true, error: null })
    setSelectedLabId(null)

    client
      .getEligibleLabs(providerId, orderId, { signal: controller.signal })
      .then((labs) => {
        if (!mountedRef.current) return
        if (listGenerationRef.current !== generation) return
        if (currentProviderRef.current !== targetProvider) return
        if (currentOrderRef.current !== targetOrder) return
        setLabsState({ labs, loading: false, error: null })
      })
      .catch(() => {
        if (controller.signal.aborted) return
        if (!mountedRef.current) return
        if (listGenerationRef.current !== generation) return
        if (currentProviderRef.current !== targetProvider) return
        if (currentOrderRef.current !== targetOrder) return
        setLabsState({ labs: [], loading: false, error: ELIGIBLE_LABS_LOAD_ERROR })
      })

    return () => controller.abort()
  }, [client, providerId, orderId])

  useEffect(() => {
    return loadEligibleLabs()
  }, [loadEligibleLabs])

  const handleConfirm = () => {
    if (!selectedLabId) return
    const generation = ++assignGenerationRef.current
    const targetProvider = providerId
    const targetOrder = orderId
    const targetLabId = selectedLabId
    const controller = new AbortController()

    setAssignState({ submitting: true, error: null })

    client
      .assignLab(
        providerId,
        orderId,
        { lab_organization_id: targetLabId },
        { signal: controller.signal },
      )
      .then(() =>
        // §30: the assign response itself carries no lab name (only id/selected_at) -- the
        // authoritative, named projection always comes from a fresh order read, never from this
        // response body.
        client.getOrder(targetProvider, targetOrder),
      )
      .then((order) => {
        if (!mountedRef.current) return
        if (assignGenerationRef.current !== generation) return
        if (currentProviderRef.current !== targetProvider) return
        if (currentOrderRef.current !== targetOrder) return
        setAssignState({ submitting: false, error: null })
        if (order.lab_assignment) {
          onAssigned(order.lab_assignment)
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        if (!mountedRef.current) return
        if (assignGenerationRef.current !== generation) return
        if (currentProviderRef.current !== targetProvider) return
        if (currentOrderRef.current !== targetOrder) return
        const isConflict = err instanceof ExchangeApiError && err.status === 409
        setAssignState({
          submitting: false,
          error: isConflict ? ASSIGN_CONFLICT_MESSAGE : ASSIGN_ERROR_FALLBACK,
        })
        if (isConflict) {
          // §19: never trust the stale list after a revalidate-at-commit refusal -- refresh it.
          loadEligibleLabs()
        }
      })
  }

  return (
    <Card>
      <div className={styles.container}>
        <div className={styles.contextBlock}>
          <PatientIdentity patient={patient} variant="full" />
          <div>
            <p className={styles.contextLabel}>Analyses demandées ({items.length})</p>
            <ul className={styles.itemList}>
              {items.map((item) => (
                <li key={item.catalog_entry_id}>
                  <strong>{item.catalog_code_snapshot}</strong> — {item.catalog_display_name_snapshot}
                </li>
              ))}
            </ul>
          </div>
        </div>

        <h3 className={styles.heading}>Choisir un laboratoire destinataire</h3>

        {/* The assignment outcome is independent of whatever the (possibly just-refreshed)
            eligible-labs list currently shows below it -- a 409 conflict message must stay
            visible even once the refreshed list turns out to be empty (§19). */}
        {assignState.error ? (
          <p role="alert" className={styles.errorText}>
            {assignState.error}
          </p>
        ) : null}

        {labsState.loading ? (
          <LoadingSkeleton label="Chargement des laboratoires disponibles…" />
        ) : labsState.error ? (
          <p role="alert" className={styles.errorText}>
            {labsState.error}{' '}
            <Button type="button" variant="secondary" onClick={loadEligibleLabs}>
              Réessayer
            </Button>
          </p>
        ) : labsState.labs.length === 0 ? (
          <p className={styles.emptyText}>{ZERO_ELIGIBLE_MESSAGE}</p>
        ) : (
          <>
            <ul className={styles.labList} role="radiogroup" aria-label="Laboratoires disponibles">
              {labsState.labs.map((lab) => (
                <li key={lab.lab_organization_id}>
                  <label className={styles.labOption}>
                    <input
                      type="radio"
                      name="eligible-lab"
                      value={lab.lab_organization_id}
                      checked={selectedLabId === lab.lab_organization_id}
                      onChange={() => setSelectedLabId(lab.lab_organization_id)}
                      disabled={assignState.submitting}
                    />
                    <span>
                      <strong>{lab.lab_organization_name}</strong>
                      <br />
                      <span className={styles.labMeta}>{lab.public_address}</span>
                      {lab.public_phone ? (
                        <>
                          <br />
                          <span className={styles.labMeta}>{lab.public_phone}</span>
                        </>
                      ) : null}
                    </span>
                  </label>
                </li>
              ))}
            </ul>

            <Button
              type="button"
              onClick={handleConfirm}
              disabled={!selectedLabId || assignState.submitting}
            >
              {assignState.submitting ? 'Envoi en cours…' : "Confirmer l'envoi au laboratoire"}
            </Button>
          </>
        )}
      </div>
    </Card>
  )
}
