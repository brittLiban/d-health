/**
 * DH-MVP-ONBOARD-001 — network administration, inside the one DHealth shell.
 *
 * Not a second product and not an "admin app": the same masthead, the same
 * design tokens, the same authentication. What differs is scope — these surfaces
 * act on the network rather than inside a facility, and they read platform
 * endpoints that take no organization id.
 *
 * Everything shown here is server truth. The readiness findings, the blockers,
 * and whether production routing is permitted are all computed by the backend and
 * rendered by this code; nothing in React decides whether a facility is ready.
 * That separation is the point of §74 — a frontend that recomputed readiness
 * would eventually disagree with the server, and the disagreement would be
 * invisible until somebody activated a route that should not have been.
 */

import { useCallback, useEffect, useState } from 'react'
import type { ExchangeApiError, ExchangeClient } from '../../api/exchangeClient'
import type { FacilityEnrollment } from '../../api/exchangeTypes'
import { EmptyState, ErrorState, LoadingSkeleton } from '../../design-system'
import type { PlatformSectionId } from '../staff/staffSections'
import { FacilityDetail } from './FacilityDetail'
import { FacilityList } from './FacilityList'
import { NewFacilityWizard } from './NewFacilityWizard'
import { IntegrationsOverview } from './IntegrationsOverview'

export interface PlatformWorkspaceProps {
  section: PlatformSectionId
  client: ExchangeClient
  openFacilityId: string | null
  onOpenFacility: (organizationId: string | null) => void
}

type LoadState =
  | { status: 'loading' }
  | { status: 'ready'; facilities: FacilityEnrollment[] }
  | { status: 'error'; error: ExchangeApiError }

export function PlatformWorkspace({
  section,
  client,
  openFacilityId,
  onOpenFacility,
}: PlatformWorkspaceProps) {
  const [state, setState] = useState<LoadState>({ status: 'loading' })
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setState({ status: 'loading' })
    try {
      const facilities = await client.listFacilities()
      setState({ status: 'ready', facilities })
    } catch (error) {
      setState({ status: 'error', error: error as ExchangeApiError })
    }
  }, [client])

  useEffect(() => {
    void load()
  }, [load])

  if (state.status === 'loading') {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        <LoadingSkeleton />
      </div>
    )
  }

  if (state.status === 'error') {
    // 403 here means the session no longer holds platform authority — a stale
    // capability, not a broken server. Saying so plainly beats a generic failure.
    const forbidden = state.error?.status === 403
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        {forbidden ? (
          <EmptyState title="Administration réseau non disponible">
            Votre compte ne dispose pas (ou plus) des autorisations d'administration de la
            plateforme DHealth.
          </EmptyState>
        ) : (
          <ErrorState code="SERVER_ERROR" onRetry={() => void load()} />
        )}
      </div>
    )
  }

  const open = openFacilityId
    ? (state.facilities.find((f) => f.organization_id === openFacilityId) ?? null)
    : null

  if (section === 'integrations') {
    return <IntegrationsOverview facilities={state.facilities} onOpenFacility={onOpenFacility} />
  }

  if (creating) {
    return (
      <NewFacilityWizard
        client={client}
        onCancel={() => setCreating(false)}
        onEnrolled={(facility) => {
          setCreating(false)
          void load()
          onOpenFacility(facility.organization_id)
        }}
      />
    )
  }

  if (open) {
    return (
      <FacilityDetail
        facility={open}
        client={client}
        onBack={() => onOpenFacility(null)}
        onChanged={() => void load()}
      />
    )
  }

  return (
    <FacilityList
      facilities={state.facilities}
      onOpenFacility={onOpenFacility}
      onCreate={() => setCreating(true)}
    />
  )
}
