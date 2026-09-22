/**
 * DH-MVP-ONBOARD-001 correction — the first facility administrator.
 *
 * The defect these tests close: the wizard let an operator search a person,
 * select them, and tick roles, and then created the facility alone. The screen
 * offered an action the product did not perform, which is the one failure this
 * whole ticket was written against.
 *
 * Two properties are pinned here. Selecting somebody grants nothing until the
 * enrollment is actually submitted — a search result is a name, not access. And
 * the completion screen reports attachment from the server's response, never from
 * its own selection state, so it cannot claim an administrator that no membership
 * backs.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../api/exchangeClient'
import type { FacilityEnrollmentCreated, IdentityLookupResult } from '../../api/exchangeTypes'
import { NewFacilityWizard } from './NewFacilityWizard'

const AHMED: IdentityLookupResult = {
  identity_id: '11111111-2222-3333-4444-555555555555',
  display_name: 'Ahmed Osman',
  email: 'ahmed@example.test',
}

function created(
  overrides: Partial<FacilityEnrollmentCreated> = {},
): FacilityEnrollmentCreated {
  return {
    organization_id: 'org-new',
    organization_name: 'Laboratoire Central',
    organization_type: 'LAB',
    organization_status: 'ACTIVE',
    participation_mode: 'NATIVE',
    onboarding_status: 'DRAFT',
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
    first_administrator: null,
    ...overrides,
  }
}

function client(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    lookupIdentities: vi.fn().mockResolvedValue([AHMED]),
    enrolFacility: vi.fn().mockResolvedValue(created()),
    ...overrides,
  } as unknown as ExchangeClient & {
    enrolFacility: ReturnType<typeof vi.fn>
    lookupIdentities: ReturnType<typeof vi.fn>
  }
}

/** Drive the wizard to the review step, optionally picking Ahmed on the way. */
async function walkToReview(
  user: ReturnType<typeof userEvent.setup>,
  {
    type = "Laboratoire d'analyses",
    name = 'Laboratoire Central',
    selectPerson = true,
    roles = ['Analyse', 'Libération'],
  }: { type?: string; name?: string; selectPerson?: boolean; roles?: string[] } = {},
) {
  await user.type(screen.getByLabelText(/Nom de l'établissement/), name)
  await user.click(screen.getByRole('radio', { name: type }))
  await user.click(screen.getByRole('button', { name: 'Continuer' }))

  await user.click(screen.getByRole('radio', { name: /Utiliser DHealth/ }))
  await user.click(screen.getByRole('button', { name: 'Continuer' }))

  if (selectPerson) {
    await user.type(screen.getByLabelText(/Rechercher/), 'Ahmed')
    await user.click(screen.getByRole('button', { name: /Rechercher/ }))
    await user.click(await screen.findByRole('button', { name: /Ahmed Osman/ }))
    for (const role of roles) {
      await user.click(screen.getByRole('checkbox', { name: role }))
    }
  }
  await user.click(screen.getByRole('button', { name: 'Continuer' }))
}

describe('the first facility administrator', () => {
  it('FW1: creating a laboratory attaches the selected person with the selected roles', async () => {
    const user = userEvent.setup()
    const c = client({
      enrolFacility: vi.fn().mockResolvedValue(
        created({
          first_administrator: {
            identity_id: AHMED.identity_id,
            membership_id: 'mem-1',
            display_name: 'Ahmed Osman',
            roles: ['LAB_RESULT_RELEASER', 'LAB_TECH', 'ORG_ADMIN'],
          },
        }),
      ),
    })
    render(<NewFacilityWizard client={c} onCancel={() => {}} onEnrolled={() => {}} />)

    await walkToReview(user)
    await user.click(screen.getByRole('button', { name: /Enregistrer l'établissement/ }))

    await waitFor(() => expect(c.enrolFacility).toHaveBeenCalled())
    const body = c.enrolFacility.mock.calls[0][0]
    expect(body.organization_type).toBe('LAB')
    expect(body.first_administrator.identity_id).toBe(AHMED.identity_id)
    // ORG_ADMIN is pre-selected; the two laboratory authorities were ticked.
    expect([...body.first_administrator.roles].sort()).toEqual([
      'LAB_RESULT_RELEASER',
      'LAB_TECH',
      'ORG_ADMIN',
    ])
  })

  it('FW2: creating a provider sends the clinician role', async () => {
    const user = userEvent.setup()
    const c = client()
    render(<NewFacilityWizard client={c} onCancel={() => {}} onEnrolled={() => {}} />)

    await walkToReview(user, {
      type: 'Établissement de soins',
      name: 'Clinique Al Rahma',
      roles: ['Clinicien'],
    })
    await user.click(screen.getByRole('button', { name: /Enregistrer l'établissement/ }))

    await waitFor(() => expect(c.enrolFacility).toHaveBeenCalled())
    const body = c.enrolFacility.mock.calls[0][0]
    expect(body.organization_type).toBe('PROVIDER')
    expect([...body.first_administrator.roles].sort()).toEqual(['CLINICIAN', 'ORG_ADMIN'])
  })

  it('FW3: the completion screen reports attachment from the server, not from the selection', async () => {
    // The sharp case. The operator selected Ahmed and the server, for whatever
    // reason, created no membership. Claiming "rattaché" here would be the
    // original defect wearing a confirmation message.
    const user = userEvent.setup()
    const c = client({
      enrolFacility: vi.fn().mockResolvedValue(created({ first_administrator: null })),
    })
    render(<NewFacilityWizard client={c} onCancel={() => {}} onEnrolled={() => {}} />)

    await walkToReview(user)
    await user.click(screen.getByRole('button', { name: /Enregistrer l'établissement/ }))

    expect(await screen.findByText(/est enregistré/)).toBeInTheDocument()
    expect(screen.getByText(/Aucun responsable n'est rattaché/)).toBeInTheDocument()
    expect(screen.queryByText(/Ahmed Osman est rattaché/)).toBeNull()
  })

  it('FW4: a completed attachment names the person and the roles the server persisted', async () => {
    const user = userEvent.setup()
    const c = client({
      enrolFacility: vi.fn().mockResolvedValue(
        created({
          first_administrator: {
            identity_id: AHMED.identity_id,
            membership_id: 'mem-1',
            display_name: 'Ahmed Osman',
            // Deliberately fewer roles than were ticked: the screen reports the
            // grant, not the request.
            roles: ['ORG_ADMIN'],
          },
        }),
      ),
    })
    render(<NewFacilityWizard client={c} onCancel={() => {}} onEnrolled={() => {}} />)

    await walkToReview(user)
    await user.click(screen.getByRole('button', { name: /Enregistrer l'établissement/ }))

    expect(await screen.findByText(/Ahmed Osman est rattaché/)).toBeInTheDocument()
    expect(screen.getByText(/Administration/)).toBeInTheDocument()
    expect(screen.queryByText(/Libération/)).toBeNull()
  })

  it('FW5: finding somebody and then cancelling grants nothing', async () => {
    // A search result is a name. Nothing is created until the enrollment is
    // submitted, and abandoning the wizard must leave no trace of the selection.
    const user = userEvent.setup()
    const c = client()
    const onCancel = vi.fn()
    render(<NewFacilityWizard client={c} onCancel={onCancel} onEnrolled={() => {}} />)

    await user.type(screen.getByLabelText(/Nom de l'établissement/), 'Laboratoire Abandonné')
    await user.click(screen.getByRole('button', { name: 'Continuer' }))
    await user.click(screen.getByRole('radio', { name: /Utiliser DHealth/ }))
    await user.click(screen.getByRole('button', { name: 'Continuer' }))
    await user.type(screen.getByLabelText(/Rechercher/), 'Ahmed')
    await user.click(screen.getByRole('button', { name: /Rechercher/ }))
    await user.click(await screen.findByRole('button', { name: /Ahmed Osman/ }))

    await user.click(screen.getByRole('button', { name: 'Annuler' }))

    expect(onCancel).toHaveBeenCalled()
    expect(c.enrolFacility).not.toHaveBeenCalled()
  })

  it('FW6: the operator never handles a raw identifier', async () => {
    // No uuid is shown, typed, or copied anywhere in this flow: the operator
    // recognises a person by name and email, and the id travels only in the
    // request body.
    const user = userEvent.setup()
    const c = client()
    render(<NewFacilityWizard client={c} onCancel={() => {}} onEnrolled={() => {}} />)

    await walkToReview(user)
    expect(screen.queryByText(AHMED.identity_id)).toBeNull()

    await user.click(screen.getByRole('button', { name: /Enregistrer l'établissement/ }))
    await waitFor(() => expect(c.enrolFacility).toHaveBeenCalled())
    expect(c.enrolFacility.mock.calls[0][0].first_administrator.identity_id).toBe(
      AHMED.identity_id,
    )
    expect(screen.queryByText(AHMED.identity_id)).toBeNull()
  })

  it('FW7: a server refusal is shown in the server’s own words, and nothing was created', async () => {
    // Role legality belongs to the backend. The wizard does not pre-empt it, and
    // when the server refuses, it says so and states that no facility exists.
    const user = userEvent.setup()
    const c = client({
      enrolFacility: vi
        .fn()
        .mockRejectedValue(
          new ExchangeApiError(
            422,
            'VALIDATION_ERROR',
            "role 'LAB_TECH' is not valid for a 'PROVIDER' organization",
          ),
        ),
    })
    const onEnrolled = vi.fn()
    render(<NewFacilityWizard client={c} onCancel={() => {}} onEnrolled={onEnrolled} />)

    await walkToReview(user, {
      type: 'Établissement de soins',
      name: 'Clinique Invalide',
      roles: ['Clinicien'],
    })
    await user.click(screen.getByRole('button', { name: /Enregistrer l'établissement/ }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/n'est pas encore rattaché|not valid for a 'PROVIDER'/)
    expect(alert.textContent).toMatch(/Aucun établissement n'a été créé/)
    expect(onEnrolled).not.toHaveBeenCalled()
    expect(screen.queryByText(/est enregistré/)).toBeNull()
  })

  it('FW8: enrolling without selecting anybody sends no administrator', async () => {
    const user = userEvent.setup()
    const c = client()
    render(<NewFacilityWizard client={c} onCancel={() => {}} onEnrolled={() => {}} />)

    await walkToReview(user, { selectPerson: false })
    expect(screen.getByText(/Aucun responsable ne sera rattaché/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Enregistrer l'établissement/ }))
    await waitFor(() => expect(c.enrolFacility).toHaveBeenCalled())
    expect(c.enrolFacility.mock.calls[0][0].first_administrator).toBeUndefined()
  })
})
