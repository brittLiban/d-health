/**
 * DHEALTH-FACILITY-PROFILE-001/ADR-074 — `Établissement`.
 *
 * What these tests cover:
 *
 * - **the section is `facility.configure`'s and only its**: a price-setter who reaches
 *   `Services et tarifs` through the same capability disjunction does **not** reach this one;
 * - **the name is read-only**, and says so rather than leaving an absent button to be read as a bug;
 * - **the change asks twice**, and the confirmation copy states what happens *and* what does not --
 *   asserted on the text, because the whole point of the warning is that it does not claim existing
 *   appointments move;
 * - **the stored IANA identifier stays visible**, so an administrator can read back the exact value
 *   the server holds rather than only a prettified label;
 * - **the server is the authority**: a 422 is surfaced with the server's own message, and the
 *   browser's picker is a candidate source rather than a validator;
 * - the request carries only `timezone_name`, so nothing else on this screen is submittable.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError } from '../../api/exchangeClient'
import type { ExchangeClient } from '../../api/exchangeClient'
import type { ExchangeMeResponse, FacilityProfile } from '../../api/exchangeTypes'
import { StaffApp } from '../staff/StaffApp'

const DJIBOUTI = 'Africa/Djibouti'
const VANCOUVER = 'America/Vancouver'

function profile(overrides: Partial<FacilityProfile> = {}): FacilityProfile {
  return {
    organization_id: 'provider-a',
    organization_name: 'Clinique Démo DHealth',
    timezone_name: DJIBOUTI,
    updated_at: '2026-10-04T06:00:00Z',
    ...overrides,
  }
}

function me(capabilities: string[]): ExchangeMeResponse {
  return {
    identity_id: 'identity-1',
    issuer: 'issuer',
    subject: 'subject',
    display_name: 'Personnel Synthétique',
    email: null,
    platform_admin: false,
    memberships: [
      {
        organization_id: 'provider-a',
        organization_name: 'Clinique Démo DHealth',
        organization_type: 'PROVIDER',
        organization_status: 'ACTIVE',
        membership_status: 'ACTIVE',
        roles: ['ORG_ADMIN'],
        capabilities,
      },
    ],
  }
}

const CONFIGURE = ['facility.configure']
const PRICES_ONLY = ['billing.configure_prices']

function makeClient(
  capabilities: string[],
  overrides: Partial<ExchangeClient> = {},
): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(me(capabilities)),
    getFacilityProfile: vi.fn().mockResolvedValue(profile()),
    setFacilityTimezone: vi.fn().mockResolvedValue(profile({ timezone_name: VANCOUVER })),
    listAppointments: vi.fn().mockResolvedValue({
      provider_organization_id: 'provider-a',
      day: '2026-10-04',
      facility_timezone: DJIBOUTI,
      appointments: [],
    }),
    listWaitingArrivals: vi.fn().mockResolvedValue([]),
    searchPatients: vi.fn().mockResolvedValue([]),
    listLabOrders: vi.fn().mockResolvedValue([]),
    listClinicServices: vi.fn().mockResolvedValue([]),
    ...overrides,
  } as unknown as ExchangeClient
}

/** Renders `Établissement` and waits for the profile's **data**, not merely its heading. */
async function renderSettings(client: ExchangeClient): Promise<void> {
  render(<StaffApp client={client} initialSection="facility" />)
  await screen.findByRole('heading', { name: 'Établissement' })
  await screen.findByText(DJIBOUTI)
}

// ── the section belongs to facility.configure ────────────────────────────────────────────────────

describe('who reaches Établissement', () => {
  it('offers the section to a facility.configure holder', async () => {
    render(<StaffApp client={makeClient(CONFIGURE)} initialSection="today" />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Établissement' })).toBeInTheDocument(),
    )
  })

  it('does not offer it to a price-setter', async () => {
    /** `Services et tarifs` is reachable through `billing.configure_prices` **or**
     * `facility.configure`; this section is not. Somebody who may set a price has no business in
     * the facility's own record, and reaching a screen is not authority over everything on it. */
    render(<StaffApp client={makeClient(PRICES_ONLY)} initialSection="today" />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Services et tarifs' })).toBeInTheDocument(),
    )
    expect(screen.queryByRole('button', { name: 'Établissement' })).not.toBeInTheDocument()
  })
})

// ── what the screen says ─────────────────────────────────────────────────────────────────────────

describe('the facility record', () => {
  it('shows the organization name and states that it is not editable here', async () => {
    await renderSettings(makeClient(CONFIGURE))

    // Scoped to the section: the app shell header also shows the active organization's name, so an
    // unscoped query finds two and says nothing about this screen.
    const panel = screen.getByRole('region', { name: 'Établissement' })
    expect(panel).toHaveTextContent('Clinique Démo DHealth')
    // Said out loud. ADR-074 Human Decision 1 leaves facility rename ungoverned, so the absence of
    // an editor is a decision and reads as one rather than as a missing button.
    expect(screen.getByText(/ne se modifie pas ici/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Nom de l/)).not.toBeInTheDocument()
  })

  it('shows the stored IANA identifier verbatim', async () => {
    await renderSettings(makeClient(CONFIGURE))

    // Not only a prettified label: the exact value the server holds, readable back.
    expect(screen.getByText(DJIBOUTI)).toBeInTheDocument()
    expect(screen.getByText(/pour les rendez-vous et les dates locales/)).toBeInTheDocument()
  })

  it('offers no change control without facility.configure', async () => {
    const client = makeClient([], {
      getFacilityProfile: vi.fn().mockResolvedValue(profile()),
    })
    render(<StaffApp client={client} initialSection="facility" />)
    // The section is not reachable at all, so there is nothing to change -- the strongest form of
    // "cannot edit", and the server refuses the request regardless.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Établissement' })).not.toBeInTheDocument(),
    )
  })
})

// ── changing it ──────────────────────────────────────────────────────────────────────────────────

describe('changing the timezone', () => {
  it('asks twice before sending anything', async () => {
    const client = makeClient(CONFIGURE)
    await renderSettings(client)

    await userEvent.click(screen.getByRole('button', { name: 'Changer le fuseau horaire' }))
    // The first click opens an editor and sends nothing.
    expect(client.setFacilityTimezone).not.toHaveBeenCalled()

    const field = screen.getByLabelText('Nouveau fuseau horaire')
    await userEvent.selectOptions(field, VANCOUVER)
    await userEvent.click(screen.getByRole('button', { name: 'Continuer' }))
    expect(client.setFacilityTimezone).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Confirmer le changement' }))
    await waitFor(() =>
      expect(client.setFacilityTimezone).toHaveBeenCalledWith('provider-a', VANCOUVER),
    )
  })

  it('states what changes and that existing appointments do not move', async () => {
    const client = makeClient(CONFIGURE)
    await renderSettings(client)

    await userEvent.click(screen.getByRole('button', { name: 'Changer le fuseau horaire' }))
    await userEvent.selectOptions(screen.getByLabelText('Nouveau fuseau horaire'), VANCOUVER)
    await userEvent.click(screen.getByRole('button', { name: 'Continuer' }))

    // The claim that matters: the warning must not say existing appointments move, because they do
    // not. It must say their displayed local time changes, because it does.
    expect(screen.getByText(/ne sont pas déplacés/)).toBeInTheDocument()
    expect(screen.getByText(/leur instant reste/)).toBeInTheDocument()
    expect(screen.getByText(/afficheront/)).toBeInTheDocument()
  })

  it('cannot be confirmed when the zone is unchanged', async () => {
    const client = makeClient(CONFIGURE)
    await renderSettings(client)

    await userEvent.click(screen.getByRole('button', { name: 'Changer le fuseau horaire' }))
    expect(screen.getByRole('button', { name: 'Continuer' })).toBeDisabled()
  })

  it('surfaces the server refusal in the server own words', async () => {
    /** The browser's picker is a candidate source, not a validator. When the server refuses -- an
     * offset, a zone its tz database does not have -- the administrator needs the message that
     * names what was sent, not a generic failure. */
    const client = makeClient(CONFIGURE, {
      setFacilityTimezone: vi
        .fn()
        .mockRejectedValue(
          new ExchangeApiError(
            422,
            'VALIDATION_ERROR',
            "'+03:00' is not an IANA timezone identifier this server recognises.",
          ),
        ),
    })
    await renderSettings(client)

    await userEvent.click(screen.getByRole('button', { name: 'Changer le fuseau horaire' }))
    await userEvent.selectOptions(screen.getByLabelText('Nouveau fuseau horaire'), VANCOUVER)
    await userEvent.click(screen.getByRole('button', { name: 'Continuer' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirmer le changement' }))

    await waitFor(() =>
      expect(screen.getByText(/is not an IANA timezone identifier/)).toBeInTheDocument(),
    )
  })

  it('shows the new zone after a successful change', async () => {
    const client = makeClient(CONFIGURE)
    await renderSettings(client)

    await userEvent.click(screen.getByRole('button', { name: 'Changer le fuseau horaire' }))
    await userEvent.selectOptions(screen.getByLabelText('Nouveau fuseau horaire'), VANCOUVER)
    await userEvent.click(screen.getByRole('button', { name: 'Continuer' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirmer le changement' }))

    await waitFor(() => expect(screen.getByText(VANCOUVER)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Confirmer le changement' })).not.toBeInTheDocument()
  })

  it('can be abandoned without sending anything', async () => {
    const client = makeClient(CONFIGURE)
    await renderSettings(client)

    await userEvent.click(screen.getByRole('button', { name: 'Changer le fuseau horaire' }))
    await userEvent.selectOptions(screen.getByLabelText('Nouveau fuseau horaire'), VANCOUVER)
    await userEvent.click(screen.getByRole('button', { name: 'Annuler' }))

    expect(client.setFacilityTimezone).not.toHaveBeenCalled()
    expect(screen.getByText(DJIBOUTI)).toBeInTheDocument()
  })
})

// ── the picker ───────────────────────────────────────────────────────────────────────────────────

describe('the timezone picker', () => {
  it('is sourced from the platform rather than a bundled list', async () => {
    await renderSettings(makeClient(CONFIGURE))
    await userEvent.click(screen.getByRole('button', { name: 'Changer le fuseau horaire' }))

    const field = screen.getByLabelText('Nouveau fuseau horaire')
    const options = Array.from(field.querySelectorAll('option')).map((o) => o.getAttribute('value'))

    // Not a list of three countries. The product is not Djibouti-only, and this comes from the
    // browser's own copy of the IANA database, so there is nothing bundled to go stale.
    expect(options.length).toBeGreaterThan(100)
    expect(options).toContain(DJIBOUTI)
    expect(options).toContain(VANCOUVER)
    expect(options).toContain('America/New_York')
  })

  it('shows both a readable label and the exact identifier', async () => {
    await renderSettings(makeClient(CONFIGURE))
    await userEvent.click(screen.getByRole('button', { name: 'Changer le fuseau horaire' }))

    const field = screen.getByLabelText('Nouveau fuseau horaire')
    const djibouti = Array.from(field.querySelectorAll('option')).find(
      (o) => o.getAttribute('value') === DJIBOUTI,
    )
    expect(djibouti?.textContent).toContain('Africa — Djibouti')
    expect(djibouti?.textContent).toContain(DJIBOUTI)
  })
})

// ── accessibility ────────────────────────────────────────────────────────────────────────────────

describe('accessibility', () => {
  /**
   * 20s rather than the 5s default, and the reason is the subject of the test: the picker holds
   * every IANA zone the browser knows -- several hundred options -- and an axe scan over that many
   * nodes genuinely takes longer than a scan of an ordinary form. The alternative was to scan with
   * the picker closed, which would have skipped the control most worth checking.
   */
  it(
    'has no axe violations, with the editor open',
    async () => {
      const { container } = render(
        <StaffApp client={makeClient(CONFIGURE)} initialSection="facility" />,
      )
      await screen.findByText(DJIBOUTI)
      await userEvent.click(screen.getByRole('button', { name: 'Changer le fuseau horaire' }))
      await userEvent.selectOptions(screen.getByLabelText('Nouveau fuseau horaire'), VANCOUVER)
      await userEvent.click(screen.getByRole('button', { name: 'Continuer' }))

      expect(await axe(container)).toHaveNoViolations()
    },
    20_000,
  )

  it('names the facility section as a landmark', async () => {
    await renderSettings(makeClient(CONFIGURE))
    expect(screen.getByRole('region', { name: 'Établissement' })).toBeInTheDocument()
  })
})
