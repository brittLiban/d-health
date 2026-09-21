import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ExchangeApiError,
  ExchangeClient,
  exchangeClient as defaultExchangeClient,
} from '../../api/exchangeClient'
import type {
  ExchangeIdentity,
  ExchangeMembershipSummary,
} from '../../api/exchangeTypes'
import { WorkspaceContext, type WorkspaceScope, type WorkspaceStatus } from './WorkspaceContext'
import { capabilitiesFor, usableMemberships } from './workspaceCapabilities'

export interface WorkspaceProviderProps {
  children: ReactNode
  client?: ExchangeClient
}

/**
 * DH-MVP-POLISH-001 — loads `/me` once for the whole staff application.
 *
 * Before this ticket, `ClinicProvider` and `LabProvider` each carried their own
 * copy of this logic, and each carried it *twice* over: once in a `reload`
 * callback and again, line for line, in a mount effect. Two providers times two
 * copies is four places where the eligibility rule had to agree, and nothing
 * made them agree. There is now one code path, called from both entry points.
 *
 * A single sequence number does the staleness work. Every load takes the next
 * number and, after awaiting, discards its own result if a newer load has since
 * started — and the unmount cleanup bumps the number too, so a response that
 * arrives after this provider is gone is dropped rather than written into state.
 * That also makes React's development-mode double mount harmless.
 */
export function WorkspaceProvider({
  children,
  client = defaultExchangeClient,
}: WorkspaceProviderProps) {
  const [status, setStatus] = useState<WorkspaceStatus>('loading')
  const [user, setUser] = useState<ExchangeIdentity | null>(null)
  const [organizations, setOrganizations] = useState<ExchangeMembershipSummary[]>([])
  const [activeOrganizationId, setActiveOrganizationIdState] = useState<string | null>(null)
  const [error, setError] = useState<ExchangeApiError | null>(null)
  const [generation, setGeneration] = useState(0)
  const [scope, setScopeState] = useState<WorkspaceScope>('organization')

  const requestSeq = useRef(0)

  /**
   * Fetches and applies the session. Deliberately does not announce `loading`
   * itself: on mount that would be a redundant second render, since `status`
   * already starts there. Announcing it belongs to `reload`, which is the only
   * caller for which the transition is real.
   */
  const load = useCallback(async () => {
    const requestId = ++requestSeq.current

    try {
      const me = await client.getMe()
      if (requestId !== requestSeq.current) return

      // A load that succeeded retires whatever failure preceded it. `reload`
      // clears this too, but the mount effect re-runs whenever the client
      // identity changes, and that path must not leave a stale error behind.
      setError(null)

      setUser({
        identity_id: me.identity_id,
        issuer: me.issuer,
        subject: me.subject,
        display_name: me.display_name,
        email: me.email,
        platform_admin: me.platform_admin,
      })

      // Server order is preserved deliberately: the list a person sees should not
      // reorder itself between sessions for reasons they cannot observe.
      const usable = usableMemberships(me.memberships)
      setOrganizations(usable)

      if (usable.length === 0) {
        setActiveOrganizationIdState(null)
        // A platform administrator with no membership is not a broken account: they
        // administer the network rather than working inside a facility. Telling them
        // "no establishment is attached to your account" would be true and useless,
        // and inventing an organization so the shell had something to put in
        // `activeOrganization` would be false — every capability check and every
        // org-scoped read would then be answering about a facility that does not
        // exist. They get platform scope, where `activeOrganization` stays null.
        if (me.platform_admin) {
          setScopeState('platform')
          setStatus('ready')
          return
        }
        setStatus('no_organizations')
        return
      }

      setStatus('ready')
      setActiveOrganizationIdState((current) => {
        if (current && usable.some((m) => m.organization_id === current)) return current
        return usable[0].organization_id
      })
    } catch (err) {
      if (requestId !== requestSeq.current) return

      if (err instanceof ExchangeApiError) {
        setError(err)
        setStatus(err.status === 401 ? 'unauthenticated' : 'error')
        return
      }
      setError(
        new ExchangeApiError(
          0,
          'NETWORK_ERROR',
          err instanceof Error ? err.message : 'Erreur inattendue.',
        ),
      )
      setStatus('error')
    }
  }, [client])

  /** The retry path. Shows the load as in progress, because here it is a change. */
  const reload = useCallback(async () => {
    setStatus('loading')
    setError(null)
    await load()
  }, [load])

  useEffect(() => {
    void load()
    // Invalidates any in-flight load so its result cannot land after unmount.
    return () => {
      requestSeq.current += 1
    }
  }, [load])

  const activeOrganization = useMemo(
    () => organizations.find((m) => m.organization_id === activeOrganizationId) ?? null,
    [organizations, activeOrganizationId],
  )

  const capabilities = useMemo(() => capabilitiesFor(activeOrganization), [activeOrganization])

  const setActiveOrganizationId = useCallback(
    (organizationId: string) => {
      if (organizationId === activeOrganizationId) return
      // Silently ignores an id that is not a usable membership rather than
      // clearing the active organization: a stale control must never be able to
      // leave the workspace pointing at nothing.
      if (!organizations.some((m) => m.organization_id === organizationId)) return

      // Both decided before either setter runs. Bumping `generation` from inside
      // the other updater would double-count it, because React is free to call
      // an updater more than once for the same change.
      setActiveOrganizationIdState(organizationId)
      setGeneration((g) => g + 1)
    },
    [activeOrganizationId, organizations],
  )

  const platformAdmin = user?.platform_admin === true

  const setScope = useCallback(
    (next: WorkspaceScope) => {
      if (next === scope) return
      // Platform scope is unreachable without platform authority. The control that
      // offers it is hidden for everyone else, and that hiding is a courtesy: this
      // check is what actually prevents a stale or crafted call from putting an
      // ordinary user in front of network administration, and the platform
      // endpoints refuse them regardless.
      if (next === 'platform' && !platformAdmin) return
      // Leaving platform scope needs somewhere to land. A platform admin with no
      // membership has nowhere, so they stay where they are.
      if (next === 'organization' && organizations.length === 0) return
      setScopeState(next)
      // A scope change invalidates every scoped async surface on screen for exactly
      // the same reason an organization change does.
      setGeneration((g) => g + 1)
    },
    [scope, platformAdmin, organizations.length],
  )

  const value = useMemo(
    () => ({
      status,
      user,
      platformAdmin,
      scope,
      setScope,
      organizations,
      activeOrganization,
      capabilities,
      error,
      client,
      generation,
      setActiveOrganizationId,
      reload,
    }),
    [
      status,
      user,
      platformAdmin,
      scope,
      setScope,
      organizations,
      activeOrganization,
      capabilities,
      error,
      client,
      generation,
      setActiveOrganizationId,
      reload,
    ],
  )

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}
