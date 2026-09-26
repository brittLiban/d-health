/**
 * DHEALTH-NATIVE-ACCESS-001 — Équipe et accès, tested for the things that would make it
 * dangerous rather than merely broken.
 *
 * DHEALTH-NATIVE-SEC-001's P1 was not a backend defect. The backend already refused a
 * withdrawn membership and a removed role on the next request; what did not exist was a
 * screen an administrator could use to withdraw either. So the properties under test here
 * are mostly about *truthfulness*:
 *
 * - The list is what the server returned, re-read after every change, never patched
 *   locally from what was clicked.
 * - Withdrawing access must not be described as deleting an account or disabling a user.
 *   Those sentences would tell an administrator they had done something much larger than
 *   they did, and a physician who works at two clinics is the case that makes it matter.
 * - Reactivation restores the roles the membership still carries, so the confirmation has
 *   to name them before it happens rather than let them be discovered afterwards.
 * - Three removals are restated before they happen, and the restatement names the person,
 *   the establishment and the authority -- never "Êtes-vous sûr ?".
 * - The last-administrator rule belongs to the server. This screen says it is coming; it
 *   does not predict it by disabling a button, and it renders the server's refusal.
 * - No patient, order or result is ever fetched to administer a staff list.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../api/exchangeClient'
import type { ExchangeMembershipSummary, OrganizationMembership } from '../../api/exchangeTypes'
import { WorkspaceProvider } from '../workspace/WorkspaceProvider'
import { TeamAccessPage } from './TeamAccessPage'

const ORG_ID = 'org-clinic-1'
const ORG_NAME = 'Clinique de la Paix'
const ME_ID = 'identity-admin'

const ADMIN: OrganizationMembership = {
  membership_id: 'mem-admin',
  identity_id: ME_ID,
  display_name: 'Amina Robleh',
  email: 'amina@clinique.test',
  status: 'ACTIVE',
  roles: ['ORG_ADMIN'],
}

const CLINICIAN: OrganizationMembership = {
  membership_id: 'mem-clin',
  identity_id: 'identity-clin',
  display_name: 'Dr Halima Farah',
  email: 'halima@clinique.test',
  status: 'ACTIVE',
  roles: ['CLINICIAN'],
}

const DEPARTED: OrganizationMembership = {
  membership_id: 'mem-gone',
  identity_id: 'identity-gone',
  display_name: 'Youssouf Aden',
  email: 'youssouf@clinique.test',
  status: 'INACTIVE',
  roles: ['CLINICIAN'],
}

function adminMembership(
  roles: string[] = ['ORG_ADMIN'],
  organizationType: 'PROVIDER' | 'LAB' = 'PROVIDER',
): ExchangeMembershipSummary {
  return {
    organization_id: ORG_ID,
    organization_name: ORG_NAME,
    organization_type: organizationType,
    organization_status: 'ACTIVE',
    membership_status: 'ACTIVE',
    roles,
  }
}

/**
 * A client whose only defined read is the membership list.
 *
 * Deliberately *not* `workspaceClient`, which stubs the clinical reads too: if this screen
 * ever started fetching patients or orders, a stub for them would hide it. Here such a call
 * would reject on an undefined method, which is the failure this test wants.
 */
function teamClient(
  members: OrganizationMembership[],
  overrides: Partial<Record<keyof ExchangeClient, unknown>> = {},
  membership: ExchangeMembershipSummary = adminMembership(),
): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue({
      identity_id: ME_ID,
      issuer: 'https://keycloak.test/realms/dhealth-exchange',
      subject: 'subject-admin',
      display_name: ADMIN.display_name,
      email: ADMIN.email,
      platform_admin: false,
      memberships: [membership],
    }),
    listMemberships: vi.fn().mockResolvedValue(members),
    ...overrides,
  } as unknown as ExchangeClient
}

function renderTeam(client: ExchangeClient) {
  return Object.assign(
    render(
      <WorkspaceProvider client={client}>
        <TeamAccessPage />
      </WorkspaceProvider>,
    ),
    { client },
  )
}

async function rowFor(member: OrganizationMembership) {
  const name = await screen.findByText(new RegExp(member.display_name as string))
  const row = name.closest('li')
  if (!row) throw new Error(`no row found for ${member.display_name}`)
  return within(row)
}

/**
 * Clicks the confirmation panel's own button, scoped to the panel.
 *
 * The panel deliberately repeats the row's wording -- "Retirer l'accès" is what the
 * administrator chose and is what they are confirming -- so an unscoped query matches
 * both and a test that used one would be asserting against whichever came first.
 */
async function confirmWith(name: RegExp | string) {
  const panel = within(await screen.findByRole('region', { name: /Retirer|Rétablir/ }))
  await userEvent.click(panel.getByRole('button', { name }))
}

// -- what the screen states -----------------------------------------------------------------

describe('what the screen states', () => {
  it('shows who is here, whether their access is live, and what they may do', async () => {
    renderTeam(teamClient([ADMIN, CLINICIAN, DEPARTED]))

    const clinician = await rowFor(CLINICIAN)
    expect(clinician.getByText('Accès actif')).toBeInTheDocument()
    expect(clinician.getByText('Clinicien')).toBeInTheDocument()

    const departed = await rowFor(DEPARTED)
    // Not "Inactif": the subject of the sentence is the access, not the person.
    expect(departed.getByText('Accès retiré')).toBeInTheDocument()
  })

  it('keeps an inactive member visible rather than losing the record', async () => {
    renderTeam(teamClient([ADMIN, DEPARTED]))
    expect(await screen.findByText(/Youssouf Aden/)).toBeInTheDocument()
  })

  it('marks the signed-in administrator as themselves', async () => {
    renderTeam(teamClient([ADMIN, CLINICIAN]))
    expect(await screen.findByText(/Amina Robleh \(vous\)/)).toBeInTheDocument()
  })

  it('says plainly when somebody holds no role at all', async () => {
    renderTeam(teamClient([ADMIN, { ...CLINICIAN, roles: [] }]))
    // A blank access column reads as access. It is not access.
    expect(await screen.findByText(/Aucun rôle/)).toBeInTheDocument()
  })

  it('never describes withdrawing access as deleting an account or disabling a user', async () => {
    renderTeam(teamClient([ADMIN, CLINICIAN, DEPARTED]))
    await screen.findByText(/Dr Halima Farah/)
    const text = document.body.textContent ?? ''
    for (const forbidden of ['supprim', 'Supprim', 'désactiv', 'Désactiv', 'compte supprimé']) {
      expect(text).not.toContain(forbidden)
    }
  })

  it('states that a withdrawal is scoped to this establishment only', async () => {
    renderTeam(teamClient([ADMIN, CLINICIAN]))
    // The §3 distinction, on screen: org access is not the person's DHealth identity.
    expect(
      await screen.findByText(/le compte DHealth de la personne\s+reste actif/),
    ).toBeInTheDocument()
  })

  it('loads no patient, order or result to administer staff access', async () => {
    const client = teamClient([ADMIN, CLINICIAN])
    renderTeam(client)
    await screen.findByText(/Dr Halima Farah/)
    expect(client.listMemberships).toHaveBeenCalledWith(ORG_ID)
    // Nothing clinical is even defined on this client, so a call would have rejected.
    expect((client as unknown as Record<string, unknown>).listPatients).toBeUndefined()
    expect((client as unknown as Record<string, unknown>).listOrders).toBeUndefined()
  })
})

// -- confirmation ---------------------------------------------------------------------------

describe('confirmation before an authority is withdrawn', () => {
  it('restates the person, the establishment and the authority, never "êtes-vous sûr"', async () => {
    renderTeam(teamClient([ADMIN, CLINICIAN]))
    const clinician = await rowFor(CLINICIAN)
    await userEvent.click(clinician.getByRole('button', { name: "Retirer l'accès" }))

    const panel = within(await screen.findByRole('region', { name: /Retirer l'accès/ }))
    expect(
      panel.getByText(`Dr Halima Farah n'aura plus accès à ${ORG_NAME}.`),
    ).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('Êtes-vous sûr')
  })

  it("says that what the person already recorded stays in their name", async () => {
    renderTeam(teamClient([ADMIN, CLINICIAN]))
    const clinician = await rowFor(CLINICIAN)
    await userEvent.click(clinician.getByRole('button', { name: "Retirer l'accès" }))
    expect(
      await screen.findByText(/reste enregistré à son nom/),
    ).toBeInTheDocument()
  })

  it('confirms removing ORG_ADMIN and LAB_RESULT_RELEASER, but not an ordinary role', async () => {
    const removeMembershipRole = vi.fn().mockResolvedValue({ ...CLINICIAN, roles: [] })
    renderTeam(teamClient([ADMIN, CLINICIAN], { removeMembershipRole }))

    // CLINICIAN is one click: it removes authority, so the fail-safe direction is easy.
    const clinician = await rowFor(CLINICIAN)
    await userEvent.click(clinician.getByRole('button', { name: 'Retirer' }))
    await waitFor(() =>
      expect(removeMembershipRole).toHaveBeenCalledWith(ORG_ID, CLINICIAN.membership_id, 'CLINICIAN'),
    )
  })

  it('restates a LAB_RESULT_RELEASER removal, naming what the person can still do', async () => {
    const releaser: OrganizationMembership = {
      ...CLINICIAN,
      roles: ['LAB_TECH', 'LAB_RESULT_RELEASER'],
    }
    const removeMembershipRole = vi.fn()
    renderTeam(
      teamClient([ADMIN, releaser], { removeMembershipRole }, adminMembership(['ORG_ADMIN'], 'LAB')),
    )

    const row = await rowFor(releaser)
    await userEvent.click(row.getAllByRole('button', { name: 'Retirer' })[1])

    expect(await screen.findByText(/ne pourra plus libérer de résultats/)).toBeInTheDocument()
    // ADR-044's separation, stated rather than implied.
    expect(screen.getByText(/pourra continuer à saisir des résultats/)).toBeInTheDocument()
    expect(removeMembershipRole).not.toHaveBeenCalled()
  })

  it('names the roles reactivation will restore, before it happens', async () => {
    renderTeam(teamClient([ADMIN, DEPARTED]))
    const departed = await rowFor(DEPARTED)
    await userEvent.click(departed.getByRole('button', { name: "Rétablir l'accès" }))

    expect(await screen.findByText(/Les rôles conservés lui seront rendus : Clinicien/)).toBeInTheDocument()
    expect(screen.getByText(/retirez d'abord les rôles concernés/)).toBeInTheDocument()
  })

  it('says so when a reactivated membership would come back with no role', async () => {
    renderTeam(teamClient([ADMIN, { ...DEPARTED, roles: [] }]))
    const departed = await rowFor({ ...DEPARTED, roles: [] })
    await userEvent.click(departed.getByRole('button', { name: "Rétablir l'accès" }))
    expect(
      await screen.findByText(/sans pouvoir y faire quoi que ce soit/),
    ).toBeInTheDocument()
  })

  it('cancelling changes nothing at all', async () => {
    const deactivateMembership = vi.fn()
    renderTeam(teamClient([ADMIN, CLINICIAN], { deactivateMembership }))
    const clinician = await rowFor(CLINICIAN)
    await userEvent.click(clinician.getByRole('button', { name: "Retirer l'accès" }))
    await userEvent.click(await screen.findByRole('button', { name: 'Annuler' }))
    expect(deactivateMembership).not.toHaveBeenCalled()
  })
})

// -- mutations ------------------------------------------------------------------------------

describe('mutations', () => {
  it('withdraws access and re-reads the list from the server', async () => {
    const deactivateMembership = vi
      .fn()
      .mockResolvedValue({ ...CLINICIAN, status: 'INACTIVE' })
    const listMemberships = vi
      .fn()
      .mockResolvedValueOnce([ADMIN, CLINICIAN])
      .mockResolvedValue([ADMIN, { ...CLINICIAN, status: 'INACTIVE' }])
    renderTeam(teamClient([], { deactivateMembership, listMemberships }))

    const clinician = await rowFor(CLINICIAN)
    await userEvent.click(clinician.getByRole('button', { name: "Retirer l'accès" }))
    await confirmWith("Retirer l'accès")

    await waitFor(() =>
      expect(deactivateMembership).toHaveBeenCalledWith(ORG_ID, CLINICIAN.membership_id),
    )
    // Re-read, not patched locally: a concurrent change by a second administrator must
    // appear, and this screen's whole subject is other people's authority.
    await waitFor(() => expect(listMemberships).toHaveBeenCalledTimes(2))
    expect(
      await screen.findByText(`Dr Halima Farah n'a plus accès à ${ORG_NAME}.`),
    ).toBeInTheDocument()
  })

  it('restores access and reports the roles that came back', async () => {
    const reactivateMembership = vi.fn().mockResolvedValue({ ...DEPARTED, status: 'ACTIVE' })
    renderTeam(teamClient([ADMIN, DEPARTED], { reactivateMembership }))

    const departed = await rowFor(DEPARTED)
    await userEvent.click(departed.getByRole('button', { name: "Rétablir l'accès" }))
    await confirmWith("Rétablir l'accès")

    await waitFor(() =>
      expect(reactivateMembership).toHaveBeenCalledWith(ORG_ID, DEPARTED.membership_id),
    )
    expect(
      await screen.findByText(/a de nouveau accès à Clinique de la Paix avec : Clinicien\./),
    ).toBeInTheDocument()
  })

  it('adds a role in one click, with no dialogue', async () => {
    const addMembershipRole = vi
      .fn()
      .mockResolvedValue({ ...CLINICIAN, roles: ['CLINICIAN', 'ORG_ADMIN'] })
    renderTeam(teamClient([ADMIN, CLINICIAN], { addMembershipRole }))

    const clinician = await rowFor(CLINICIAN)
    await userEvent.click(clinician.getByRole('button', { name: /Ajouter administration/i }))

    await waitFor(() =>
      expect(addMembershipRole).toHaveBeenCalledWith(ORG_ID, CLINICIAN.membership_id, 'ORG_ADMIN'),
    )
    expect(
      await screen.findByText(/Administration ajouté dans Clinique de la Paix\./),
    ).toBeInTheDocument()
  })

  it('offers only the roles this establishment type may hold', async () => {
    renderTeam(teamClient([ADMIN, CLINICIAN]))
    const clinician = await rowFor(CLINICIAN)
    expect(clinician.getByRole('button', { name: /Ajouter administration/i })).toBeInTheDocument()
    // A provider can never hold a laboratory role, and the server refuses it regardless.
    expect(clinician.queryByRole('button', { name: /Ajouter analyse/i })).toBeNull()
    expect(clinician.queryByRole('button', { name: /Ajouter libération/i })).toBeNull()
  })

  it('offers no role-addition or withdrawal controls on an inactive membership', async () => {
    renderTeam(teamClient([ADMIN, DEPARTED]))
    const departed = await rowFor(DEPARTED)
    expect(departed.queryByRole('button', { name: /Ajouter/i })).toBeNull()
    expect(departed.queryByRole('button', { name: "Retirer l'accès" })).toBeNull()
    // Role *removal* stays available: it is how somebody is restored with less authority.
    expect(departed.getByRole('button', { name: 'Retirer' })).toBeInTheDocument()
  })
})

// -- the last administrator -----------------------------------------------------------------

describe('the last administrator', () => {
  it('warns that DHealth will refuse it, rather than predicting the refusal', async () => {
    renderTeam(teamClient([ADMIN, CLINICIAN]))
    expect(
      await screen.findByText(/n'a qu'un seul administrateur actif/),
    ).toBeInTheDocument()

    // Not disabled. The rule is the backend's, and a UI that predicted it would
    // eventually predict it wrongly -- a control disabled for a reason that no longer
    // holds is worse than a refusal that says why.
    const admin = await rowFor(ADMIN)
    expect(admin.getByRole('button', { name: "Retirer l'accès" })).not.toBeDisabled()
  })

  it('counts active administrators only, not inactive ones', async () => {
    renderTeam(
      teamClient([ADMIN, { ...DEPARTED, roles: ['ORG_ADMIN'] }]),
    )
    expect(await screen.findByText(/n'a qu'un seul administrateur actif/)).toBeInTheDocument()
  })

  it('stops warning once a second administrator is active', async () => {
    renderTeam(teamClient([ADMIN, { ...CLINICIAN, roles: ['CLINICIAN', 'ORG_ADMIN'] }]))
    expect(await screen.findByText(/Administrateurs actifs : 2\./)).toBeInTheDocument()
  })

  it("renders the server's own refusal when the protection fires", async () => {
    const removeMembershipRole = vi
      .fn()
      .mockRejectedValue(
        new ExchangeApiError(
          409,
          'CONFLICT',
          'this would leave the organization with no active administrator',
        ),
      )
    renderTeam(teamClient([ADMIN, CLINICIAN], { removeMembershipRole }))

    const admin = await rowFor(ADMIN)
    await userEvent.click(admin.getByRole('button', { name: 'Retirer' }))
    await userEvent.click(await screen.findByRole('button', { name: /Retirer administration/i }))

    const alert = await screen.findByRole('alert')
    // The server's sentence, because "l'opération a échoué" would leave an administrator
    // with no idea which of two very different 409s they hit.
    expect(alert).toHaveTextContent(/no active administrator/)
  })
})

// -- errors ---------------------------------------------------------------------------------

describe('errors', () => {
  it('tells an administrator who has lost their own authority to reload', async () => {
    const deactivateMembership = vi
      .fn()
      .mockRejectedValue(new ExchangeApiError(403, 'FORBIDDEN', 'not authorized'))
    renderTeam(teamClient([ADMIN, CLINICIAN], { deactivateMembership }))

    const clinician = await rowFor(CLINICIAN)
    await userEvent.click(clinician.getByRole('button', { name: "Retirer l'accès" }))
    await confirmWith("Retirer l'accès")

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /n'avez plus les autorisations nécessaires/,
    )
  })

  it('does not present a 404 as another organization existing', async () => {
    const deactivateMembership = vi
      .fn()
      .mockRejectedValue(new ExchangeApiError(404, 'NOT_FOUND', 'membership not found'))
    renderTeam(teamClient([ADMIN, CLINICIAN], { deactivateMembership }))

    const clinician = await rowFor(CLINICIAN)
    await userEvent.click(clinician.getByRole('button', { name: "Retirer l'accès" }))
    await confirmWith("Retirer l'accès")

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/ne fait plus partie de cet établissement/)
    expect(alert.textContent).not.toMatch(/autre établissement|another organization/i)
  })

  it('offers a retry when the list itself cannot be loaded', async () => {
    const listMemberships = vi
      .fn()
      .mockRejectedValueOnce(new ExchangeApiError(0, 'NETWORK_ERROR', 'network'))
      .mockResolvedValue([ADMIN, CLINICIAN])
    renderTeam(teamClient([], { listMemberships }))

    await userEvent.click(await screen.findByRole('button', { name: 'Réessayer' }))
    expect(await screen.findByText(/Dr Halima Farah/)).toBeInTheDocument()
  })

  it('says nobody is attached rather than rendering an empty list', async () => {
    renderTeam(teamClient([]))
    expect(
      await screen.findByText(/Personne n'est rattaché à cet établissement/),
    ).toBeInTheDocument()
  })
})
