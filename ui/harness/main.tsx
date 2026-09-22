/**
 * DH-MVP-POLISH-001 — a visual-review harness, not part of the application.
 *
 * This ticket delivered a design and then proved it only under jsdom, which has
 * no layout, no cascade, and no fonts. A reviewer caught a defect no source scan
 * could have caught (the authorization enum sitting in the masthead), which is
 * the argument for being able to *look* at the thing.
 *
 * So: the real components, the real stylesheets, the real self-hosted fonts, in a
 * real browser — with stub data instead of a live Exchange, because the point is
 * appearance rather than behaviour.
 *
 * Its own entry, so nothing here reaches the shipped bundle: `npm run build` only
 * builds `index.html`, and this file is never imported by `src/`. To use it, run
 * `npx vite` and open `/harness/index.html?scene=<name>`; with no `scene` it lists
 * the ones that exist.
 *
 * It authenticates against nothing and talks to nothing. Every name and value
 * below is synthetic.
 */

import type { ReactElement } from 'react'
import { createRoot } from 'react-dom/client'
import type { ExchangeClient } from '../src/api/exchangeClient'
import type {
  FacilityConnector,
  ExchangeMembershipSummary,
  ExchangeMeResponse,
  FacilityEnrollment,
  FacilityReadiness,
  OrganizationMembership,
  ProviderLabRoute,
  PatientOrder,
} from '../src/api/exchangeTypes'
import { PatientStatusPage } from '../src/features/patient/PatientStatusPage'
import { StaffWorkspace } from '../src/features/staff/StaffWorkspace'
import { StaffShell } from '../src/features/staff/StaffShell'
import { WorkflowRelay } from '../src/features/staff/WorkflowRelay'
import { providerRelayNodes } from '../src/features/staff/relayNodes'
import { availableSections } from '../src/features/staff/staffSections'
import { WorkspaceProvider } from '../src/features/workspace/WorkspaceProvider'
import { capabilitiesFor } from '../src/features/workspace/workspaceCapabilities'
import '../src/fonts.ts'
import '../src/index.css'

function membership(
  organizationType: 'PROVIDER' | 'LAB',
  organizationName: string,
  roles: string[],
): ExchangeMembershipSummary {
  return {
    organization_id: organizationType === 'PROVIDER' ? 'prov-1' : 'lab-1',
    organization_name: organizationName,
    organization_type: organizationType,
    organization_status: 'ACTIVE',
    membership_status: 'ACTIVE',
    roles,
  }
}

function me(memberships: ExchangeMembershipSummary[], displayName: string): ExchangeMeResponse {
  return {
    identity_id: 'identity-1',
    issuer: 'harness',
    subject: 'harness',
    display_name: displayName,
    email: null,
    platform_admin: false,
    memberships,
  }
}

/** Only `getMe` is needed: `StaffShell` reads the workspace and nothing else. */
function stubClient(response: ExchangeMeResponse): ExchangeClient {
  return { getMe: () => Promise.resolve(response) } as unknown as ExchangeClient
}

function ShellScene({ response }: { response: ExchangeMeResponse }) {
  const active = response.memberships[0]
  const sections = availableSections(active, capabilitiesFor(active))
  return (
    <WorkspaceProvider client={stubClient(response)}>
      <StaffShell
        sections={sections}
        activeSection={sections[0]?.id ?? null}
        onSelectSection={() => {}}
        onLogout={() => {}}
      >
        <div style={{ padding: 'var(--space-5)', maxWidth: 'var(--measure-focus)' }}>
          <h1>Aujourd'hui</h1>
          <p style={{ color: 'var(--color-text-muted)' }}>
            Contenu de démonstration. Cette page existe pour inspecter le cadre, la typographie et
            les couleurs — pas pour exercer un comportement.
          </p>
        </div>
      </StaffShell>
    </WorkspaceProvider>
  )
}

const RELAY_ORDER = {
  status: 'COMPLETED' as const,
  created_at: '2026-09-18T07:05:00Z',
  lab_assignment: {
    lab_organization_id: 'lab-1',
    lab_organization_name: 'Laboratoire Central',
    selected_at: '2026-09-18T07:20:00Z',
  },
}

function RelayScene({ amended }: { amended: boolean }) {
  const nodes = providerRelayNodes({
    order: RELAY_ORDER,
    providerOrganizationName: 'Clinique de la Paix',
    releaseVersions: [
      {
        release_version_number: amended ? 2 : 1,
        released_at: '2026-09-18T11:00:00Z',
        review_status: 'PENDING',
        reviewed_at: null,
      },
    ],
  })
  return (
    <div style={{ padding: 'var(--space-6)', background: 'var(--color-bg)', minHeight: '100vh' }}>
      <div
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-md)',
          padding: 'var(--space-5)',
          maxWidth: '40rem',
        }}
      >
        <h2 style={{ marginTop: 0 }}>Parcours de l'ordre</h2>
        <WorkflowRelay nodes={nodes} label="Étapes de l'ordre" />
      </div>
    </div>
  )
}

const PATIENT_ORDER: PatientOrder = {
  status: 'COMPLETED',
  result_status: 'FOLLOW_UP_REQUIRED',
  ordering_provider_name: 'Clinique de la Paix',
  requested_tests: [
    { catalog_code_snapshot: 'GLU', catalog_display_name_snapshot: 'Glycémie à jeun' },
    { catalog_code_snapshot: 'CBC', catalog_display_name_snapshot: 'Numération formule sanguine' },
  ],
  selection: {
    lab_organization_name: 'Laboratoire Central',
    public_address: 'Avenue 13, Djibouti-Ville',
    public_phone: '+253 21 00 00 00',
  },
} as unknown as PatientOrder

// ── DH-MVP-ONBOARD-001: the platform control plane ───────────────────────────────────────────
//
// Network administration is desktop and tablet work, so these scenes are captured at 1280 and
// 768 rather than at phone width. They mount the real components against stub data: the point is
// what an operator sees, and the readiness verdicts below are the shapes the server actually
// returns rather than anything this harness decides.

function platformMe(memberships: ExchangeMembershipSummary[] = []): ExchangeMeResponse {
  return {
    identity_id: 'identity-platform',
    issuer: 'harness',
    subject: 'harness',
    display_name: 'Opérateur Plateforme',
    email: null,
    platform_admin: true,
    memberships,
  }
}

function facilityFixture(overrides: Partial<FacilityEnrollment> = {}): FacilityEnrollment {
  return {
    organization_id: 'org-1',
    organization_name: 'Laboratoire Central',
    organization_type: 'LAB',
    organization_status: 'ACTIVE',
    participation_mode: 'CONNECT',
    onboarding_status: 'AWAITING_CONNECTOR',
    integration_feasibility: 'CONNECTOR_DEVELOPMENT_REQUIRED',
    existing_system_name: 'XYZ LIS',
    existing_system_vendor: 'ABC Systems',
    existing_system_version: '8.4',
    hosting_model: 'ON_PREMISE',
    technical_contact: 'Ahmed Osman',
    vendor_contact: 'support@abc-systems.example',
    documentation_reference: null,
    patient_identifier_namespace: null,
    order_identifier_concept: null,
    report_identifier_concept: null,
    specimen_identifier_concept: null,
    exposes_source_event_timestamps: null,
    exposes_professional_release_event: null,
    notes: null,
    interfaces: [
      { interface_type: 'FHIR', verification_state: 'VERIFIED', detail: null },
      { interface_type: 'HL7_V2', verification_state: 'REPORTED', detail: null },
    ],
    open_blockers: [],
    ...overrides,
  }
}

const NETWORK_FIXTURE: FacilityEnrollment[] = [
  facilityFixture({
    organization_id: 'org-native-provider',
    organization_name: 'Clinique Al Rahma',
    organization_type: 'PROVIDER',
    participation_mode: 'NATIVE',
    onboarding_status: 'READY_FOR_ROUTING',
    integration_feasibility: null,
    existing_system_name: null,
    existing_system_vendor: null,
    existing_system_version: null,
    hosting_model: null,
    technical_contact: null,
    vendor_contact: null,
    interfaces: [],
  }),
  facilityFixture(),
  facilityFixture({
    organization_id: 'org-discovery',
    organization_name: 'Clinique Salaam',
    organization_type: 'PROVIDER',
    onboarding_status: 'TECHNICAL_DISCOVERY',
    integration_feasibility: 'DISCOVERY_REQUIRED',
    existing_system_name: null,
    existing_system_vendor: null,
    hosting_model: 'UNKNOWN',
    interfaces: [{ interface_type: 'UNKNOWN', verification_state: 'REPORTED', detail: null }],
    open_blockers: [
      {
        id: 'blk-1',
        code: 'VENDOR_DOCUMENTATION_UNAVAILABLE',
        note: "en attente de la documentation de l'éditeur",
        created_at: '2026-09-20T09:00:00Z',
      },
    ],
  }),
]

const CONNECT_READINESS: FacilityReadiness = {
  organization_id: 'org-1',
  participation_mode: 'CONNECT',
  onboarding_status: 'AWAITING_CONNECTOR',
  production_routing_allowed: false,
  findings: [
    { code: 'organization_active', satisfied: true, blocking: true, detail: null },
    { code: 'enrollment_not_blocked', satisfied: true, blocking: true, detail: null },
    { code: 'lab_accepting_orders', satisfied: true, blocking: true, detail: null },
    { code: 'lab_has_active_offering', satisfied: true, blocking: true, detail: '3 analyse(s) active(s)' },
    // Both, always, for a CONNECT facility -- that is what the server emits now,
    // and a fixture carrying only the first would be a screenshot of a readiness
    // panel the product no longer produces.
    { code: 'connector_qualified', satisfied: false, blocking: true, detail: "aucun connecteur n'est enregistré pour cet établissement" },
    {
      code: 'connector_operational',
      satisfied: false,
      blocking: true,
      detail: "aucune exécution de connecteur ne remonte d'état ; le transport DHealth Connect n'est pas construit",
    },
    { code: 'administrator_present', satisfied: true, blocking: false, detail: null },
  ],
}

/**
 * The same CONNECT readiness with the qualification finding's reason swapped.
 *
 * The server derives that sentence from the same rows the connector panel reads,
 * so a scene whose readiness says one thing while the panel below says another
 * would be a picture of a state the product cannot produce -- which is precisely
 * what a capture is for catching.
 */
function connectReadinessWithReason(detail: string): FacilityReadiness {
  return {
    ...CONNECT_READINESS,
    findings: CONNECT_READINESS.findings.map((finding) =>
      finding.code === 'connector_qualified' ? { ...finding, detail } : finding,
    ),
  }
}

/** A connector build is registered here and has never been qualified. */
const CONNECT_REGISTERED_READINESS = connectReadinessWithReason(
  "le connecteur enregistré n'a pas encore été qualifié",
)

/** Registered, qualified once, and the qualification no longer applies. */
const CONNECT_STALE_READINESS = connectReadinessWithReason(
  'la configuration ou la correspondance des analyses a changé depuis la dernière qualification',
)

/** A Native lab that has lost its release authority: configured, and not currently usable. */
const NATIVE_BLOCKED_READINESS: FacilityReadiness = {
  organization_id: 'org-native-lab',
  participation_mode: 'NATIVE',
  onboarding_status: 'ORGANIZATION_READY',
  production_routing_allowed: false,
  findings: [
    { code: 'organization_active', satisfied: true, blocking: true, detail: null },
    { code: 'enrollment_not_blocked', satisfied: true, blocking: true, detail: null },
    { code: 'lab_accepting_orders', satisfied: true, blocking: true, detail: null },
    { code: 'lab_has_active_offering', satisfied: true, blocking: true, detail: '2 analyse(s) active(s)' },
    { code: 'lab_technician_present', satisfied: true, blocking: true, detail: null },
    { code: 'lab_releaser_present', satisfied: false, blocking: true, detail: null },
    // Unsatisfied and non-blocking, so "Configuration à compléter" is actually
    // rendered in at least one captured scene. A section no screenshot ever
    // shows is a section nobody has looked at.
    { code: 'lab_result_templates_active', satisfied: false, blocking: false, detail: '1/2 analyse(s) structurée(s)' },
  ],
}

/**
 * A Native lab that offers work DHealth cannot complete: active offerings, no
 * active result template. Blocking, because it can finish none of what it sells.
 */
const NATIVE_NO_TEMPLATE_READINESS: FacilityReadiness = {
  organization_id: 'org-untemplated',
  participation_mode: 'NATIVE',
  onboarding_status: 'ORGANIZATION_READY',
  production_routing_allowed: false,
  findings: [
    { code: 'organization_active', satisfied: true, blocking: true, detail: null },
    { code: 'enrollment_not_blocked', satisfied: true, blocking: true, detail: null },
    { code: 'lab_accepting_orders', satisfied: true, blocking: true, detail: null },
    { code: 'lab_has_active_offering', satisfied: true, blocking: true, detail: '2 analyse(s) active(s)' },
    { code: 'lab_technician_present', satisfied: true, blocking: true, detail: null },
    { code: 'lab_releaser_present', satisfied: true, blocking: true, detail: null },
    { code: 'lab_result_templates_active', satisfied: false, blocking: true, detail: '0/2 analyse(s) structurée(s)' },
    { code: 'administrator_present', satisfied: true, blocking: false, detail: null },
  ],
}

/** A provider that may order, for the routing scene. */
const PROVIDER_READY_READINESS: FacilityReadiness = {
  organization_id: 'org-native-provider',
  participation_mode: 'NATIVE',
  onboarding_status: 'READY_FOR_ROUTING',
  production_routing_allowed: true,
  findings: [
    { code: 'organization_active', satisfied: true, blocking: true, detail: null },
    { code: 'enrollment_not_blocked', satisfied: true, blocking: true, detail: null },
    { code: 'clinician_present', satisfied: true, blocking: true, detail: null },
    { code: 'administrator_present', satisfied: true, blocking: false, detail: null },
  ],
}

/** A Native laboratory that can take work today. */
const LAB_READY_READINESS: FacilityReadiness = {
  organization_id: 'org-1',
  participation_mode: 'NATIVE',
  onboarding_status: 'READY_FOR_ROUTING',
  production_routing_allowed: true,
  findings: [
    { code: 'organization_active', satisfied: true, blocking: true, detail: null },
    { code: 'enrollment_not_blocked', satisfied: true, blocking: true, detail: null },
    { code: 'lab_accepting_orders', satisfied: true, blocking: true, detail: null },
    { code: 'lab_has_active_offering', satisfied: true, blocking: true, detail: '3 analyse(s) active(s)' },
    { code: 'lab_technician_present', satisfied: true, blocking: true, detail: null },
    { code: 'lab_releaser_present', satisfied: true, blocking: true, detail: null },
  ],
}

/** A Native laboratory, staffed, so the team section is the subject of the scene. */
const TEAM_LAB: FacilityEnrollment = facilityFixture({
  organization_id: 'org-1',
  organization_name: 'Laboratoire Central',
  participation_mode: 'NATIVE',
  onboarding_status: 'READY_FOR_ROUTING',
  integration_feasibility: null,
  existing_system_name: null,
  existing_system_vendor: null,
  existing_system_version: null,
  hosting_model: null,
  technical_contact: null,
  vendor_contact: null,
  interfaces: [],
})

/** The same laboratory with nobody able to administer it. */
const NO_ADMIN_READINESS: FacilityReadiness = {
  ...LAB_READY_READINESS,
  onboarding_status: 'ORGANIZATION_READY',
  production_routing_allowed: false,
  findings: [
    ...LAB_READY_READINESS.findings.filter(
      (finding) => finding.code !== 'lab_technician_present' && finding.code !== 'lab_releaser_present',
    ),
    { code: 'administrator_present', satisfied: false, blocking: true, detail: null },
    { code: 'lab_technician_present', satisfied: false, blocking: true, detail: null },
    { code: 'lab_releaser_present', satisfied: false, blocking: true, detail: null },
  ],
}

/**
 * A laboratory's team, in the shape the facility screen lists it: one person with
 * every authority, one with a single one, and one whose access is switched off
 * but whose record is still there. The third is the case that has to be looked
 * at -- it must read as "no longer has access", never as a row to be tidied away.
 */
const DEFAULT_TEAM: OrganizationMembership[] = [
  {
    membership_id: 'mem-1',
    identity_id: 'id-1',
    display_name: 'Ahmed Ismail',
    email: 'ahmed@example.test',
    status: 'ACTIVE',
    roles: ['ORG_ADMIN', 'LAB_TECH', 'LAB_RESULT_RELEASER'],
  },
  {
    membership_id: 'mem-2',
    identity_id: 'id-3',
    display_name: 'Fatou Hassan',
    email: 'fatou@example.test',
    status: 'ACTIVE',
    roles: ['LAB_TECH'],
  },
  {
    membership_id: 'mem-3',
    identity_id: 'id-4',
    display_name: 'Omar Warsame',
    email: 'omar@example.test',
    status: 'INACTIVE',
    roles: ['ORG_ADMIN'],
  },
]

/** A care facility's team. Different roles exist here, which is the whole point. */
const PROVIDER_TEAM: OrganizationMembership[] = [
  {
    membership_id: 'mem-p1',
    identity_id: 'id-5',
    display_name: 'Dr. Halima Farah',
    email: 'halima@example.test',
    status: 'ACTIVE',
    roles: ['ORG_ADMIN', 'CLINICIAN'],
  },
  {
    membership_id: 'mem-p2',
    identity_id: 'id-6',
    display_name: 'Dr. Saïd Abdi',
    email: 'said@example.test',
    status: 'ACTIVE',
    roles: ['CLINICIAN'],
  },
]

// ── DH-CONNECT-CONTRACT-001 connector fixtures ────────────────────────────────
//
// Six states of one laboratory's connector, captured so somebody can look at
// them rather than take a test's word. Every one of them has `operational:
// false` and `transport_available: false`, including the qualified one -- if a
// screenshot ever shows a CONNECT facility that looks routable, this is where
// the falsehood would have been invented.

const CONNECTOR_FP_CONFIG = '9f2b7c41d8e05a36bb14ce9207d5f8a1c3e60b94d72af815e0c36d9b47a1f280'
const CONNECTOR_FP_MAPPING = '3a51e0cd7b92486fa0d1c85e73b64902fd18ae57c0b39d462e8f10a7cb95d3e6'

const CONNECTOR_INSTALLATION = {
  installation_id: 'inst-1',
  organization_id: 'org-1',
  connector_key: 'openelis-fhir',
  connector_version: '0.1',
  contract_version: '1.0',
  source_system: 'OpenELIS',
  source_system_version: '2.7',
  interface_type: 'FHIR',
  configuration_fingerprint: CONNECTOR_FP_CONFIG,
  mapping_fingerprint: CONNECTOR_FP_MAPPING,
  status: 'REGISTERED',
  created_at: '2026-09-01T08:00:00Z',
  updated_at: '2026-09-01T08:00:00Z',
}

const CONNECTOR_CAPABILITIES = [
  'ORDER_RECEIPT',
  'BUSINESS_ACCEPTANCE',
  'COLLECTION_EVENT',
  'RELEASED_RESULT_PUBLICATION',
  'AMENDMENT_PUBLICATION',
]

const CONNECTOR_PASSED_RUN = {
  qualification_id: 'qual-1',
  result: 'PASSED',
  suite_version: '2',
  started_at: '2026-09-10T08:00:00Z',
  completed_at: '2026-09-10T08:04:00Z',
  contract_version: '1.0',
  connector_version: '0.1',
  configuration_fingerprint: CONNECTOR_FP_CONFIG,
  mapping_fingerprint: CONNECTOR_FP_MAPPING,
  // Exactly what `QualificationRun.evidence_summary` emits for a full-capability laboratory run:
  // sixteen scenarios, and the caveat naming the one that only checked a declaration. A fixture
  // carrying text the code cannot produce is a screenshot of a state the product cannot reach.
  evidence_summary:
    '16 scénario(s) exécuté(s), tous satisfaits ; déclaration seule : LQ1-order-receipt-declared',
  capabilities: CONNECTOR_CAPABILITIES.map((capability) => ({
    capability,
    passed: true,
    // The one capability the harness cannot drive, recorded as a declaration in the column the
    // verdict actually reads -- not only in the prose beside it, which is what the correction to
    // this ticket fixed.
    evidence_level:
      capability === 'ORDER_RECEIPT' ? ('DECLARED' as const) : ('EXERCISED' as const),
    detail:
      capability === 'ORDER_RECEIPT'
        ? "LQ1-order-receipt-declared: déclaration seule : la réception d'une commande n'est pas exercée dans cette version (aucun transport n'existe pour la livrer)"
        : null,
  })),
}

const CONNECTOR_NONE: FacilityConnector = {
  organization_id: 'org-1',
  participation_mode: 'CONNECT',
  installation: null,
  installations: [],
  verdict: {
    qualified: false,
    gap: 'NO_INSTALLATION',
    detail: "aucun connecteur n'est enregistré pour cet établissement",
    qualification_id: null,
    completed_at: null,
    missing_capabilities: [],
    unexercised_capabilities: [],
    installed_contract_version: null,
    current_contract_version: '1.0',
    current_suite_version: '2',
  },
  health_state: 'UNKNOWN',
  operational: false,
  transport_available: false,
  qualification_history: [],
}

const CONNECTOR_REGISTERED: FacilityConnector = {
  ...CONNECTOR_NONE,
  installation: CONNECTOR_INSTALLATION,
  installations: [CONNECTOR_INSTALLATION],
  verdict: {
    ...CONNECTOR_NONE.verdict,
    gap: 'NEVER_QUALIFIED',
    detail: "le connecteur enregistré n'a pas encore été qualifié",
  },
}

/**
 * What the synthetic laboratory harness actually produces, and the scene that matters most.
 *
 * The suite ran and every scenario it could execute behaved correctly — so the run is PASSED and
 * will stay PASSED forever. `ORDER_RECEIPT` is DECLARED, because it is the one DHealth → connector
 * capability and there is no transport to drive it over. Production qualification is therefore
 * incomplete, and the screen has to say both things at once without either implying the other.
 */
const CONNECTOR_SUITE_PASSED: FacilityConnector = {
  ...CONNECTOR_REGISTERED,
  verdict: {
    ...CONNECTOR_REGISTERED.verdict,
    qualified: false,
    gap: 'REQUIRED_CAPABILITY_UNEXERCISED',
    detail:
      '1 capacité requise déclarée par le connecteur mais non encore exercée ; la qualification' +
      ' de production est incomplète',
    qualification_id: 'qual-1',
    completed_at: '2026-09-10T08:04:00Z',
    unexercised_capabilities: ['ORDER_RECEIPT'],
  },
  qualification_history: [CONNECTOR_PASSED_RUN],
}

/**
 * Fully qualified — which nothing in this build can produce.
 *
 * Every capability EXERCISED including `ORDER_RECEIPT`, which requires a delivery path that does
 * not exist. Kept as a scene because it is the state DH-CONNECT-AGENT-001 will legitimately reach,
 * and because it is the clearest demonstration that even then the facility stays unroutable:
 * `operational` is still false below, and so is production routing.
 */
const CONNECTOR_QUALIFIED: FacilityConnector = {
  ...CONNECTOR_REGISTERED,
  verdict: {
    qualified: true,
    gap: null,
    detail: 'connecteur qualifié pour cette configuration',
    qualification_id: 'qual-1',
    completed_at: '2026-09-10T08:04:00Z',
    missing_capabilities: [],
    unexercised_capabilities: [],
    installed_contract_version: '1.0',
    current_contract_version: '1.0',
    current_suite_version: '2',
  },
  qualification_history: [
    {
      ...CONNECTOR_PASSED_RUN,
      evidence_summary: '16 scénario(s) exécuté(s), tous satisfaits',
      capabilities: CONNECTOR_CAPABILITIES.map((capability) => ({
        capability,
        passed: true,
        evidence_level: 'EXERCISED' as const,
        detail: null,
      })),
    },
  ],
}

const CONNECTOR_FAILED: FacilityConnector = {
  ...CONNECTOR_REGISTERED,
  verdict: {
    ...CONNECTOR_REGISTERED.verdict,
    gap: 'LAST_ATTEMPT_FAILED',
    detail: 'la dernière qualification a échoué',
  },
  qualification_history: [
    {
      ...CONNECTOR_PASSED_RUN,
      qualification_id: 'qual-0',
      result: 'FAILED',
      completed_at: '2026-09-08T11:20:00Z',
      evidence_summary:
        '13/16 scénario(s) satisfaits ; non satisfaits : LQ10-amendment, ' +
        'LQ11-original-remains-reconstructable, LQ10-post-release-correction ; ' +
        'déclaration seule : LQ1-order-receipt-declared',
      capabilities: CONNECTOR_CAPABILITIES.map((capability) => ({
        capability,
        passed: capability !== 'AMENDMENT_PUBLICATION',
        evidence_level:
          capability === 'ORDER_RECEIPT' ? ('DECLARED' as const) : ('EXERCISED' as const),
        detail:
          capability === 'AMENDMENT_PUBLICATION'
            ? 'LQ10-amendment: capacité non déclarée par le connecteur'
            : null,
      })),
    },
  ],
}

const CONNECTOR_STALE: FacilityConnector = {
  ...CONNECTOR_QUALIFIED,
  installation: {
    ...CONNECTOR_INSTALLATION,
    mapping_fingerprint: 'c48d0a92f16b3e7508cd21ba9e4f6307d5182ca6b09e3f741dc85a02e6b91f3d',
    updated_at: '2026-09-18T14:05:00Z',
  },
  verdict: {
    ...CONNECTOR_QUALIFIED.verdict,
    qualified: false,
    gap: 'QUALIFICATION_STALE',
    detail:
      'la configuration ou la correspondance des analyses a changé depuis la dernière qualification',
  },
}

const CONNECTOR_SUSPENDED: FacilityConnector = {
  ...CONNECTOR_QUALIFIED,
  installation: { ...CONNECTOR_INSTALLATION, status: 'SUSPENDED' },
  installations: [{ ...CONNECTOR_INSTALLATION, status: 'SUSPENDED' }],
  verdict: {
    ...CONNECTOR_QUALIFIED.verdict,
    qualified: false,
    gap: 'SUSPENDED',
    detail: 'le connecteur est suspendu',
  },
}

/** A CONNECT laboratory whose connector qualified and still cannot be routed to. */
const CONNECT_QUALIFIED_READINESS: FacilityReadiness = {
  ...CONNECT_READINESS,
  findings: [
    { code: 'organization_active', satisfied: true, blocking: true, detail: null },
    { code: 'enrollment_not_blocked', satisfied: true, blocking: true, detail: null },
    { code: 'lab_accepting_orders', satisfied: true, blocking: true, detail: null },
    {
      code: 'lab_has_active_offering',
      satisfied: true,
      blocking: true,
      detail: '3 analyse(s) active(s)',
    },
    {
      code: 'connector_qualified',
      satisfied: true,
      blocking: true,
      detail: 'connecteur qualifié pour cette configuration',
    },
    // Satisfied above, refused here. The whole ticket in two adjacent rows.
    {
      code: 'connector_operational',
      satisfied: false,
      blocking: true,
      detail:
        "aucune exécution de connecteur ne remonte d'état ; le transport DHealth Connect n'est pas construit",
    },
    { code: 'administrator_present', satisfied: true, blocking: false, detail: null },
  ],
}

function platformClient(
  facilities: FacilityEnrollment[],
  readiness: FacilityReadiness,
  readinessByOrg: Record<string, FacilityReadiness> = {},
  routes: ProviderLabRoute[] = [],
  team: OrganizationMembership[] = DEFAULT_TEAM,
  connector: FacilityConnector = CONNECTOR_NONE,
): ExchangeClient {
  return {
    getFacilityConnector: () => Promise.resolve(connector),
    setFacilityConnectorStatus: () =>
      Promise.reject(new Error('the harness never writes; scenes are captured before the write')),
    getMe: () => Promise.resolve(platformMe()),
    listFacilities: () => Promise.resolve(facilities),
    // Every facility detail has a team section, so this answers for all of them.
    // A scene that left it unstubbed would be a picture of a load failure.
    //
    // Per organization, not one shared answer: a care facility's team holds
    // CLINICIAN and a laboratory's holds LAB_TECH, and `roleLabel` falls back to
    // the raw enum for a role the facility type cannot grant. A single team for
    // both therefore put `LAB_RESULT_RELEASER` on a clinic -- a screenshot of
    // something the product cannot produce, which is what these captures exist
    // to catch.
    listMemberships: (organizationId: string) =>
      Promise.resolve(
        facilities.find((facility) => facility.organization_id === organizationId)
          ?.organization_type === 'PROVIDER'
          ? PROVIDER_TEAM
          : team,
      ),
    createMembershipWithOutcome: () =>
      Promise.reject(new Error('the harness never writes; scenes are captured before the write')),
    // Per-organization when a scene needs several facilities to differ -- the
    // routing scene shows one ready laboratory beside one that is not, and a
    // single shared answer would make that scene a picture of something the
    // product cannot produce.
    getFacilityReadiness: (organizationId: string) =>
      Promise.resolve(readinessByOrg[organizationId] ?? readiness),
    listPlatformProviderLabRoutes: () => Promise.resolve(routes),
    setProviderLabRoute: () =>
      Promise.reject(new Error('the harness never writes; scenes are captured before the write')),
    // Org-scoped reads, for the scene where the platform admin also holds a
    // membership and therefore lands in organization scope first.
    listOrders: () => Promise.resolve([]),
    listPatients: () => Promise.resolve([]),
    listResultReviews: () => Promise.resolve({ entries: [] }),
    listLabOrders: () => Promise.resolve([]),
    lookupIdentities: () =>
      Promise.resolve([
        { identity_id: 'id-1', display_name: 'Ahmed Ismail', email: 'ahmed@example.test' },
        { identity_id: 'id-2', display_name: 'Ahmed Osman', email: 'a.osman@example.test' },
        // Already on `DEFAULT_TEAM` with one role, which is what makes the add
        // panel show a held role locked beside two that can still be granted.
        { identity_id: 'id-3', display_name: 'Fatou Hassan', email: 'fatou@example.test' },
      ]),
  } as unknown as ExchangeClient
}

function PlatformScene({
  facilities = NETWORK_FIXTURE,
  readiness = CONNECT_READINESS,
  readinessByOrg = {},
  routes = [],
  memberships = [],
  team = DEFAULT_TEAM,
  connector = CONNECTOR_NONE,
}: {
  facilities?: FacilityEnrollment[]
  readiness?: FacilityReadiness
  readinessByOrg?: Record<string, FacilityReadiness>
  routes?: ProviderLabRoute[]
  memberships?: ExchangeMembershipSummary[]
  team?: OrganizationMembership[]
  connector?: FacilityConnector
}) {
  const client = {
    ...platformClient(facilities, readiness, readinessByOrg, routes, team, connector),
    getMe: () => Promise.resolve(platformMe(memberships)),
  } as unknown as ExchangeClient
  return (
    <WorkspaceProvider client={client}>
      <StaffWorkspace />
    </WorkspaceProvider>
  )
}

const SCENES: Record<string, () => ReactElement> = {
  'shell-provider': () => (
    <ShellScene
      response={me([membership('PROVIDER', 'Clinique de la Paix', ['CLINICIAN'])], 'Dr. Halima Farah')}
    />
  ),
  'shell-lab-dual': () => (
    <ShellScene
      response={me(
        [membership('LAB', 'Laboratoire Central', ['LAB_TECH', 'LAB_RESULT_RELEASER'])],
        'Ahmed Osman',
      )}
    />
  ),
  'shell-two-establishments': () => (
    <ShellScene
      response={me(
        [
          membership('PROVIDER', 'Clinique de la Paix', ['CLINICIAN']),
          membership('LAB', 'Laboratoire Central', ['LAB_TECH', 'LAB_RESULT_RELEASER']),
        ],
        'Dr. Halima Farah',
      )}
    />
  ),
  relay: () => <RelayScene amended={false} />,
  'relay-amended': () => <RelayScene amended />,
  patient: () => <PatientStatusPage order={PATIENT_ORDER} />,

  // DH-MVP-ONBOARD-001 platform scenes. Network administration is desktop and
  // tablet work, captured at 1280 and 768 rather than at phone width.
  'platform-zero-membership': () => <PlatformScene />,
  'platform-facility-list': () => <PlatformScene />,
  'platform-new-facility': () => <PlatformScene />,
  // The first-administrator step: a person found, selected, and three laboratory
  // roles ticked. The scene the wizard existed to offer and did not perform.
  'platform-first-admin': () => <PlatformScene />,
  // §65: the team of a facility that already exists. The hole this closes is
  // "our old administrator left and Fatou is taking over" -- until this section
  // there was no screen for it, only the API and somebody's identity UUID.
  'platform-team': () => (
    <PlatformScene facilities={[TEAM_LAB]} readiness={LAB_READY_READINESS} />
  ),
  // The same screen with the add panel open on somebody already on the team: the
  // role she holds is ticked and locked, because nothing here removes access.
  'platform-team-add': () => (
    <PlatformScene facilities={[TEAM_LAB]} readiness={LAB_READY_READINESS} />
  ),
  // A facility nobody administers -- enrolled before the person meant to run it
  // had ever signed in. The readiness blocker and the empty team are the same
  // fact stated twice, and the action that fixes it is on this screen.
  'platform-team-empty': () => (
    <PlatformScene
      // Its own onboarding status, not TEAM_LAB's: a facility nobody can
      // administer is not "Prêt pour le routage", and a scene showing that
      // header above three blockers would be a picture of an impossible state.
      facilities={[{ ...TEAM_LAB, onboarding_status: 'ORGANIZATION_READY' }]}
      readiness={NO_ADMIN_READINESS}
      team={[]}
    />
  ),
  // A Native laboratory blocked because it can complete none of what it offers:
  // active offerings, no active result template, so DHealth could enter nothing.
  'platform-native-no-template': () => (
    <PlatformScene
      facilities={[
        facilityFixture({
          organization_id: 'org-untemplated',
          organization_name: 'Laboratoire Bahr',
          participation_mode: 'NATIVE',
          onboarding_status: 'ORGANIZATION_READY',
          integration_feasibility: null,
          existing_system_name: null,
          existing_system_vendor: null,
          existing_system_version: null,
          hosting_model: null,
          technical_contact: null,
          vendor_contact: null,
          interfaces: [],
        }),
      ]}
      readiness={NATIVE_NO_TEMPLATE_READINESS}
    />
  ),
  // The same laboratory once its templates are published: nothing else changed.
  'platform-native-ready': () => (
    <PlatformScene
      facilities={[
        facilityFixture({
          organization_id: 'org-1',
          organization_name: 'Laboratoire Nour',
          participation_mode: 'NATIVE',
          onboarding_status: 'READY_FOR_ROUTING',
          integration_feasibility: null,
          existing_system_name: null,
          existing_system_vendor: null,
          existing_system_version: null,
          hosting_model: null,
          technical_contact: null,
          vendor_contact: null,
          interfaces: [],
        }),
      ]}
      readiness={LAB_READY_READINESS}
    />
  ),
  'platform-with-membership': () => (
    <PlatformScene memberships={[membership('PROVIDER', 'Clinique Al Rahma', ['CLINICIAN'])]} />
  ),
  'platform-connect-readiness': () => <PlatformScene />,
  // A Native laboratory whose last release authority went inactive: the route it
  // is configured on is untouched, and it is not currently a usable destination.
  'platform-native-blocked': () => (
    <PlatformScene
      facilities={[
        facilityFixture({
          organization_id: 'org-native-lab',
          organization_name: 'Laboratoire Horizon',
          participation_mode: 'NATIVE',
          onboarding_status: 'ORGANIZATION_READY',
          integration_feasibility: null,
          existing_system_name: null,
          existing_system_vendor: null,
          existing_system_version: null,
          hosting_model: null,
          technical_contact: null,
          vendor_contact: null,
          interfaces: [],
        }),
      ]}
      readiness={NATIVE_BLOCKED_READINESS}
    />
  ),
  // §62: the provider's network relationships. One laboratory ready to be
  // switched on, one Native laboratory already configured whose release
  // authority went inactive -- configured and unavailable at the same time --
  // and one Connect laboratory that cannot be offered at all.
  'platform-route-activation': () => (
    <PlatformScene
      readinessByOrg={{
        'org-native-provider': PROVIDER_READY_READINESS,
        'org-ready-lab': LAB_READY_READINESS,
        'org-native-lab': NATIVE_BLOCKED_READINESS,
        // `org-1` is a Connect laboratory and deliberately keeps a *ready*
        // readiness here: no staffing answer makes a connector exist, so it must
        // still be unofferable, and the scene shows that rather than asserting it.
        'org-1': LAB_READY_READINESS,
      }}
      facilities={[
        ...NETWORK_FIXTURE,
        facilityFixture({
          organization_id: 'org-ready-lab',
          organization_name: 'Laboratoire Nour',
          participation_mode: 'NATIVE',
          onboarding_status: 'READY_FOR_ROUTING',
          integration_feasibility: null,
          existing_system_name: null,
          existing_system_vendor: null,
          existing_system_version: null,
          hosting_model: null,
          technical_contact: null,
          vendor_contact: null,
          interfaces: [],
        }),
        facilityFixture({
          organization_id: 'org-native-lab',
          organization_name: 'Laboratoire Horizon',
          participation_mode: 'NATIVE',
          onboarding_status: 'ORGANIZATION_READY',
          integration_feasibility: null,
          existing_system_name: null,
          existing_system_vendor: null,
          existing_system_version: null,
          hosting_model: null,
          technical_contact: null,
          vendor_contact: null,
          interfaces: [],
        }),
        facilityFixture({
          organization_id: 'org-connect-lab',
          organization_name: 'Laboratoire Bahr',
          participation_mode: 'CONNECT',
          onboarding_status: 'AWAITING_CONNECTOR',
        }),
      ]}
      routes={[
        {
          id: 'route-horizon',
          provider_organization_id: 'org-native-provider',
          lab_organization_id: 'org-native-lab',
          lab_organization_name: 'Laboratoire Horizon',
          active: true,
          created_at: '2026-09-20T09:00:00Z',
          updated_at: '2026-09-20T09:00:00Z',
        },
      ]}
    />
  ),
  // ── DH-CONNECT-CONTRACT-001 §79: the connector states, captured ────────────
  //
  // Seven scenes because the states genuinely differ on screen and somebody has
  // to be able to look at each one. The pair that matters most is
  // `connector-qualified` beside `connector-none`: the first is the best state
  // any CONNECT facility can reach in this release, and it still says the
  // routing is off. If that ever stops being visible in the capture, the product
  // has started implying something it cannot know.
  'connector-none': () => (
    <PlatformScene facilities={[facilityFixture()]} connector={CONNECTOR_NONE} />
  ),
  'connector-registered': () => (
    <PlatformScene
      facilities={[facilityFixture()]}
      readiness={CONNECT_REGISTERED_READINESS}
      connector={CONNECTOR_REGISTERED}
    />
  ),
  'connector-failed': () => (
    <PlatformScene
      facilities={[facilityFixture()]}
      readiness={connectReadinessWithReason('la dernière qualification a échoué')}
      connector={CONNECTOR_FAILED}
    />
  ),
  // The truthful synthetic state, and the scene that matters most after the correction: the
  // suite passed and production qualification is incomplete, both said at once.
  'connector-suite-passed': () => (
    <PlatformScene
      facilities={[facilityFixture()]}
      readiness={connectReadinessWithReason(
        '1 capacité requise déclarée par le connecteur mais non encore exercée ; la ' +
          'qualification de production est incomplète',
      )}
      connector={CONNECTOR_SUITE_PASSED}
    />
  ),
  // Fully qualified, which nothing in this build can produce -- ORDER_RECEIPT exercised requires
  // a delivery path that does not exist. Kept because it shows that even then the facility stays
  // unroutable, which is the claim the next ticket must not quietly undo.
  'connector-qualified': () => (
    <PlatformScene
      facilities={[facilityFixture()]}
      readiness={CONNECT_QUALIFIED_READINESS}
      connector={CONNECTOR_QUALIFIED}
    />
  ),
  'connector-stale': () => (
    <PlatformScene
      facilities={[facilityFixture()]}
      readiness={CONNECT_STALE_READINESS}
      connector={CONNECTOR_STALE}
    />
  ),
  'connector-suspended': () => (
    <PlatformScene
      facilities={[facilityFixture()]}
      readiness={connectReadinessWithReason('le connecteur est suspendu')}
      connector={CONNECTOR_SUSPENDED}
    />
  ),
  // The control. A NATIVE facility gets no connector section at all -- not an
  // empty one, not a greyed one. It needs no connector, and a panel telling it
  // so would invent a deficiency it does not have.
  'connector-native-unchanged': () => (
    <PlatformScene facilities={[TEAM_LAB]} readiness={LAB_READY_READINESS} />
  ),

  'platform-not-integrable': () => (
    <PlatformScene
      facilities={[
        facilityFixture({
          integration_feasibility: 'NOT_SAFELY_INTEGRABLE',
          open_blockers: [
            {
              id: 'blk-x',
              code: 'REQUIRED_INTERFACE_UNAVAILABLE',
              note: "aucune interface supportee par l'editeur actuel",
              created_at: '2026-09-20T09:00:00Z',
            },
          ],
        }),
      ]}
    />
  ),
}


const scene = new URLSearchParams(window.location.search).get('scene') ?? 'shell-provider'
const render = SCENES[scene]

createRoot(document.getElementById('root')!).render(
  render ? (
    render()
  ) : (
    <div style={{ padding: 24, fontFamily: 'var(--font-family-sans)' }}>
      <h1>Scène inconnue : {scene}</h1>
      <ul>
        {Object.keys(SCENES).map((name) => (
          <li key={name}>
            <a href={'?scene=' + name}>{name}</a>
          </li>
        ))}
      </ul>
    </div>
  ),
)
