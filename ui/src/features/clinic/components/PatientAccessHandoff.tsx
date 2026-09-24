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
import type {
  LabOrderItem,
  NotificationChannel,
  PatientAccessMetadata,
  PatientNotificationStatus,
} from '../../../api/exchangeTypes'
import { Button } from '../../../design-system'
import { buildPatientAccessUrl } from '../../patient/patientAccessUrl'
import { PatientAccessQr } from './PatientAccessQr'
import {
  channelLabel,
  failureGuidance,
  notificationSummaryLine,
} from './patientNotificationWording'
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
  /**
   * DH-NOTIFY-001 §18: the "Envoyer au patient" flow.
   *
   * `composing` is the small deliberate form -- destination, channel, consent -- shown instead of
   * the plain "Créer le suivi patient" button when the clinician chooses to send. It is a separate
   * gesture rather than a default, because §19 keeps notification optional: a patient with no
   * phone must still get the QR and the sheet, and the fastest path must not route through a form
   * asking for a number they do not have.
   */
  composing: boolean
  destination: string
  channel: NotificationChannel
  consent: boolean
  notifications: PatientNotificationStatus | null
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
  composing: false,
  destination: '',
  channel: 'SMS',
  consent: false,
  notifications: null,
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

  const readNotifications = useCallback(
    async (key: string, signal?: AbortSignal): Promise<void> => {
      try {
        const notifications = await client.getPatientNotifications(providerId, orderId, { signal })
        applyIfCurrent(key, (previous) => ({ ...previous, notifications }))
      } catch {
        // A failed notifications read must never degrade the access panel: the QR, the printed
        // sheet and revoke all work without it, and DH-NOTIFY-001 §19 makes those the path that
        // must keep working. Left null, the messaging section simply does not render.
        applyIfCurrent(key, (previous) => ({ ...previous, notifications: null }))
      }
    },
    [applyIfCurrent, client, orderId, providerId],
  )

  useEffect(() => {
    const controller = new AbortController()
    const key = `${providerId}::${orderId}`
    setState(INITIAL)
    void readMetadata(key, controller.signal)
    // Independent of the metadata read and deliberately not awaited with it: a deployment with
    // notifications switched off, or an older backend, must leave the access panel fully working.
    void readNotifications(key, controller.signal)
    return () => controller.abort()
  }, [orderId, providerId, readMetadata, readNotifications])

  const handleIssue = useCallback(
    async (notify?: { channel: NotificationChannel; destination: string }) => {
    const key = requestKey
    setState((previous) => ({ ...previous, busy: true, error: null, notice: null }))
    try {
      // Two arguments when nothing is being sent, exactly as before DH-NOTIFY-001. Not cosmetic:
      // §19 keeps the QR/print path the baseline, and the call it makes is unchanged -- which is
      // what `PatientAccessHandoff.test.tsx`'s pre-existing CH2 assertion pins.
      const issued = notify
        ? await client.issuePatientAccess(providerId, orderId, {
            notify: {
              channel: notify.channel,
              destination: notify.destination,
              // Typed `true` in `exchangeTypes.ts`, mirroring the server's `Literal[True]`. The
              // checkbox gates reaching this call at all; the type is what makes an unconsented
              // request unconstructable.
              patient_consent_confirmed: true,
            },
          })
        : await client.issuePatientAccess(providerId, orderId)
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
        composing: false,
        consent: false,
        metadata: {
          active: true,
          expires_at: issued.expires_at,
          access_mode: issued.access_mode,
        },
      }))
      if (notify) {
        // The server sent the link inline and recorded the outcome; read it back rather than
        // assuming it worked. A send failure is terminal (the link cannot be rebuilt), so the
        // clinician has to see it here -- the issuance itself deliberately succeeded regardless.
        await readNotifications(key)
      }
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
      // A 422 is the server refusing the *destination* or the consent field -- a correctable
      // mistake on this screen, not an ambiguous outcome. Its detail is safe to show: it names a
      // reason class ("must include a country code"), never a provider message or a number.
      const rejected = error instanceof ExchangeApiError && error.status === 422
      // A 503 means this deployment cannot send at all. Saying "réessayez" would be a lie.
      const unavailable = error instanceof ExchangeApiError && error.status === 503
      applyIfCurrent(key, (previous) => ({
        ...previous,
        busy: false,
        notice: null,
        error: rejected
          ? (error as ExchangeApiError).detail ||
            'Ce numéro ne peut pas être utilisé. Vérifiez-le et réessayez.'
          : unavailable
            ? "L'envoi de messages n'est pas activé sur cette installation. Créez le suivi sans " +
              'envoi : le QR code et le document imprimé fonctionnent.'
            : conflict || !ambiguous
              ? "La création de l'accès patient a échoué. Réessayez."
              : "La création de l'accès patient n'a pas pu être confirmée. Réessayez.",
      }))
    }
    },
    [applyIfCurrent, client, orderId, providerId, readMetadata, readNotifications, requestKey],
  )

  const handleRetry = useCallback(
    async (notificationId: string) => {
      const key = requestKey
      setState((previous) => ({ ...previous, busy: true, error: null, notice: null }))
      try {
        await client.retryPatientNotification(providerId, orderId, notificationId)
      } catch {
        // Never a blind second retry, and never a claim about what happened: re-read authoritative
        // state below and let it say. Matching §47/§48's treatment of every other ambiguous
        // mutation in this component.
        applyIfCurrent(key, (previous) => ({
          ...previous,
          error: "La relance n'a pas pu être confirmée. Vérifiez l'état ci-dessous.",
        }))
      }
      await readNotifications(key)
      applyIfCurrent(key, (previous) => ({ ...previous, busy: false }))
    },
    [applyIfCurrent, client, orderId, providerId, readNotifications, requestKey],
  )

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
        // Two genuinely different sub-cases, and the wording must not claim more than is known:
        // `metadata` non-null means the server was reached and still reports the grant active;
        // `null` means the re-read itself failed, so whether the revoke committed is simply
        // unknown. Saying "l'accès est toujours actif" in that second case would assert a fact
        // this code does not have.
        error:
          metadata === null
            ? "La révocation n'a pas pu être confirmée et l'état de l'accès n'a pas pu être " +
              'relu. Réessayez.'
            : "La révocation n'a pas pu être confirmée et l'accès est toujours actif. Réessayez.",
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

  /*
   * DH-NOTIFY-META-001 §6: server-derived, never a frontend constant. `null` notifications (the
   * read failed, or an older backend) yields an empty list, which correctly offers nothing rather
   * than guessing that some channel probably works.
   */
  const availableChannels = state.notifications?.available_channels ?? []
  const canSend = availableChannels.length > 0
  /*
   * The selected channel must be one the deployment can send on. Derived rather than stored, so a
   * server that stops supporting a channel cannot leave a stale selection in component state that
   * the submit handler would then send.
   */
  const selectedChannel = availableChannels.includes(state.channel)
    ? state.channel
    : (availableChannels[0] ?? state.channel)

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
      ) : state.composing ? (
        /*
         * DH-NOTIFY-001 §18: a small deliberate flow. Destination, channel, and an explicit
         * confirmation that the patient agreed to be messaged at that number.
         *
         * No Twilio/Meta terminology anywhere (§18) -- a clinical user sees "SMS" and "WhatsApp",
         * which are the things their patient has. And the consent box is unticked by default and
         * gates the send button: §5 forbids inferring consent from registration, from having a
         * number, or from having scanned a QR, and a pre-ticked box would be exactly that
         * inference wearing a checkbox.
         */
        <form
          className={styles.body}
          onSubmit={(event) => {
            event.preventDefault()
            if (!state.consent || !state.destination.trim() || !canSend) return
            void handleIssue({ channel: selectedChannel, destination: state.destination.trim() })
          }}
        >
          <p>Envoyer le lien sécurisé au patient.</p>

          <label className={styles.field}>
            <span>Numéro du patient</span>
            <input
              type="tel"
              name="destination"
              value={state.destination}
              placeholder="+253 77 00 00 00"
              autoComplete="off"
              onChange={(event) =>
                setState((previous) => ({ ...previous, destination: event.target.value }))
              }
            />
          </label>

          {/*
            DH-NOTIFY-META-001 §6/§20: the channels come from the **server**, never from a constant
            here. A hard-coded list is a UI that keeps offering SMS after the provider stops
            supporting it -- and a clinician would then collect a patient's consent for a message
            nothing can deliver.

            With exactly one channel available -- today's state, Meta WhatsApp and no SMS provider
            -- a radio group of one is a control with no choice in it, so the channel is stated
            instead. §20: the clinician sees "WhatsApp", never "Meta" or a Graph API version.
          */}
          {availableChannels.length === 1 ? (
            <p className={styles.field}>Canal : {channelLabel(availableChannels[0])}</p>
          ) : (
            <fieldset className={styles.field}>
              <legend>Canal</legend>
              {availableChannels.map((option) => (
                <label key={option}>
                  <input
                    type="radio"
                    name={`channel-${orderId}`}
                    value={option}
                    checked={selectedChannel === option}
                    onChange={() => setState((previous) => ({ ...previous, channel: option }))}
                  />
                  {channelLabel(option)}
                </label>
              ))}
            </fieldset>
          )}

          <label className={styles.field}>
            <input
              type="checkbox"
              name="consent"
              checked={state.consent}
              onChange={(event) =>
                setState((previous) => ({ ...previous, consent: event.target.checked }))
              }
            />
            <span>Le patient accepte de recevoir une notification à ce numéro.</span>
          </label>

          <div className={styles.actions}>
            <Button
              type="submit"
              disabled={state.busy || !state.consent || !state.destination.trim()}
            >
              Envoyer le lien sécurisé
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() =>
                setState((previous) => ({
                  ...previous,
                  composing: false,
                  consent: false,
                  error: null,
                }))
              }
              disabled={state.busy}
            >
              Annuler
            </Button>
          </div>
        </form>
      ) : !state.metadata?.active ? (
        <>
          <p className={styles.body}>Aucun accès patient actif.</p>
          <div className={styles.actions}>
            {/*
              Two separate actions, and the QR-only one is first. §19: notification is optional,
              and a patient with no phone must reach the printed path without being routed through
              a form asking for a number they do not have.
            */}
            <Button type="button" onClick={() => void handleIssue()} disabled={state.busy}>
              Créer le suivi patient
            </Button>
            {/*
              §19/§6: offered only when the deployment can actually send. Hidden rather than
              disabled -- a greyed-out control invites a clinician to hunt for the reason, and the
              QR-only path beside it is the one that always works.
            */}
            {canSend ? (
              <Button
                type="button"
                variant="secondary"
                onClick={() => setState((previous) => ({ ...previous, composing: true }))}
                disabled={state.busy}
              >
                Créer et envoyer au patient
              </Button>
            ) : null}
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

      {/*
        DH-NOTIFY-001 §18: what was actually sent, in DHealth's own understated vocabulary.

        `patientNotificationWording.ts` is the only place a state becomes French, precisely so that
        "Transmis à l'opérateur" never drifts into "reçu" and "Remis au téléphone" never drifts
        into "lu". A failure shows an action, never a provider error.
      */}
      {state.notifications && state.notifications.notifications.length > 0 ? (
        <div className={styles.body}>
          <p className={styles.sheetLabel}>Messages envoyés</p>
          <ul className={styles.sheetList}>
            {state.notifications.notifications.map((notification) => (
              <li key={notification.id}>
                {notificationSummaryLine(notification)}
                {notification.state === 'FAILED' ? (
                  <>
                    {' — '}
                    {failureGuidance(notification.failure_category)}
                    {notification.retryable ? (
                      <Button
                        type="button"
                        variant="secondary"
                        disabled={state.busy}
                        onClick={() => void handleRetry(notification.id)}
                      >
                        Réessayer
                      </Button>
                    ) : null}
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

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
