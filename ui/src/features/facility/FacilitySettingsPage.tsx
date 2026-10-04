/**
 * DHEALTH-FACILITY-PROFILE-001/ADR-074 — `Établissement`.
 *
 * The facility's own record: its name, and the timezone Hawva interprets its local wall clocks in.
 * Two fields, one of them read-only, and that is the whole screen.
 *
 * ── Why this is its own section ──
 *
 * `facility.configure`'s French description has named three subjects since before this ticket
 * existed — *"Gérer le catalogue, les modèles de résultats et la fiche de l'établissement"* — and
 * `Services et tarifs` only ever implemented the first. The third had no screen anywhere, which is
 * what `docs/NATIVE-OPERATING-PRODUCT.md` §30 recorded as the remaining content a settings section
 * would hold. ADR-071 refused a second navigation door into a subject an existing surface already
 * owns; a facility's record is a different subject from what it offers and charges, so this is a
 * first door into a new room rather than a second into an old one.
 *
 * ── The name is read-only, and that is a decision ──
 *
 * `Organization.name` is network participant identity. No governed rename semantics exist — whether
 * a facility may rename itself, and what that would mean for routing, audit and the directory other
 * facilities see, is ADR-074 Human Decision 1. So the name is shown, because an administrator
 * configuring a facility needs to know which one they are looking at, and no editor is offered.
 * Inventing rename semantics to make this form look complete is exactly what the ticket forbids.
 *
 * ── Changing the timezone is consequential, so it asks twice ──
 *
 * The warning says what actually happens and nothing more: future local times are interpreted in
 * the new zone, and stored instants are displayed in it. It does **not** say existing appointments
 * move, because they do not — `scheduled_at` is the instant staff booked under the authority in
 * effect then, and nothing rewrites it. What a reader does need to know is that an existing booking
 * will *display* at a different wall clock afterwards, which is a true and surprising consequence,
 * so it is stated plainly rather than buried.
 *
 * No free-text reason is required. ADR-074 did not establish a governed change-history store, so
 * asking for a justification nothing can store or show would be theatre.
 *
 * ── The picker is the platform's, and the server is the authority ──
 *
 * Zone names come from `Intl.supportedValuesOf('timeZone')` — the browser's own copy of the IANA
 * database, so there is no bundled list to go stale and no timezone library added for a dropdown.
 * Where that API is unavailable the field degrades to free text rather than to a hardcoded list of
 * three countries. Either way what the browser sends is a **candidate**: the server validates it
 * against its own tz database and refuses an offset like `+03:00`, which carries no daylight-saving
 * rule. A 422 from that validation is rendered as the server wrote it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ExchangeApiError, mapExchangeErrorToCode } from '../../api/exchangeClient'
import type { FacilityProfile } from '../../api/exchangeTypes'
import type { ErrorCode } from '../../api/types'
import {
  Button,
  Card,
  ErrorState,
  LoadingSkeleton,
  Select,
  TextInput,
} from '../../design-system'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { Capability, can } from '../workspace/workspaceCapabilities'
import styles from './FacilitySettingsPage.module.css'


/**
 * Every IANA zone this browser knows, or `null` where the platform cannot say.
 *
 * `null` is not "no zones" — it means fall back to a text field, because shipping a short
 * hand-written list would quietly make the product look Djibouti-only, which it is not.
 */
function supportedTimeZones(): string[] | null {
  const api = Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] }
  if (typeof api.supportedValuesOf !== 'function') return null
  try {
    const zones = api.supportedValuesOf('timeZone')
    return zones.length > 0 ? zones : null
  } catch {
    return null
  }
}

/** `Africa/Djibouti` → `Africa — Djibouti`, with the identifier still shown beside it. */
function zoneLabel(zone: string): string {
  const [area, ...rest] = zone.split('/')
  if (rest.length === 0) return zone
  return `${area} — ${rest.join('/').replace(/_/g, ' ')}`
}

interface State {
  loading: boolean
  error: ErrorCode | null
  data: FacilityProfile | null
}

export function FacilitySettingsPage() {
  const workspace = useWorkspace()
  const { client, activeOrganization } = workspace
  const organizationId = activeOrganization?.organization_id
  // The capability set the **server** resolved for this membership, never a role name.
  const mayConfigure = can(workspace.capabilities, Capability.FACILITY_CONFIGURE)

  const [state, setState] = useState<State>({ loading: true, error: null, data: null })
  const [editing, setEditing] = useState(false)
  const [candidate, setCandidate] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const zones = useMemo(() => supportedTimeZones(), [])

  const load = useCallback(() => {
    if (!client || !organizationId) return
    setState((prev) => ({ ...prev, loading: true, error: null }))
    client
      .getFacilityProfile(organizationId)
      .then((data) => {
        if (!mounted.current) return
        setState({ loading: false, error: null, data })
      })
      .catch((error: unknown) => {
        if (!mounted.current) return
        setState({
          loading: false,
          error:
            error instanceof ExchangeApiError
              ? mapExchangeErrorToCode(error.code)
              : 'NETWORK_ERROR',
          data: null,
        })
      })
  }, [client, organizationId])

  useEffect(load, [load])

  const beginEdit = () => {
    setCandidate(state.data?.timezone_name ?? '')
    setSaveError(null)
    setConfirming(false)
    setEditing(true)
  }

  const cancelEdit = () => {
    setEditing(false)
    setConfirming(false)
    setSaveError(null)
  }

  const save = () => {
    if (!client || !organizationId) return
    setBusy(true)
    setSaveError(null)
    client
      .setFacilityTimezone(organizationId, candidate)
      .then((data) => {
        if (!mounted.current) return
        setState({ loading: false, error: null, data })
        setEditing(false)
        setConfirming(false)
      })
      .catch((error: unknown) => {
        if (!mounted.current) return
        // The server's own message, not a generic one: for an invalid zone it names what was sent
        // and what shape is expected, which is what the administrator who just picked it needs.
        setSaveError(
          error instanceof ExchangeApiError && error.detail
            ? error.detail
            : "Le fuseau horaire n'a pas pu être modifié.",
        )
      })
      .finally(() => {
        if (mounted.current) setBusy(false)
      })
  }

  if (state.loading) return <LoadingSkeleton />
  if (state.error !== null || state.data === null) {
    // `ErrorState` owns the wording per code, so no message is passed -- the same contract every
    // other page here uses, which keeps one vocabulary for a failure instead of one per screen.
    return <ErrorState code={state.error ?? 'NETWORK_ERROR'} onRetry={load} />
  }

  const profile = state.data
  const unchanged = candidate.trim() === profile.timezone_name

  return (
    <Card>
      <section className={styles.panel} aria-labelledby="facility-settings-heading">
        <header className={styles.header}>
          <h2 id="facility-settings-heading" className={styles.heading}>
            Établissement
          </h2>
        </header>

        <dl className={styles.facts}>
          <div className={styles.fact}>
            <dt className={styles.label}>Nom</dt>
            <dd className={styles.value}>
              {profile.organization_name}
              {/* Said out loud rather than left as an absent button somebody reads as a bug. */}
              <span className={styles.note}>
                Le nom est géré par Hawva et ne se modifie pas ici.
              </span>
            </dd>
          </div>

          <div className={styles.fact}>
            <dt className={styles.label}>Fuseau horaire</dt>
            <dd className={styles.value}>
              <span className={styles.zone}>{profile.timezone_name}</span>
              <span className={styles.note}>
                Hawva utilise ce fuseau pour les rendez-vous et les dates locales de
                l&apos;établissement.
              </span>
              {mayConfigure && !editing ? (
                <span className={styles.actions}>
                  <Button type="button" variant="secondary" onClick={beginEdit}>
                    Changer le fuseau horaire
                  </Button>
                </span>
              ) : null}
            </dd>
          </div>
        </dl>

        {editing ? (
          <div className={styles.editor}>
            {zones === null ? (
              <TextInput
                label="Nouveau fuseau horaire"
                value={candidate}
                placeholder="Africa/Djibouti"
                onChange={(event) => {
                  setCandidate(event.target.value)
                  setConfirming(false)
                }}
              />
            ) : (
              <Select
                label="Nouveau fuseau horaire"
                value={candidate}
                onChange={(event) => {
                  setCandidate(event.target.value)
                  setConfirming(false)
                }}
              >
                {zones.map((zone) => (
                  <option key={zone} value={zone}>
                    {zoneLabel(zone)} ({zone})
                  </option>
                ))}
              </Select>
            )}

            {!confirming ? (
              <span className={styles.actions}>
                <Button type="button" onClick={() => setConfirming(true)} disabled={unchanged}>
                  Continuer
                </Button>
                <Button type="button" variant="secondary" onClick={cancelEdit}>
                  Annuler
                </Button>
              </span>
            ) : (
              <div className={styles.confirm} role="group" aria-label="Confirmer le changement">
                {/* Exactly what changes, and exactly what does not. */}
                <p className={styles.warning}>
                  Hawva interprétera les prochaines heures locales de cet établissement en{' '}
                  <strong>{candidate}</strong>, et affichera les dates et heures locales dans ce
                  fuseau.
                </p>
                <p className={styles.warning}>
                  Les rendez-vous déjà enregistrés ne sont pas déplacés : leur instant reste
                  identique. Ils s&apos;afficheront en revanche à l&apos;heure locale de ce nouveau
                  fuseau.
                </p>
                <span className={styles.actions}>
                  <Button type="button" onClick={save} disabled={busy}>
                    {busy ? 'Modification…' : 'Confirmer le changement'}
                  </Button>
                  <Button type="button" variant="secondary" onClick={cancelEdit} disabled={busy}>
                    Annuler
                  </Button>
                </span>
              </div>
            )}

            {saveError !== null ? <p className={styles.problem}>{saveError}</p> : null}
          </div>
        ) : null}
      </section>
    </Card>
  )
}
