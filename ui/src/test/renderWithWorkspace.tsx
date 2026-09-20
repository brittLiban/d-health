/**
 * DH-MVP-POLISH-001 — mounting anything that needs a staff session.
 *
 * `ClinicProvider` and `LabProvider` no longer load `/me` themselves; they
 * project the one workspace. That is deliberate — a second way to establish a
 * session is a second thing that can disagree with the first about who is signed
 * in and where — and it means a test can no longer hand a provider its own
 * client and expect a session to appear.
 *
 * So tests mount the real `WorkspaceProvider` over a stubbed client, exercising
 * the same load path production uses rather than a test-only shortcut. The
 * builders below keep the fixtures honest: a membership that would be filtered
 * out by the real eligibility rule is filtered out here too.
 */

import { render, screen, within, type RenderResult } from '@testing-library/react'
import { vi } from 'vitest'
import type { ReactElement } from 'react'
import type { ExchangeClient } from '../api/exchangeClient'
import type {
  ExchangeMembershipSummary,
  ExchangeMeResponse,
} from '../api/exchangeTypes'
import { WorkspaceProvider } from '../features/workspace/WorkspaceProvider'

export function providerMembership(
  organizationId: string,
  organizationName: string,
  roles: string[] = ['CLINICIAN'],
): ExchangeMembershipSummary {
  return {
    organization_id: organizationId,
    organization_name: organizationName,
    organization_type: 'PROVIDER',
    organization_status: 'ACTIVE',
    membership_status: 'ACTIVE',
    roles,
  }
}

export function labMembership(
  organizationId: string,
  organizationName: string,
  roles: string[] = ['LAB_TECH', 'LAB_RESULT_RELEASER'],
): ExchangeMembershipSummary {
  return {
    organization_id: organizationId,
    organization_name: organizationName,
    organization_type: 'LAB',
    organization_status: 'ACTIVE',
    membership_status: 'ACTIVE',
    roles,
  }
}

export function meWith(
  memberships: ExchangeMembershipSummary[],
  overrides: Partial<ExchangeMeResponse> = {},
): ExchangeMeResponse {
  return {
    identity_id: 'identity-1',
    issuer: 'https://keycloak.test/realms/dhealth-exchange',
    subject: 'subject-1',
    display_name: 'Dr. Halima Farah',
    email: 'halima.farah@clinique.test',
    platform_admin: false,
    memberships,
    ...overrides,
  }
}

/**
 * A client whose reads all resolve empty, so a test only has to state the calls
 * it actually cares about. Every page below the workspace fetches on mount; a
 * client missing one of these would fail on an unrelated rejection rather than
 * on the behaviour under test.
 */
export function workspaceClient(
  me: ExchangeMeResponse,
  overrides: Partial<Record<keyof ExchangeClient, unknown>> = {},
): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(me),
    listOrders: vi.fn().mockResolvedValue([]),
    getOrder: vi.fn().mockResolvedValue({}),
    getOrderResults: vi.fn().mockResolvedValue({}),
    getReleaseVersions: vi.fn().mockResolvedValue({}),
    listLabOrders: vi.fn().mockResolvedValue([]),
    listPatients: vi.fn().mockResolvedValue([]),
    createPatientWithOutcome: vi.fn().mockResolvedValue({}),
    ...overrides,
  } as unknown as ExchangeClient
}

export interface RenderWithWorkspaceOptions {
  /** Defaults to a single active provider membership held as CLINICIAN. */
  me?: ExchangeMeResponse
  /** Overrides `me` entirely when supplied. */
  client?: ExchangeClient
}

export interface RenderWithWorkspaceResult extends RenderResult {
  client: ExchangeClient
}

export function renderWithWorkspace(
  ui: ReactElement,
  options: RenderWithWorkspaceOptions = {},
): RenderWithWorkspaceResult {
  const me = options.me ?? meWith([providerMembership('org-provider-1', 'Clinique de la Paix')])
  const client = options.client ?? workspaceClient(me)

  const result = render(<WorkspaceProvider client={client}>{ui}</WorkspaceProvider>)
  return Object.assign(result, { client })
}

/** The accessible name of the staff shell's identity band. */
export const MASTHEAD_NAME = 'Session et établissement actif'

/** The accessible name of the staff shell's section navigation. */
export const NAV_NAME = 'Navigation principale'

/**
 * A section button from the navigation region specifically.
 *
 * Two reasons it is both scoped and awaited. Scoped, because a page legitimately
 * contains its own buttons named "Ordres" or "Résultats", and an unscoped query
 * would match either one. Awaited, because the unified shell does not draw
 * navigation until the session has loaded — it cannot know which sections a
 * membership unlocks before `/me` returns, and drawing a navigation it then has
 * to correct is a worse answer than drawing it once.
 */
export async function sectionButton(name: string): Promise<HTMLElement> {
  const nav = within(await screen.findByRole('navigation', { name: NAV_NAME }))
  return nav.getByRole('button', { name })
}

/** Queries scoped to the identity band, which must be unambiguous about the
 *  establishment in use even when the page below names it too. */
export async function masthead() {
  return within(await screen.findByRole('banner', { name: MASTHEAD_NAME }))
}
