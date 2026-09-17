import { readFileSync, writeFileSync } from 'node:fs'

/**
 * DH-CLINIC-004B: the only channel between global-setup.ts (which provisions
 * synthetic identities/organizations once, before any test runs) and the spec
 * files (which read this to log in as a specific synthetic identity or make a
 * raw admin-API call). Written to a path the orchestrator controls via
 * E2E_STATE_FILE -- always outside the repository, never committed, deleted
 * by global-teardown.ts. Contains only synthetic test credentials, never a
 * real patient/staff identifier and never a long-lived production secret.
 */
export interface QualificationState {
  adminToken: string
  setupClient: { uuid: string; clientId: string; secret: string }
  providerOrgA: { id: string; name: string }
  providerOrgB: { id: string; name: string }
  clinicianA: { id: string; email: string; password: string; identityId: string }
  clinicianB: { id: string; email: string; password: string; identityId: string }
  unassigned: { id: string; email: string; password: string; identityId: string }
  shortAccessTokenLifespanSeconds: number | null
}

function statePath(): string {
  const path = process.env.E2E_STATE_FILE
  if (!path) throw new Error('E2E_STATE_FILE is required (set by scripts/qualify-native-auth.sh)')
  return path
}

export function writeState(state: QualificationState): void {
  writeFileSync(statePath(), JSON.stringify(state, null, 2), { mode: 0o600 })
}

export function readState(): QualificationState {
  return JSON.parse(readFileSync(statePath(), 'utf-8')) as QualificationState
}
