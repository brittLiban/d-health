/**
 * DH-NOTIFY-001 §18: the only place a notification state becomes French.
 *
 * It exists as its own module for the same reason `patientStatusWording.ts` does: the distinctions
 * that matter here are exactly the ones a hurried translation collapses. §18 names three that must
 * never be implied:
 *
 *     Envoyé  ≠  lu
 *     Remis   ≠  compris
 *     Remis   ≠  résultat examiné par le médecin
 *
 * So "Envoyé" is never rendered as "Le patient a reçu", and "Remis" is never rendered as
 * "Le patient a vu". The strings below say what DHealth actually knows, which is less than a
 * clinician might want and is the honest amount.
 *
 * **No provider vocabulary reaches a clinical user** (§18). Twilio, Meta, SID, webhook, API and
 * HTTP status codes appear nowhere; a failure becomes an action the clinician can take.
 */

import type {
  NotificationChannel,
  NotificationState,
  PatientNotificationSummary,
} from '../../../api/exchangeTypes'

export function channelLabel(channel: NotificationChannel): string {
  return channel === 'WHATSAPP' ? 'WhatsApp' : 'SMS'
}

/** The state, as a clinician should read it. Deliberately understated. */
export function stateLabel(state: NotificationState): string {
  switch (state) {
    case 'PENDING':
      return 'En attente d’envoi'
    case 'SENDING':
      return 'Envoi en cours'
    case 'SENT':
      // Not "reçu" and not "remis": the operator accepted it, which is all DHealth knows.
      return 'Transmis à l’opérateur'
    case 'DELIVERED':
      // Not "lu". A delivery receipt says a handset accepted it, nothing about a person.
      return 'Remis au téléphone'
    case 'FAILED':
      return 'Échec'
  }
}

/**
 * What the clinician should *do*, per sanitized failure category.
 *
 * Never the provider's error text (§18: "Never render raw provider error JSON"). The backend
 * already refuses to store it; this is the second half of that boundary -- even the category is
 * turned into an action rather than shown as a code.
 */
export function failureGuidance(category: string | null): string {
  switch (category) {
    case 'INVALID_DESTINATION':
      return 'Le numéro ne peut pas recevoir ce message. Vérifiez-le avec le patient, puis révoquez et recréez le suivi avec le bon numéro.'
    case 'PROVIDER_REJECTED':
      return 'L’opérateur a refusé le message. Le patient peut utiliser le QR code imprimé, ou contacter votre établissement.'
    case 'RATE_LIMIT':
    case 'TRANSIENT_PROVIDER':
      return 'Le service de messagerie est momentanément indisponible. Vous pouvez réessayer.'
    case 'AUTHENTICATION':
    case 'CONFIGURATION':
      // A deployment problem, not something the clinician can fix by retrying -- saying "réessayez"
      // here would send them round a loop that cannot succeed.
      return 'L’envoi de messages n’est pas configuré correctement sur cette installation. Signalez-le à votre administrateur ; le QR code et le document imprimé fonctionnent normalement.'
    default:
      return 'L’envoi a échoué. Le QR code et le document imprimé restent utilisables.'
  }
}

/**
 * One line summarising a message for the handoff panel.
 *
 * The access-link message and the update messages are named differently because they *are*
 * different things: one carried the secure link, the others only told the patient to use it.
 */
export function notificationSummaryLine(notification: PatientNotificationSummary): string {
  const kind =
    notification.event_type === 'ACCESS_LINK_ISSUED'
      ? 'Lien sécurisé'
      : 'Mise à jour'
  return `${kind} · ${channelLabel(notification.channel)} · ${stateLabel(notification.state)}`
}
