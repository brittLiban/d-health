/**
 * DH-MVP-ONBOARD-001 — attaching people to a facility that already exists.
 *
 * The hole these tests close: enrollment could attach a first administrator, and
 * after that there was no screen for "we hired another technician" or "the
 * administrator left". The capability was in the API the whole time, reachable
 * only with somebody's identity UUID and a REST client.
 *
 * The properties under test are about truthfulness, not plumbing. The list is
 * what the server returned, never what was ticked. Adding roles to somebody
 * already on the team is not "adding a person" and must not say so. Nothing on
 * this screen removes access, so nothing may look like it does. And because
 * staffing is a readiness condition, a successful attach has to make the facility
 * re-read its readiness -- otherwise the operator fixes "Aucun responsable
 * désigné" and the screen goes on saying it.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../api/exchangeClient'
import type {
  FacilityEnrollment,
  IdentityLookupResult,
  OrganizationMembership,
} from '../../api/exchangeTypes'
import { FacilityTeam } from './FacilityTeam'

const LAB: FacilityEnrollment = {
  organization_id: 'org-lab',
  organization_name: 'Laboratoire Central',
  organization_type: 'LAB',
  organization_status: 'ACTIVE',
  participation_mode: 'NATIVE',
  onboarding_status: 'ORGANIZATION_READY',
  integration_feasibility: null,
  existing_system_name: null,
  existing_system_vendor: null,
  existing_system_version: null,
  hosting_model: null,
  technical_contact: null,
  vendor_contact: null,
  documentation_reference: null,
  patient_identifier_namespace: null,
  order_identifier_concept: null,
  report_identifier_concept: null,
  specimen_identifier_concept: null,
  exposes_source_event_timestamps: null,
  exposes_professional_release_event: null,
  notes: null,
  interfaces: [],
  open_blockers: [],
}

const CLINIC: FacilityEnrollment = {
  ...LAB,
  organization_id: 'org-clinic',
  organization_name: 'Clinique Al Rahma',
  organization_type: 'PROVIDER',
}

const AHMED: OrganizationMembership = {
  membership_id: 'mem-ahmed',
  identity_id: 'id-ahmed',
  display_name: 'Ahmed Ismail',
  email: 'ahmed@example.test',
  status: 'ACTIVE',
  roles: ['ORG_ADMIN', 'LAB_TECH', 'LAB_RESULT_RELEASER'],
}

const FATOU: OrganizationMembership = {
  membership_id: 'mem-fatou',
  identity_id: 'id-fatou',
  display_name: 'Fatou Hassan',
  email: 'fatou@example.test',
  status: 'ACTIVE',
  roles: ['LAB_TECH'],
}

const DEPARTED: OrganizationMembership = {
  membership_id: 'mem-old',
  identity_id: 'id-old',
  display_name: 'Omar Warsame',
  email: 'omar@example.test',
  status: 'INACTIVE',
  roles: ['ORG_ADMIN'],
}

const NEWCOMER: IdentityLookupResult = {
  identity_id: 'id-new',
  display_name: 'Khadija Aden',
  email: 'khadija@example.test',
}

interface Setup {
  /** Successive answers from the list endpoint, so a refresh can differ. */
  memberships?: OrganizationMembership[][]
  lookup?: IdentityLookupResult[]
  createMembershipWithOutcome?: ReturnType<typeof vi.fn>
}

function client(setup: Setup = {}) {
  const pages = setup.memberships ?? [[]]
  let call = 0
  return {
    listMemberships: vi.fn(async () => {
      const page = pages[Math.min(call, pages.length - 1)]
      call += 1
      return page
    }),
    lookupIdentities: vi.fn().mockResolvedValue(setup.lookup ?? [NEWCOMER]),
    createMembershipWithOutcome:
      setup.createMembershipWithOutcome ??
      vi.fn().mockResolvedValue({
        membership: {
          membership_id: 'mem-new',
          identity_id: NEWCOMER.identity_id,
          display_name: NEWCOMER.display_name,
          email: NEWCOMER.email,
          status: 'ACTIVE',
          roles: ['LAB_TECH'],
        },
        created: true,
      }),
  } as unknown as ExchangeClient & {
    listMemberships: ReturnType<typeof vi.fn>
    lookupIdentities: ReturnType<typeof vi.fn>
    createMembershipWithOutcome: ReturnType<typeof vi.fn>
  }
}

function renderTeam(c: ExchangeClient, facility = LAB) {
  const onChanged = vi.fn()
  render(<FacilityTeam facility={facility} client={c} onChanged={onChanged} />)
  return { onChanged }
}

/** Search for somebody and select them. The panel must already be open. */
async function pick(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.type(screen.getByLabelText('Rechercher une personne'), name.slice(0, 5))
  await user.click(screen.getByRole('button', { name: 'Rechercher' }))
  await user.click(await screen.findByRole('button', { name: new RegExp(name) }))
}

describe('FacilityTeam', () => {
  it('FT1 lists each person with their roles in French and their access status', async () => {
    renderTeam(client({ memberships: [[AHMED, FATOU, DEPARTED]] }))

    const rows = await screen.findAllByRole('listitem')
    expect(within(rows[0]).getByText('Ahmed Ismail')).toBeInTheDocument()
    expect(
      within(rows[0]).getByText('Administration · Analyse · Libération'),
    ).toBeInTheDocument()
    expect(within(rows[0]).getByText('Actif')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Analyse')).toBeInTheDocument()
    // The departed administrator is still listed. Losing the record would be
    // worse than showing an access that is switched off.
    expect(within(rows[2]).getByText('Omar Warsame')).toBeInTheDocument()
    expect(within(rows[2]).getByText('Inactif')).toBeInTheDocument()
  })

  it('FT2 never shows a raw role enum or an identity UUID', async () => {
    const { container } = render(
      <FacilityTeam facility={LAB} client={client({ memberships: [[AHMED]] })} onChanged={vi.fn()} />,
    )
    await screen.findByText('Ahmed Ismail')
    const text = container.textContent ?? ''
    expect(text).not.toContain('LAB_RESULT_RELEASER')
    expect(text).not.toContain('ORG_ADMIN')
    expect(text).not.toContain('id-ahmed')
    expect(text).not.toContain('mem-ahmed')
  })

  it('FT3 says plainly when nobody is attached, rather than showing an empty list', async () => {
    renderTeam(client({ memberships: [[]] }))
    expect(await screen.findByText(/Personne n'est rattaché/)).toBeInTheDocument()
  })

  it('FT4 attaches a new person with the roles chosen, then re-reads the team and readiness', async () => {
    const user = userEvent.setup()
    const create = vi.fn().mockResolvedValue({
      membership: {
        membership_id: 'mem-new',
        identity_id: NEWCOMER.identity_id,
        display_name: 'Khadija Aden',
        email: NEWCOMER.email,
        status: 'ACTIVE',
        roles: ['LAB_TECH'],
      },
      created: true,
    })
    const c = client({
      memberships: [[FATOU], [FATOU, { ...FATOU, membership_id: 'mem-new', identity_id: NEWCOMER.identity_id, display_name: 'Khadija Aden' }]],
      createMembershipWithOutcome: create,
    })
    const { onChanged } = renderTeam(c)

    await user.click(await screen.findByRole('button', { name: 'Ajouter une personne' }))
    await pick(user, 'Khadija Aden')
    await user.click(screen.getByRole('checkbox', { name: /Analyse/ }))
    await user.click(screen.getByRole('button', { name: 'Ajouter la personne' }))

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith('org-lab', {
        identity_id: 'id-new',
        roles: ['LAB_TECH'],
      }),
    )
    expect(await screen.findByText(/Khadija Aden fait maintenant partie de l'équipe/)).toBeInTheDocument()
    // Two reads: the first render and the refresh. The screen shows the server's
    // answer, not the row it could have appended locally.
    expect(c.listMemberships).toHaveBeenCalledTimes(2)
    // Staffing is a readiness condition -- attaching somebody is exactly what
    // clears "Aucun responsable désigné", so the parent must re-evaluate.
    expect(onChanged).toHaveBeenCalled()
  })

  it('FT5 adding roles to somebody already on the team is not described as adding a person', async () => {
    const user = userEvent.setup()
    const create = vi.fn().mockResolvedValue({
      membership: { ...FATOU, roles: ['LAB_TECH', 'LAB_RESULT_RELEASER'] },
      created: false,
    })
    const c = client({
      memberships: [[FATOU]],
      lookup: [{ identity_id: FATOU.identity_id, display_name: 'Fatou Hassan', email: FATOU.email }],
      createMembershipWithOutcome: create,
    })
    renderTeam(c)

    await user.click(await screen.findByRole('button', { name: 'Ajouter une personne' }))
    await pick(user, 'Fatou Hassan')

    // The role she holds is ticked and cannot be unticked: this screen adds
    // authority and never removes it.
    const held = screen.getByRole('checkbox', { name: /Analyse/ })
    expect(held).toBeChecked()
    expect(held).toBeDisabled()

    await user.click(screen.getByRole('checkbox', { name: /Libération/ }))
    await user.click(screen.getByRole('button', { name: 'Ajouter les rôles' }))

    await waitFor(() =>
      // Only the new role. Re-sending the held one would be harmless on the
      // server and would still misstate the request as "her roles are now these".
      expect(create).toHaveBeenCalledWith('org-lab', {
        identity_id: 'id-fatou',
        roles: ['LAB_RESULT_RELEASER'],
      }),
    )
    const message = await screen.findByRole('status')
    expect(message).toHaveTextContent('Rôles ajoutés pour Fatou Hassan.')
    expect(message).not.toHaveTextContent(/fait maintenant partie/)
  })

  it('FT6 offers no way to attach roles to an inactive membership, and says why', async () => {
    const user = userEvent.setup()
    const create = vi.fn()
    const c = client({
      memberships: [[DEPARTED]],
      lookup: [
        { identity_id: DEPARTED.identity_id, display_name: 'Omar Warsame', email: DEPARTED.email },
      ],
      createMembershipWithOutcome: create,
    })
    renderTeam(c)

    await user.click(await screen.findByRole('button', { name: 'Ajouter une personne' }))
    await pick(user, 'Omar Warsame')
    await user.click(screen.getByRole('checkbox', { name: /Analyse/ }))

    expect(screen.getByRole('note')).toHaveTextContent(/n'a plus accès à cet établissement/)
    expect(screen.getByRole('note')).toHaveTextContent(/inactif/i)
    expect(screen.getByRole('button', { name: 'Ajouter les rôles' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Ajouter les rôles' }))
    expect(create).not.toHaveBeenCalled()
  })

  it('FT6b a revoked person is not described as being on the team', async () => {
    const user = userEvent.setup()
    const revoked = { ...DEPARTED, status: 'REVOKED', display_name: 'Omar Warsame' }
    renderTeam(
      client({
        memberships: [[revoked]],
        lookup: [
          { identity_id: revoked.identity_id, display_name: 'Omar Warsame', email: revoked.email },
        ],
      }),
    )

    await user.click(await screen.findByRole('button', { name: 'Ajouter une personne' }))
    await user.type(screen.getByLabelText('Rechercher une personne'), 'Omar')
    await user.click(screen.getByRole('button', { name: 'Rechercher' }))

    const hit = await screen.findByRole('button', { name: /Omar Warsame/ })
    // "Déjà dans l'équipe" would say their access is live. It is not.
    expect(hit).not.toHaveTextContent(/Déjà dans l'équipe/)
    expect(hit).toHaveTextContent('Accès retiré')
  })

  it('FT7 renders the server refusal in its own words and claims nothing succeeded', async () => {
    const user = userEvent.setup()
    const create = vi.fn().mockRejectedValue(
      new ExchangeApiError(
        409,
        'CONFLICT' as never,
        'membership exists but is inactive; reactivation is not supported here',
      ),
    )
    const { onChanged } = renderTeam(
      client({ memberships: [[FATOU]], createMembershipWithOutcome: create }),
    )

    await user.click(await screen.findByRole('button', { name: 'Ajouter une personne' }))
    await pick(user, 'Khadija Aden')
    await user.click(screen.getByRole('checkbox', { name: /Analyse/ }))
    await user.click(screen.getByRole('button', { name: 'Ajouter la personne' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'membership exists but is inactive; reactivation is not supported here',
    )
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('FT8 offers only the roles a facility of this type can grant', async () => {
    const user = userEvent.setup()
    renderTeam(client({ memberships: [[]] }), CLINIC)

    await user.click(await screen.findByRole('button', { name: 'Ajouter une personne' }))
    await pick(user, 'Khadija Aden')

    expect(screen.getByRole('checkbox', { name: /Clinicien/ })).toBeInTheDocument()
    // Not a gate -- the server refuses an illegal pairing regardless -- but
    // offering a laboratory role on a clinic would invite a guaranteed refusal.
    expect(screen.queryByRole('checkbox', { name: /Libération/ })).not.toBeInTheDocument()
  })

  it('FT9 cancelling sends nothing', async () => {
    const user = userEvent.setup()
    const create = vi.fn()
    const c = client({ memberships: [[FATOU]], createMembershipWithOutcome: create })
    renderTeam(c)

    await user.click(await screen.findByRole('button', { name: 'Ajouter une personne' }))
    await pick(user, 'Khadija Aden')
    await user.click(screen.getByRole('checkbox', { name: /Analyse/ }))
    await user.click(screen.getByRole('button', { name: 'Annuler' }))

    expect(create).not.toHaveBeenCalled()
    expect(c.listMemberships).toHaveBeenCalledTimes(1)
    expect(screen.queryByLabelText('Rechercher une personne')).not.toBeInTheDocument()
  })

  it('FT10 shows no control that would remove a role or an access', async () => {
    renderTeam(client({ memberships: [[AHMED, DEPARTED]] }))
    await screen.findByText('Ahmed Ismail')
    for (const label of [/Retirer/i, /Supprimer/i, /Désactiver/i, /Révoquer/i, /Réactiver/i]) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument()
    }
  })

  it('FT11 a failed load offers a retry instead of an empty team', async () => {
    const user = userEvent.setup()
    const c = {
      listMemberships: vi
        .fn()
        .mockRejectedValueOnce(new Error('boom'))
        .mockResolvedValue([FATOU]),
    } as unknown as ExchangeClient
    renderTeam(c)

    expect(await screen.findByRole('alert')).toHaveTextContent(/n'a pas pu être chargée/)
    // Never the empty-team sentence on a failure: "nobody is attached" and "we
    // could not find out" are different facts.
    expect(screen.queryByText(/Personne n'est rattaché/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Réessayer' }))
    expect(await screen.findByText('Fatou Hassan')).toBeInTheDocument()
  })
})
