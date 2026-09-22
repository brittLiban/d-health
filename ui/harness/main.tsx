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
  ExchangeMembershipSummary,
  ExchangeMeResponse,
  FacilityEnrollment,
  FacilityReadiness,
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
    { code: 'connector_qualified', satisfied: false, blocking: true, detail: "aucun connecteur DHealth n'existe encore pour cet établissement" },
    { code: 'administrator_present', satisfied: true, blocking: false, detail: null },
  ],
}

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

function platformClient(
  facilities: FacilityEnrollment[],
  readiness: FacilityReadiness,
  readinessByOrg: Record<string, FacilityReadiness> = {},
  routes: ProviderLabRoute[] = [],
): ExchangeClient {
  return {
    getMe: () => Promise.resolve(platformMe()),
    listFacilities: () => Promise.resolve(facilities),
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
      ]),
  } as unknown as ExchangeClient
}

function PlatformScene({
  facilities = NETWORK_FIXTURE,
  readiness = CONNECT_READINESS,
  readinessByOrg = {},
  routes = [],
  memberships = [],
}: {
  facilities?: FacilityEnrollment[]
  readiness?: FacilityReadiness
  readinessByOrg?: Record<string, FacilityReadiness>
  routes?: ProviderLabRoute[]
  memberships?: ExchangeMembershipSummary[]
}) {
  const client = {
    ...platformClient(facilities, readiness, readinessByOrg, routes),
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
