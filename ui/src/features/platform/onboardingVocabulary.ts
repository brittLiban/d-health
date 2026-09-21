/**
 * DH-MVP-ONBOARD-001 — what the onboarding domain is called on screen.
 *
 * Every enum this module translates is a real backend value, and none of them is
 * fit to put in front of an operator. The same discipline DH-MVP-POLISH-001
 * applied to `LAB_RESULT_RELEASER` and `Version 2 — Amendée` applies here:
 * `AWAITING_CONNECTOR` is a database fact, "En attente du connecteur" is what a
 * person reads.
 *
 * Values are never translated back. Nothing here is parsed, compared, or sent to
 * the server — the enum stays the identity throughout, and this is presentation
 * at the very edge.
 *
 * An unmapped value falls back to itself rather than to a friendly guess: a code
 * on screen is a visible prompt to name it, whereas inventing a label for
 * something this build does not understand would state something untrue about a
 * facility.
 */

import type {
  EnrollmentBlockerCode,
  HostingModel,
  IntegrationFeasibility,
  InterfaceType,
  InterfaceVerificationState,
  OnboardingStatus,
  ParticipationMode,
} from '../../api/exchangeTypes'

const PARTICIPATION_MODE: Record<ParticipationMode, string> = {
  NATIVE: 'DHealth',
  CONNECT: 'Connect',
}

/** The longer form, for a screen that is explaining the choice rather than listing it. */
const PARTICIPATION_MODE_DESCRIPTION: Record<ParticipationMode, string> = {
  NATIVE: 'Le personnel utilisera directement DHealth pour ce flux diagnostique.',
  CONNECT:
    "L'établissement conserve son logiciel actuel. DHealth préparera son intégration au réseau.",
}

const ONBOARDING_STATUS: Record<OnboardingStatus, string> = {
  DRAFT: 'Dossier ouvert',
  FACILITY_VERIFIED: 'Établissement vérifié',
  ORGANIZATION_READY: 'Établissement créé',
  CONFIGURATION_IN_PROGRESS: 'Configuration en cours',
  TECHNICAL_DISCOVERY: 'Découverte technique',
  AWAITING_CONNECTOR: 'En attente du connecteur',
  READY_FOR_QUALIFICATION: 'Prêt pour la qualification',
  READY_FOR_ROUTING: 'Prêt pour le routage',
  BLOCKED: 'Bloqué',
}

const FEASIBILITY: Record<IntegrationFeasibility, string> = {
  DISCOVERY_REQUIRED: 'Découverte requise',
  CONNECTABLE: 'Interfaces apparemment suffisantes',
  CONNECTOR_DEVELOPMENT_REQUIRED: 'Développement de connecteur requis',
  NOT_SAFELY_INTEGRABLE: 'Intégration sûre impossible actuellement',
}

const HOSTING: Record<HostingModel, string> = {
  ON_PREMISE: "Serveur dans l'établissement",
  CLOUD: 'Cloud',
  HYBRID: 'Hybride',
  UNKNOWN: 'Je ne sais pas',
}

const INTERFACE: Record<InterfaceType, string> = {
  REST_API: 'API REST',
  FHIR: 'FHIR',
  HL7_V2: 'HL7 v2',
  WEBHOOK: 'Webhooks',
  SFTP: 'SFTP',
  FILE_IMPORT_EXPORT: 'Import/export de fichiers',
  DATABASE_READ: 'Lecture base de données',
  OTHER: 'Autre',
  UNKNOWN: 'Je ne sais pas',
}

/**
 * The three truth levels, worded so they cannot be misread as each other.
 *
 * "Signalé par le partenaire" is somebody's claim. "Documentation reçue" is paper
 * in hand. "Interface vérifiée" is DHealth having checked the interface exists as
 * documented — and none of the three says a connector exists, which is why none
 * of these strings contains the word "connecté".
 */
const VERIFICATION: Record<InterfaceVerificationState, string> = {
  REPORTED: 'Signalé par le partenaire',
  DOCUMENTED: 'Documentation reçue',
  VERIFIED: 'Interface vérifiée',
  UNSUPPORTED: 'Non prise en charge',
}

const BLOCKER: Record<EnrollmentBlockerCode, string> = {
  VENDOR_DOCUMENTATION_UNAVAILABLE: "Documentation de l'éditeur requise",
  VENDOR_CONTACT_REQUIRED: "Contact éditeur requis",
  REQUIRED_INTERFACE_UNAVAILABLE: 'Aucune interface utilisable disponible',
  MISSING_RESULT_RELEASE_EVENT: 'Événement de libération des résultats absent',
  UNKNOWN_PATIENT_IDENTIFIER_BEHAVIOR: "Comportement des identifiants patient inconnu",
  CONNECTOR_NOT_IMPLEMENTED: 'Connecteur DHealth non disponible',
  FACILITY_CONFIGURATION_INCOMPLETE: "Configuration de l'établissement incomplète",
  CLINICAL_TEMPLATE_SIGNOFF_REQUIRED: 'Validation clinique des structures requise',
}

/**
 * Readiness findings, as sentences an operator can act on.
 *
 * These codes come from the server's own evaluation and are the answer to "what
 * is preventing go-live". A finding this map does not know still renders — its
 * code, plainly — because silently dropping a blocking reason would leave an
 * operator staring at a refusal with no explanation.
 */
const READINESS_FINDING: Record<string, string> = {
  organization_exists: "L'établissement existe",
  facility_enrolled: "Dossier d'adhésion créé",
  organization_active: 'Établissement actif',
  enrollment_not_blocked: "Aucun blocage d'adhésion",
  administrator_present: 'Responsable désigné',
  clinician_present: 'Clinicien actif',
  lab_technician_present: "Personnel d'analyse actif",
  lab_releaser_present: 'Autorité de libération active',
  lab_accepting_orders: 'Laboratoire ouvert aux ordres',
  lab_has_active_offering: 'Analyses actives proposées',
  lab_result_templates_active: 'Structures de résultat actives',
  connector_qualified: 'Connecteur DHealth qualifié',
}

/** Readiness codes arrive prefixed by side when a route pair is evaluated. */
function stripSide(code: string): string {
  if (code.startsWith('provider_')) return code.slice('provider_'.length)
  if (code.startsWith('lab_lab_')) return code.slice('lab_'.length)
  if (code.startsWith('lab_') && code in READINESS_FINDING) return code
  if (code.startsWith('lab_')) return code.slice('lab_'.length)
  return code
}

function lookup<T extends string>(map: Record<string, string>, value: T | null): string {
  if (value === null) return 'Non renseigné'
  return map[value] ?? value
}

export function participationModeLabel(mode: ParticipationMode | null): string {
  return lookup(PARTICIPATION_MODE, mode)
}

export function participationModeDescription(mode: ParticipationMode): string {
  return PARTICIPATION_MODE_DESCRIPTION[mode]
}

export function onboardingStatusLabel(status: OnboardingStatus | null): string {
  return lookup(ONBOARDING_STATUS, status)
}

export function feasibilityLabel(value: IntegrationFeasibility | null): string {
  return lookup(FEASIBILITY, value)
}

export function hostingModelLabel(value: HostingModel | null): string {
  return lookup(HOSTING, value)
}

export function interfaceTypeLabel(value: InterfaceType): string {
  return lookup(INTERFACE, value)
}

export function verificationStateLabel(value: InterfaceVerificationState): string {
  return lookup(VERIFICATION, value)
}

export function blockerLabel(value: EnrollmentBlockerCode): string {
  return lookup(BLOCKER, value)
}

export function readinessFindingLabel(code: string): string {
  return READINESS_FINDING[stripSide(code)] ?? code
}

/**
 * The organization type, in the words the rest of this product already uses.
 * `PROVIDER`/`LAB` never reach a screen.
 */
export function organizationTypeLabel(organizationType: string): string {
  if (organizationType === 'PROVIDER') return 'Établissement de soins'
  if (organizationType === 'LAB') return "Laboratoire d'analyses"
  return 'Établissement'
}

/**
 * Which role names a facility of this type can grant, in human words.
 * Administration never implies an operational authority — that separation is the
 * backend's, and the wording must not blur it.
 */
export const ROLE_CHOICES_BY_TYPE: Record<string, { role: string; label: string }[]> = {
  PROVIDER: [
    { role: 'ORG_ADMIN', label: 'Administration' },
    { role: 'CLINICIAN', label: 'Clinicien' },
  ],
  LAB: [
    { role: 'ORG_ADMIN', label: 'Administration' },
    { role: 'LAB_TECH', label: 'Analyse' },
    { role: 'LAB_RESULT_RELEASER', label: 'Libération' },
  ],
}
