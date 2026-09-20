/**
 * DH-PATIENT-001 §11/§12/§15-§19/§47-§49/§57: "Suivi patient" -- the clinician's control for
 * creating, revoking, and rotating a patient's access to their own order status, plus the
 * printable/QR handoff that control produces.
 *
 * **The one hard fact this UI is built around.** The raw bearer secret exists exactly once, in
 * the issuance response, and the Exchange cannot recover it afterward (§10). So this component
 * has two genuinely different "a link is active" states and must never conflate them:
 *
 *   active + we still hold the secret   -> show the QR, the copy action, and the printable sheet
 *   active + we do not hold the secret  -> say so plainly and offer revoke-and-recreate
 *
 * The second state is reached by a page reload, by navigating away and back, and by §47's
 * lost-issuance-response case. Pretending the QR could be reconstructed there would be a lie the
 * patient would discover at the laboratory door.
 *
 * **Never a blind mutation retry (§19/§47/§48).** Every ambiguous outcome re-reads authoritative
 * metadata and acts on what the server actually says. A second issuance is never fired
 * speculatively: it would either fail (one live grant per order is a database constraint) or, if
 * it somehow succeeded, silently invalidate a link already in the patient's hands.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  isAmbiguousExchangeError,
  type ExchangeClient,
} from '../../../api/exchangeClient'
import { ExchangeApiError } from '../../../api/exchangeClient'
import type { LabOrderItem, PatientAccessMetadata } from '../../../api/exchangeTypes'
import { Button } from '../../../design-system'
import { buildPatientAccessUrl } from '../../patient/patientAccessUrl'
import { PatientAccessQr } from './PatientAccessQr'
import styles from './PatientAccessHandoff.module.css'

export interface PatientAccessHandoffProps {
  client: ExchangeClient
  providerId: string
  orderId: string
  /** Display name of the ordering provider, printed on the sheet (§15). */
  providerName: string
  /** The ordered tests, printed by human display name only -- never by catalog code (§35). */
  items: LabOrderItem[]
  /** Assigned laboratory name, or `null` when the order has no lab yet (§7 forbids issuing then). */
  labName: string | null
}

type Phase = 'loading' | 'ready' | 'metadata_error'

interface HandoffState {
  phase: Phase
  metadata: PatientAccessMetadata | null
  /**
   * The raw bearer secret, in React memory only, for as long as this component is mounted and not
   * a moment longer. Never persisted, never logged, never rendered as text (§23/§43, AC6/CH7).
   */
  rawToken: string | null
  expiresAt: string | null
  busy: boolean
  error: string | null
  /** §47/§48: a truthful statement about an outcome the server could not confirm. */
  notice: string | null
  confirmingRevoke: boolean
}

const INITIAL: HandoffState = {
  phase: 'loading',
  metadata: null,
  rawToken: null,
  expiresAt: null,
  busy: false,
  error: null,
  notice: null,
  confirmingRevoke: false,
}

function formatExpiry(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('fr-FR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function PatientAccessHandoff({
  client,
  providerId,
  orderId,
  providerName,
  items,
  labName,
}: PatientAccessHandoffProps) {
  const [state, setState] = useState<HandoffState>(INITIAL)

  /*
   * §49: a late response must never render under a different patient/order. `requestKey` carries
   * the exact (provider, order) a request was issued for; every state write checks it against the
   * pair currently mounted, matching the identity-check pattern `ClinicOrdersPage` already uses
   * for its own routing panel. `key={orderId}` at the call site remounts this component on an
   * order change, and this guard is the belt to that suspenders.
   */
  const requestKey = `${providerId}::${orderId}`
  const activeKeyRef = useRef(requestKey)
  useEffect(() => {
    activeKeyRef.current = requestKey
  }, [requestKey])

  const applyIfCurrent = useCallback(
    (key: string, update: (previous: HandoffState) => HandoffState) => {
      if (activeKeyRef.current !== key) return
      setState(update)
    },
    [],
  )

  const readMetadata = useCallback(
    async (key: string, signal?: AbortSignal): Promise<PatientAccessMetadata | null> => {
      try {
        const metadata = await client.getPatientAccessMetadata(providerId, orderId, { signal })
        applyIfCurrent(key, (previous) => ({
          ...previous,
          phase: 'ready',
          metadata,
          // A grant the server now reports inactive cannot have a usable secret in memory either.
          rawToken: metadata.active ? previous.rawToken : null,
          expiresAt: metadata.active ? metadata.expires_at : null,
        }))
        return metadata
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return null
        applyIfCurrent(key, (previous) => ({ ...previous, phase: 'metadata_error' }))
        return null
      }
    },
    [applyIfCurrent, client, orderId, providerId],
  )

  useEffect(() => {
    const controller = new AbortController()
    const key = `${providerId}::${orderId}`
    setState(INITIAL)
    void readMetadata(key, controller.signal)
    return () => controller.abort()
  }, [orderId, providerId, readMetadata])

  const handleIssue = useCallback(async () => {
    const key = requestKey
    setState((previous) => ({ ...previous, busy: true, error: null, notice: null }))
    try {
      const issued = await client.issuePatientAccess(providerId, orderId)
      if (issued.access_mode !== 'ORDER_READ') {
        /*
         * §12: fail closed. An unexpected `LAB_SELECTION` grant means the order had no laboratory
         * when the grant was created -- a marketplace-capable credential this launch UI has no
         * surface for, and must not hand to a patient as though it were a status link. The
         * clinician is told plainly; no marketplace fallback UI is built here (§6).
         */
        applyIfCurrent(key, (previous) => ({
          ...previous,
          busy: false,
          rawToken: null,
          error:
            "Cet accès autorise le choix du laboratoire, ce qui n'est pas prévu ici. Révoquez-le " +
            'et attribuez un laboratoire avant de créer le suivi patient.',
        }))
        await readMetadata(key)
        return
      }
      applyIfCurrent(key, (previous) => ({
        ...previous,
        busy: false,
        rawToken: issued.access_token,
        expiresAt: issued.expires_at,
        metadata: {
          active: true,
          expires_at: issued.expires_at,
          access_mode: issued.access_mode,
        },
      }))
    } catch (error) {
      /*
       * §47: the response was lost, or the grant already existed. Either way, re-read
       * authoritative metadata rather than guessing, and never issue a second time blindly -- a
       * live grant may already be in the patient's possession.
       */
      const conflict = error instanceof ExchangeApiError && error.status === 409
      const ambiguous = isAmbiguousExchangeError(error)
      const metadata = await readMetadata(key)

      if (metadata?.active) {
        applyIfCurrent(key, (previous) => ({
          ...previous,
          busy: false,
          rawToken: null,
          error: null,
          notice:
            "Un accès a été créé, mais le lien n'a pas pu être récupéré. Révoquez-le et créez-en " +
            'un nouveau.',
        }))
        return
      }
      applyIfCurrent(key, (previous) => ({
        ...previous,
        busy: false,
        notice: null,
        error:
          conflict || !ambiguous
            ? "La création de l'accès patient a échoué. Réessayez."
            : "La création de l'accès patient n'a pas pu être confirmée. Réessayez.",
      }))
    }
  }, [applyIfCurrent, client, orderId, providerId, readMetadata, requestKey])

  const performRevoke = useCallback(async (): Promise<boolean> => {
    const key = requestKey
    setState((previous) => ({ ...previous, busy: true, error: null, notice: null }))
    try {
      await client.revokePatientAccess(providerId, orderId)
      const metadata = await readMetadata(key)
      applyIfCurrent(key, (previous) => ({
        ...previous,
        busy: false,
        rawToken: null,
        confirmingRevoke: false,
      }))
      return metadata ? !metadata.active : true
    } catch {
      /*
       * §48: an ambiguous revoke is resolved by re-reading metadata, never by a blind retry. If
       * the grant is now inactive the revoke did commit and this is a success; if it is still
       * active the clinician is offered an explicit retry rather than having one fired for them.
       */
      const metadata = await readMetadata(key)
      if (metadata && !metadata.active) {
        applyIfCurrent(key, (previous) => ({
          ...previous,
          busy: false,
          rawToken: null,
          confirmingRevoke: false,
        }))
        return true
      }
      applyIfCurrent(key, (previous) => ({
        ...previous,
        busy: false,
        confirmingRevoke: false,
        error:
          "La révocation n'a pas pu être confirmée et l'accès est toujours actif. Réessayez.",
      }))
      return false
    }
  }, [applyIfCurrent, client, orderId, providerId, readMetadata, requestKey])

  const handleRotate = useCallback(async () => {
    // §19: revoke -> confirm authoritative state -> only then issue. Never revoke-and-issue as
    // one optimistic pair.
    const revoked = await performRevoke()
    if (!revoked) return
    await handleIssue()
  }, [handleIssue, performRevoke])

  const handleCopy = useCallback(async () => {
    const key = requestKey
    if (!state.rawToken || typeof window === 'undefined') return
    const url = buildPatientAccessUrl(window.location.origin, state.rawToken)
    try {
      // Explicitly, not `navigator.clipboard?.writeText(url)`: optional chaining on an absent
      // Clipboard API yields `undefined`, which awaits successfully -- so the clinician would be
      // told "Lien copié." with nothing on the clipboard. A browser without the API (or with it
      // withheld on an insecure origin) must reach the failure branch below, which points at the
      // QR and the printed sheet -- both of which still work.
      if (!navigator.clipboard?.writeText) {
        throw new Error('clipboard unavailable')
      }
      // §18: the full fragment URL, never the bare token as a separate visible value.
      await navigator.clipboard.writeText(url)
      applyIfCurrent(key, (previous) => ({ ...previous, notice: 'Lien copié.', error: null }))
    } catch {
      applyIfCurrent(key, (previous) => ({
        ...previous,
        error: "Copie impossible sur cet appareil. Utilisez le QR ou l'impression.",
      }))
    }
  }, [applyIfCurrent, requestKey, state.rawToken])

  // §7: no patient handoff before a laboratory exists -- the resulting grant would be
  // LAB_SELECTION (marketplace-capable), which this launch flow does not hand to patients.
  if (!labName) {
    return (
      <section className={styles.panel} aria-labelledby={`suivi-${orderId}`}>
        <h3 className={styles.heading} id={`suivi-${orderId}`}>
          Suivi patient
        </h3>
        <p className={styles.body}>
          Attribuez d’abord un laboratoire à cet ordre pour pouvoir créer le suivi patient.
        </p>
      </section>
    )
  }

  const accessUrl =
    state.rawToken && typeof window !== 'undefined'
      ? buildPatientAccessUrl(window.location.origin, state.rawToken)
      : null

  return (
    <section className={styles.panel} aria-labelledby={`suivi-${orderId}`}>
      <h3 className={styles.heading} id={`suivi-${orderId}`}>
        Suivi patient
      </h3>

      {state.phase === 'loading' ? (
        <p className={styles.body}>Chargement de l’accès patient…</p>
      ) : state.phase === 'metadata_error' ? (
        <>
          <p className={styles.body}>
            L’état de l’accès patient n’a pas pu être chargé.
          </p>
          <div className={styles.actions}>
            <Button type="button" variant="secondary" onClick={() => void readMetadata(requestKey)}>
              Réessayer
            </Button>
          </div>
        </>
      ) : state.confirmingRevoke ? (
        // §19: explicit confirmation, stating the real consequence before it happens.
        <>
          <p className={styles.body}>
            Révoquer cet accès patient ? Le lien ou QR actuel cessera immédiatement de fonctionner.
          </p>
          <div className={styles.actions}>
            <Button type="button" onClick={() => void performRevoke()} disabled={state.busy}>
              Révoquer
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() =>
                setState((previous) => ({ ...previous, confirmingRevoke: false }))
              }
              disabled={state.busy}
            >
              Annuler
            </Button>
          </div>
        </>
      ) : !state.metadata?.active ? (
        <>
          <p className={styles.body}>Aucun accès patient actif.</p>
          <div className={styles.actions}>
            <Button type="button" onClick={() => void handleIssue()} disabled={state.busy}>
              Créer le suivi patient
            </Button>
          </div>
        </>
      ) : !state.rawToken ? (
        // Active, but the secret is not in this browser's memory -- the honest state (§11).
        <>
          <p className={styles.body}>
            Un accès patient est déjà actif
            {state.metadata.expires_at ? ` jusqu’au ${formatExpiry(state.metadata.expires_at)}` : ''}.
            Pour créer un nouveau document, révoquez l’accès existant.
          </p>
          <div className={styles.actions}>
            <Button type="button" onClick={() => void handleRotate()} disabled={state.busy}>
              Révoquer et recréer
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setState((previous) => ({ ...previous, confirmingRevoke: true }))}
              disabled={state.busy}
            >
              Révoquer
            </Button>
          </div>
        </>
      ) : (
        <>
          <div className={styles.actions}>
            <Button type="button" variant="secondary" onClick={() => void handleCopy()}>
              Copier le lien
            </Button>
            <Button type="button" variant="secondary" onClick={() => window.print()}>
              Imprimer le document patient
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setState((previous) => ({ ...previous, confirmingRevoke: true }))}
              disabled={state.busy}
            >
              Révoquer
            </Button>
          </div>

          {/*
            §15/§16/§17: the printable sheet. `data-print-root` is what `index.css`'s print block
            isolates, so printing yields this sheet alone -- no Clinic sidebar, no navigation, no
            buttons.

            What is on it: DHealth, the provider, the requested tests by display name, the
            assigned laboratory, the QR, the expiry, and the assisted-care instruction. What is
            deliberately NOT on it: any result value, any diagnosis or interpretation, any
            internal UUID, the patient's demographic block, and the bearer token as visible text.
            Patient identity stays on the clinician's screen (this panel sits inside an order row
            that already shows it), not on the paper the patient carries away (§16).
          */}
          <div className={styles.sheet} data-print-root>
            <p className={styles.sheetBrand}>DHealth</p>
            <h4 className={styles.sheetTitle}>Suivi de votre analyse</h4>
            <p>{providerName}</p>

            <div className={styles.sheetSection}>
              <p className={styles.sheetLabel}>Analyse(s)</p>
              <ul className={styles.sheetList}>
                {items.map((item, index) => (
                  <li key={`${item.catalog_code_snapshot}-${index}`}>
                    {item.catalog_display_name_snapshot}
                  </li>
                ))}
              </ul>
            </div>

            <div className={styles.sheetSection}>
              <p className={styles.sheetLabel}>Laboratoire</p>
              <p>{labName}</p>
            </div>

            <div className={styles.qrBlock}>
              <p>Scannez ce code pour voir l’état de votre analyse.</p>
              {accessUrl ? <PatientAccessQr url={accessUrl} className={styles.qr} /> : null}
            </div>

            {state.expiresAt ? (
              <div className={styles.sheetSection}>
                <p className={styles.sheetLabel}>Accès valable jusqu’au</p>
                <p>{formatExpiry(state.expiresAt)}</p>
              </div>
            ) : null}

            <p className={styles.assistedNote}>
              Si vous n’utilisez pas de téléphone, présentez ce document à votre clinique.
            </p>
          </div>
        </>
      )}

      {state.notice ? (
        <p className={styles.notice} role="status">
          {state.notice}
        </p>
      ) : null}
      {state.error ? (
        <p className={styles.error} role="alert">
          {state.error}
        </p>
      ) : null}
    </section>
  )
}
