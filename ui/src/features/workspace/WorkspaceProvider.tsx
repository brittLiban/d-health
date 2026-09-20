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
import { WorkspaceContext, type WorkspaceStatus } from './WorkspaceContext'
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
        setStatus('no_organizations')
        setActiveOrganizationIdState(null)
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

  const value = useMemo(
    () => ({
      status,
      user,
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
