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
import type { ExchangeMembershipSummary, ExchangeMeResponse, PatientOrder } from '../src/api/exchangeTypes'
import { PatientStatusPage } from '../src/features/patient/PatientStatusPage'
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
