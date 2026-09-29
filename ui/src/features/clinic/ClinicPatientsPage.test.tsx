import { StrictMode } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../api/exchangeClient'
import type {
  CatalogEntry,
  ClinicPatientSummary,
  ExchangeMeResponse,
  LabOrder,
  LabOrderCreateOutcome,
  LabOrderIntent,
  PatientCreateRequest,
  PatientDirectoryEntry,
  PatientIdentitySummary,
} from '../../api/exchangeTypes'
import {
  clearAllStoredPendingPatients,
  getStoredPendingPatient,
  setStoredPendingPatient,
} from './pendingPatientStorage'
import {
  clearAllStoredPendingOrders,
  getStoredPendingOrder,
  setStoredPendingOrder,
} from './pendingOrderStorage'
import { StaffApp } from '../staff/StaffApp'

const mockCatalogTests: CatalogEntry[] = [
  {
    id: '11111111-2222-4333-8444-555555555555',
    code: 'SYNTH-CBC',
    display_name: 'Numération Formule Sanguine (NFS)',
    version: 1,
    active: true,
    is_synthetic: true,
  },
  {
    id: '22222222-3333-4444-8555-666666666666',
    code: 'SYNTH-GLU',
    display_name: 'Glycémie à jeun',
    version: 1,
    active: true,
    is_synthetic: true,
  },
  {
    id: '33333333-4444-4555-8666-777777777777',
    code: 'SYNTH-LIP',
    display_name: 'Bilan lipidique complet',
    version: 1,
    active: true,
    is_synthetic: true,
  },
]

const patientWithName: ClinicPatientSummary = {
  patient_reference_id: '11111111-1111-4111-8111-111111111111',
  display_name: 'Patient Synthétique',
  contact_channel_type: 'PHONE',
  contact_value: '+25377000000',
  local_reference: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
}

const patientWithoutName: ClinicPatientSummary = {
  patient_reference_id: '22222222-2222-4222-8222-222222222222',
  display_name: null,
  contact_channel_type: null,
  contact_value: null,
  local_reference: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
}

const authoritativeReplayPatient: ClinicPatientSummary = {
  ...patientWithName,
  display_name: 'Nom stocké autoritatif',
  contact_value: '+25377111111',
}

// DH-IDENTITY-001: the native Patient-directory response shape. `toClinicPatientSummary()` in
// ClinicPatientsPage.tsx maps `primary_patient_reference_id -> patient_reference_id` and
// `patient_id -> local_reference` -- these fixtures are the exact preimage of
// `patientWithName`/`patientWithoutName`/`authoritativeReplayPatient` under that mapping, so every
// existing rendered-output assertion keeps working unchanged.
function toPatientDirectoryEntry(
  summary: ClinicPatientSummary,
  overrides: Partial<Pick<PatientDirectoryEntry, 'provider_organization_id' | 'date_of_birth' | 'created_at'>> = {},
): PatientDirectoryEntry {
  return {
    patient_id: summary.local_reference,
    provider_organization_id: overrides.provider_organization_id ?? 'provider-a',
    display_name: summary.display_name,
    date_of_birth: overrides.date_of_birth ?? null,
    contact_channel_type: summary.contact_channel_type,
    contact_value: summary.contact_value,
    primary_patient_reference_id: summary.patient_reference_id,
    created_at: overrides.created_at ?? new Date().toISOString(),
  }
}

const patientEntryWithName: PatientDirectoryEntry = toPatientDirectoryEntry(patientWithName)
const patientEntryWithoutName: PatientDirectoryEntry = toPatientDirectoryEntry(patientWithoutName)
const authoritativeReplayPatientEntry: PatientDirectoryEntry = toPatientDirectoryEntry(
  authoritativeReplayPatient,
)

// DH-CLINIC-005: the `PatientIdentitySummary` a LabOrder fixture carries once it embeds the
// server-resolved identity -- always in sync with `patientEntryWithName`/`patientWithName` so
// order/result fixtures below never accidentally diverge from the selected-patient fixture.
const patientIdentityForOrders: PatientIdentitySummary = {
  patient_id: patientEntryWithName.patient_id,
  display_name: patientEntryWithName.display_name,
  date_of_birth: patientEntryWithName.date_of_birth,
  contact_channel_type: patientEntryWithName.contact_channel_type,
  contact_value: patientEntryWithName.contact_value,
}

/**
 * Builds a `createPatientWithOutcome`-shaped mock outcome from a `ClinicPatientSummary` fixture --
 * the DH-IDENTITY-001-era equivalent of the old `{reference: {...}, created}` bind-outcome
 * literals every creation test used to construct by hand.
 */
function makePatientCreateOutcome(
  summary: ClinicPatientSummary,
  created: boolean,
  overrides: Partial<Pick<PatientDirectoryEntry, 'provider_organization_id' | 'date_of_birth'>> = {},
) {
  return { patient: toPatientDirectoryEntry(summary, overrides), created }
}

const me: ExchangeMeResponse = {
  identity_id: 'identity-1',
  issuer: 'issuer',
  subject: 'subject',
  display_name: 'Clinicien Synthétique',
  email: null,
  platform_admin: false,
  memberships: [
    {
      organization_id: 'provider-a',
      organization_name: 'Clinique A',
      organization_type: 'PROVIDER',
      organization_status: 'ACTIVE',
      membership_status: 'ACTIVE',
      roles: ['CLINICIAN'],
      capabilities: ['order.manage', 'patient.directory.read', 'patient.register', 'result.review', 'visit.document', 'visit.read'],
    },
  ],
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  const defaultCreateIntent = vi
    .fn()
    .mockImplementation((providerId: string, idempotencyKey: string, body: any) =>
      Promise.resolve({
        intent: {
          id: `intent-${idempotencyKey}`,
          idempotency_key: idempotencyKey,
          provider_organization_id: providerId,
          patient_reference_id: body.patient_reference_id,
          practitioner_reference_id: 'prac-clinician',
          committed_lab_order_id: null,
          visit_id: null,
          created_at: new Date().toISOString(),
          items: body.catalog_entry_ids.map((id: string) => {
            const entry = mockCatalogTests.find((c) => c.id === id)
            return {
              id: `item-${id}`,
              catalog_entry_id: id,
              catalog_code_snapshot: entry?.code ?? 'CODE',
              catalog_display_name_snapshot: entry?.display_name ?? 'Display',
            }
          }),
        },
        created: true,
      }),
    )

  const defaultCommitIntent = vi
    .fn()
    .mockImplementation((providerId: string, intentId: string) =>
      Promise.resolve({
        order: {
          id: `order-${intentId}`,
          provider_organization_id: providerId,
          patient_reference_id: patientWithName.patient_reference_id,
          practitioner_reference_id: 'prac-clinician',
          status: 'AWAITING_LAB_SELECTION',
          created_at: new Date().toISOString(),
          items: [
            {
              catalog_entry_id: mockCatalogTests[0].id,
              catalog_code_snapshot: mockCatalogTests[0].code,
              catalog_display_name_snapshot: mockCatalogTests[0].display_name,
            },
          ],
        },
        created: true,
      }),
    )

  // DH-CLINIC-006: a harmless default so ordinary 003C/patient-workflow scenarios that pass
  // through the new post-commit routing step don't need their own eligible-labs wiring just to
  // reach it -- tests that specifically exercise routing/`getOrder`-after-assign override these.
  const defaultEligibleLab = {
    lab_organization_id: 'lab-default-eligible',
    lab_organization_name: 'Laboratoire Test',
    public_address: 'Adresse de test',
    public_phone: null,
  }

  const client = {
    getMe: vi.fn().mockResolvedValue(me),
    listOrders: vi.fn().mockResolvedValue([]),
    getOrder: vi.fn(),
    getEligibleLabs: vi.fn().mockResolvedValue([defaultEligibleLab]),
    assignLab: vi.fn().mockResolvedValue({
      lab_order_id: 'unused',
      lab_organization_id: defaultEligibleLab.lab_organization_id,
      selected_at: new Date().toISOString(),
    }),
    getOrderResults: vi.fn(),
    getReleaseVersions: vi.fn(),
    createPatientWithOutcome: vi.fn(),
    listPatients: vi.fn().mockResolvedValue([]),
    searchPatients: vi.fn().mockResolvedValue([]),
    getPatientRegistration: vi.fn().mockResolvedValue(null),
    getPatientReference: vi.fn(),
    getPatient: vi.fn(),
    listCatalogTests: vi.fn().mockResolvedValue([]),
    createOrderWithOutcome: vi.fn(),
    createOrderIntentWithOutcome: defaultCreateIntent,
    getOrderIntentByIdempotencyKey: vi.fn().mockResolvedValue(null),
    getOrderIntent: vi.fn().mockResolvedValue(null),
    commitOrderIntentWithOutcome: defaultCommitIntent,
    listPatientOrders: vi.fn().mockResolvedValue([]),
    getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
    ...overrides,
  } as unknown as ExchangeClient

  // Older 003/003A scenarios use this local test shorthand. Keep their behavioural
  // assertions (selection and outcome) while executing the production Option-A call
  // sequence: registration returns a durable intent, then only commit returns the order.
  const legacyCreate = overrides.createOrderWithOutcome as unknown as
    | ((providerId: string, key: string, body: unknown) => Promise<LabOrderCreateOutcome>)
    | undefined
  if (legacyCreate) {
    let legacyOutcome: LabOrderCreateOutcome | undefined
    client.createOrderIntentWithOutcome = vi.fn(async (providerId, key, body) => {
      legacyOutcome = await legacyCreate(providerId, key, body)
      return {
        created: legacyOutcome.created,
        intent: {
          id: `intent-${key}`,
          idempotency_key: key,
          provider_organization_id: providerId,
          patient_reference_id: body.patient_reference_id,
          practitioner_reference_id: 'prac-clinician',
          committed_lab_order_id: null,
visit_id: null,
          created_at: new Date().toISOString(),
          items: body.catalog_entry_ids.map((catalog_entry_id: string) => {
            const entry = mockCatalogTests.find((value) => value.id === catalog_entry_id)
            return {
              catalog_entry_id,
              catalog_code_snapshot: entry?.code ?? catalog_entry_id,
              catalog_display_name_snapshot: entry?.display_name ?? 'Service diagnostique',
            }
          }),
        },
      }
    })
    client.commitOrderIntentWithOutcome = vi.fn(async () => {
      if (!legacyOutcome) throw new Error('intent must be registered before commit')
      return legacyOutcome
    })
    // DH-CLINIC-006: LabRoutingStep re-reads the order via getOrder() after a successful assign
    // (§30 -- the assign response itself carries no lab name). Tests that don't specifically
    // exercise routing/recovery and don't provide their own getOrder override get this generic,
    // harmless one: whichever order this session actually committed, now SELECTED with the same
    // default lab getEligibleLabs/assignLab already agree on.
    if (!overrides.getOrder) {
      client.getOrder = vi.fn(async (_providerId: string, orderId: string): Promise<LabOrder> => {
        if (legacyOutcome && legacyOutcome.order.id === orderId) {
          return {
            ...legacyOutcome.order,
            status: 'SELECTED' as const,
            visit_id: null,
            lab_assignment: {
              lab_organization_id: 'lab-default-eligible',
              lab_organization_name: 'Laboratoire Test',
              selected_at: new Date().toISOString(),
            },
          }
        }
        throw new Error(`no committed order matches getOrder(${orderId}) in this test`)
      })
    }
  }
  return client
}

// DH-CLINIC-006: after a successful commit, the wizard now stops at the new 'routing' step
// (§27-29) before 'confirmation' -- shared helper for tests that don't care about routing
// specifically and just need to reach the confirmation banner.
async function completeRoutingStep(userEventObj: ReturnType<typeof userEvent.setup>) {
  await screen.findByText(/n.a pas encore été envoyé à un laboratoire/)
  await userEventObj.click(await screen.findByRole('radio'))
  await userEventObj.click(screen.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }))
}

describe('Clinic native patient workflow', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    clearAllStoredPendingPatients()
    clearAllStoredPendingOrders()
    localStorage.clear()
  })

  it('accepts completed patient requests under the application Strict Mode lifecycle', async () => {
    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
    })

    render(
      <StrictMode>
        <StaffApp client={client} initialSection="patients" />
      </StrictMode>,
    )

    expect(await screen.findByText('Patient Synthétique')).toBeInTheDocument()
  })

  it('renders loading, results, opens an existing patient, and uses the truthful null-name fallback', async () => {
    let resolveList!: (patients: PatientDirectoryEntry[]) => void
    const listPromise = new Promise<PatientDirectoryEntry[]>((resolve) => { resolveList = resolve })
    const client = makeClient({ listPatients: vi.fn().mockReturnValue(listPromise) })

    render(<StaffApp client={client} initialSection="patients" />)
    expect(await screen.findByText('Chargement des patients…')).toBeInTheDocument()

    resolveList([patientEntryWithName, patientEntryWithoutName])
    expect(await screen.findByText('Patient Synthétique')).toBeInTheDocument()
    // DH-CLINIC-005 §16: a missing name never falls back to the raw technical id -- it renders
    // the truthful placeholder instead.
    expect(screen.getByText('Nom non renseigné')).toBeInTheDocument()

    const openButtons = screen.getAllByRole('button', { name: 'Ouvrir' })
    await userEvent.setup().click(openButtons[1])
    expect(await screen.findByRole('heading', { name: 'Nom non renseigné' })).toBeInTheDocument()
    // The full identity banner still makes the complete technical id available (§17).
    expect(screen.getByText(patientWithoutName.local_reference)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeEnabled()
  })

  it('shows the empty state, creates a minimal opaque reference, fetches it, and opens the workspace', async () => {
    const bind = vi.fn().mockResolvedValue(makePatientCreateOutcome(patientWithName, true))
    const get = vi.fn().mockResolvedValue(patientEntryWithName)
    const client = makeClient({
      createPatientWithOutcome: bind,
      getPatient: get,
    })

    render(<StaffApp client={client} initialSection="patients" />)
    expect(await screen.findByText('Aucun patient trouvé')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Patient Synthétique')
    await user.type(screen.getByLabelText('Téléphone (facultatif)'), '+25377000000')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
    expect(bind).toHaveBeenCalledTimes(1)
    // DH-IDENTITY-001: source_system/source_identity_system/source_identity_value are gone from
    // the create request body -- the server owns identity now, there is nothing left to assert
    // about a source-identity namespace the browser no longer sends. What the browser DOES still
    // generate is a request-identity Idempotency-Key (argument index 1), asserted below.
    const [calledProviderId, calledIdempotencyKey, submitted] = bind.mock.calls[0]
    expect(calledProviderId).toBe('provider-a')
    expect(submitted).toMatchObject({
      display_name: 'Patient Synthétique',
      contact_channel_type: 'PHONE',
      contact_value: '+25377000000',
    })
    expect(calledIdempotencyKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    )
    expect(get).toHaveBeenCalledWith('provider-a', patientEntryWithName.patient_id)
  })

  it('creates a patient with an optional exact date of birth', async () => {
    const bind = vi.fn().mockResolvedValue(makePatientCreateOutcome(patientWithName, true))
    const get = vi.fn().mockResolvedValue(patientEntryWithName)
    const client = makeClient({
      createPatientWithOutcome: bind,
      getPatient: get,
    })

    render(<StaffApp client={client} initialSection="patients" />)
    expect(await screen.findByText('Aucun patient trouvé')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Patient Avec Naissance')
    await user.type(screen.getByLabelText('Date de naissance (facultatif)'), '1990-05-14')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
    const [, , submitted] = bind.mock.calls[0]
    expect(submitted).toMatchObject({
      display_name: 'Patient Avec Naissance',
      date_of_birth: '1990-05-14',
      contact_channel_type: null,
      contact_value: null,
    })
  })

  it('submits provider-local search text and renders the returned result', async () => {
    // Mount always calls listPatients (empty search); submitting the form calls searchPatients.
    const list = vi.fn().mockResolvedValue([])
    const search = vi.fn().mockResolvedValue([patientEntryWithName])
    const client = makeClient({ listPatients: list, searchPatients: search })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    await screen.findByText('Aucun patient trouvé')
    await user.type(screen.getByLabelText('Rechercher un patient'), 'Synthétique')
    await user.click(screen.getByRole('button', { name: 'Rechercher' }))

    expect(await screen.findByText('Patient Synthétique')).toBeInTheDocument()
    expect(search).toHaveBeenLastCalledWith('provider-a', 'Synthétique', { limit: 50 })
  })

  it('reuses the original opaque identity and payload after an ambiguous response and tab navigation', async () => {
    const bind = vi
      .fn()
      .mockRejectedValueOnce(new ExchangeApiError(0, 'NETWORK_ERROR', 'raw transport detail'))
      .mockResolvedValueOnce(makePatientCreateOutcome(patientWithName, false))
    const client = makeClient({
      createPatientWithOutcome: bind,
      getPatient: vi.fn().mockResolvedValue(authoritativeReplayPatientEntry),
    })
    const user = userEvent.setup()

    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Patient Synthétique')
    await user.type(screen.getByLabelText('Téléphone (facultatif)'), '+25377000000')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText('Réessayez pour vérifier la même référence sans créer un doublon.')).toBeInTheDocument()
    expect(screen.queryByText('raw transport detail')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: "Aujourd'hui" }))
    await screen.findByRole('heading', { name: 'Accueil Clinique' })
    await user.click(screen.getByRole('button', { name: 'Patients' }))
    await screen.findByRole('heading', { name: 'Nouveau patient' })
    expect(screen.getByLabelText('Nom du patient (obligatoire)')).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(await screen.findByRole('heading', { name: 'Nom stocké autoritatif' })).toBeInTheDocument()
    expect(screen.getByText('Téléphone : +25377111111')).toBeInTheDocument()
    expect(screen.queryByText('Téléphone : +25377000000')).not.toBeInTheDocument()
    expect(bind).toHaveBeenCalledTimes(2)
    expect(bind.mock.calls[1][1]).toEqual(bind.mock.calls[0][1])
  })

  it('retains the exact pending tuple when authoritative detail retrieval fails', async () => {
    const bind = vi.fn().mockResolvedValue(makePatientCreateOutcome(patientWithName, false))
    const get = vi.fn()
      .mockRejectedValueOnce(new ExchangeApiError(403, 'FORBIDDEN', 'raw detail failure'))
      .mockResolvedValueOnce(authoritativeReplayPatientEntry)
    const client = makeClient({
      createPatientWithOutcome: bind,
      getPatient: get,
    })
    const user = userEvent.setup()

    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Nom saisi')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByRole('button', { name: 'Réessayer l’ouverture' })).toBeInTheDocument()
    expect(screen.queryByText('raw detail failure')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Réessayer l’ouverture' }))
    expect(await screen.findByRole('heading', { name: 'Nom stocké autoritatif' })).toBeInTheDocument()
    expect(bind).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledTimes(2)
  })

  it.each([
    {
      created: true,
      label: 'newly-created (created=true)',
      failureType: 'definitive (403)',
      error: new ExchangeApiError(403, 'FORBIDDEN', 'detail forbidden'),
    },
    {
      created: true,
      label: 'newly-created (created=true)',
      failureType: 'transient (503)',
      error: new ExchangeApiError(503, 'SERVER_ERROR', 'service unavailable'),
    },
    {
      created: false,
      label: 'idempotent-replay (created=false)',
      failureType: 'definitive (403)',
      error: new ExchangeApiError(403, 'FORBIDDEN', 'detail forbidden'),
    },
    {
      created: false,
      label: 'idempotent-replay (created=false)',
      failureType: 'transient (503)',
      error: new ExchangeApiError(503, 'SERVER_ERROR', 'service unavailable'),
    },
  ])(
    'matrix: $label with $failureType detail GET failure keeps mutation confirmed, retries GET only, and opens on success',
    async ({ created, error }) => {
      const bind = vi.fn().mockResolvedValue(makePatientCreateOutcome(patientWithName, created))
      const get = vi
        .fn()
        .mockRejectedValueOnce(error)
        .mockResolvedValueOnce(patientEntryWithName)
      const client = makeClient({ createPatientWithOutcome: bind, getPatient: get })
      const user = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)
      await screen.findByText('Aucun patient trouvé')
      await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
      await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Patient Synthétique')
      await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

      // Confirmed recovery state
      expect(await screen.findByRole('button', { name: 'Réessayer l’ouverture' })).toBeInTheDocument()
      expect(screen.queryByText(/Tentative non confirmée/)).not.toBeInTheDocument()
      expect(screen.getByText(/Patient créé/)).toBeInTheDocument()
      expect(bind).toHaveBeenCalledTimes(1)
      const identity = bind.mock.calls[0][1]
      expect(typeof identity).toBe('string')
      expect(getStoredPendingPatient('identity-1', 'provider-a')).toMatchObject({
        idempotency_key: identity,
        confirmed_patient_reference_id: patientWithName.patient_reference_id,
      })

      // Retry performs GET only; no second POST create mutation
      await user.click(screen.getByRole('button', { name: 'Réessayer l’ouverture' }))
      expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
      expect(bind).toHaveBeenCalledTimes(1)
      expect(get).toHaveBeenCalledTimes(2)
      expect(getStoredPendingPatient('identity-1', 'provider-a')).toBeNull()
    },
  )

  it('fails closed without POST when durable pending storage cannot be written', async () => {
    const bind = vi.fn()
    const client = makeClient({ createPatientWithOutcome: bind })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Patient Synthétique')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText(/Rien n’a été envoyé : aucun patient n’a été créé/)).toBeInTheDocument()
    expect(bind).not.toHaveBeenCalled()
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toBeNull()
  })

  it('fails closed without POST when storage read-back throws during pre-POST verification', async () => {
    const bind = vi.fn()
    const client = makeClient({ createPatientWithOutcome: bind })
    const originalGetItem = Storage.prototype.getItem
    // Allow initial mount check to succeed, but throw when verifying write before POST
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key: string) {
      if (key.startsWith('dhealth_clinic_pending_patient_v2:identity-1:provider-a')) {
        // Return null for the initial query, but throw if setStoredPendingPatient calls getItem
        if (this.length > 0) {
          throw new Error('read-after-write failed')
        }
      }
      return originalGetItem.call(this, key)
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Patient Synthétique')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText(/Rien n’a été envoyé : aucun patient n’a été créé/)).toBeInTheDocument()
    expect(bind).not.toHaveBeenCalled()
  })

  it('fails closed without POST when storage read-back is mismatched during pre-POST verification', async () => {
    const bind = vi.fn()
    const client = makeClient({ createPatientWithOutcome: bind })
    const originalGetItem = Storage.prototype.getItem
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key: string) {
      if (key.startsWith('dhealth_clinic_pending_patient_v2:identity-1:provider-a')) {
        // If an entry was written, simulate corrupted read-back
        if (this.length > 0) {
          return '{"corrupted":true}'
        }
      }
      return originalGetItem.call(this, key)
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Patient Synthétique')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText(/Rien n’a été envoyé : aucun patient n’a été créé/)).toBeInTheDocument()
    expect(bind).not.toHaveBeenCalled()
  })

  it('fails closed when the scoped pending-store cannot be read', async () => {
    const bind = vi.fn()
    const client = makeClient({ createPatientWithOutcome: bind })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')
    expect(await screen.findByText(/Impossible de vérifier si une création de patient faite ici auparavant a bien abouti/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).toBeDisabled()
    expect(bind).not.toHaveBeenCalled()
  })

  it('fails closed when scoped pending-store record contains corrupted fields and leaves raw entry intact', async () => {
    const bind = vi.fn()
    const client = makeClient({ createPatientWithOutcome: bind })
    const rawCorrupted = JSON.stringify({
      idempotency_key: 12345, // invalid type
      corrupted_payload: true,
    })
    localStorage.setItem('dhealth_clinic_pending_patient_v2:identity-1:provider-a', rawCorrupted)

    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')

    expect(await screen.findByText(/Impossible de vérifier si une création de patient faite ici auparavant a bien abouti/)).toBeInTheDocument()
    expect(screen.queryByText(/corrupted_payload/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).toBeDisabled()
    expect(bind).not.toHaveBeenCalled()
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v2:identity-1:provider-a')).toBe(rawCorrupted)
  })

  it('fails closed when scoped pending-store record contains invalid JSON and leaves raw entry intact', async () => {
    const bind = vi.fn()
    const client = makeClient({ createPatientWithOutcome: bind })
    const rawInvalidJson = '{not valid json'
    localStorage.setItem('dhealth_clinic_pending_patient_v2:identity-1:provider-a', rawInvalidJson)

    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')

    expect(await screen.findByText(/Impossible de vérifier si une création de patient faite ici auparavant a bien abouti/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).toBeDisabled()
    expect(bind).not.toHaveBeenCalled()
    expect(localStorage.getItem('dhealth_clinic_pending_patient_v2:identity-1:provider-a')).toBe(rawInvalidJson)
  })

  it('renders a sanitized recoverable error without exposing raw backend details', async () => {
    const client = makeClient({
      listPatients: vi.fn().mockRejectedValue(
        new ExchangeApiError(500, 'SERVER_ERROR', 'Traceback: secret database details'),
      ),
    })
    render(<StaffApp client={client} initialSection="patients" />)

    expect(await screen.findByText('Erreur du serveur')).toBeInTheDocument()
    expect(screen.queryByText(/Traceback|secret database details/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Réessayer' })).toBeInTheDocument()
  })

  it('does not restore stale provider A results after switching to provider B', async () => {
    let resolveA!: (patients: PatientDirectoryEntry[]) => void
    const providerAPromise = new Promise<PatientDirectoryEntry[]>((resolve) => { resolveA = resolve })
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [
          me.memberships[0],
          { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
        ],
      }),
      listPatients: vi.fn((providerId: string) =>
        providerId === 'provider-a' ? providerAPromise : Promise.resolve([patientEntryWithoutName]),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    await user.selectOptions(await screen.findByLabelText("Établissement actif"), 'provider-b')
    expect(await screen.findByText('Nom non renseigné')).toBeInTheDocument()
    await act(async () => {
      resolveA([patientEntryWithName])
      await providerAPromise
    })
    await waitFor(() => expect(screen.queryByText('Patient Synthétique')).not.toBeInTheDocument())
  })

  it('clears an opened patient when the active provider changes', async () => {
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [
          me.memberships[0],
          { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
        ],
      }),
      listPatients: vi.fn((providerId: string) =>
        Promise.resolve(providerId === 'provider-a' ? [patientEntryWithName] : [patientEntryWithoutName]),
      ),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    await screen.findByText('Patient Synthétique')
    await user.click(screen.getByRole('button', { name: 'Ouvrir' }))
    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText("Établissement actif"), 'provider-b')
    expect(await screen.findByText('Nom non renseigné')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Patient Synthétique' })).not.toBeInTheDocument()
  })

  it('keeps unresolved creation identities isolated through an A to B to A provider roundtrip', async () => {
    let providerAAttempts = 0
    const bind = vi.fn((providerId: string, _idempotencyKey: string, _body: PatientCreateRequest) => {
      if (providerId === 'provider-a') {
        providerAAttempts += 1
        if (providerAAttempts === 1) {
          return Promise.reject(new ExchangeApiError(0, 'NETWORK_ERROR', 'provider A transport'))
        }
        return Promise.resolve(makePatientCreateOutcome(patientWithName, false))
      }
      return Promise.reject(new ExchangeApiError(0, 'NETWORK_ERROR', 'provider B transport'))
    })
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [
          me.memberships[0],
          { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
        ],
      }),
      createPatientWithOutcome: bind,
      getPatient: vi.fn().mockResolvedValue(authoritativeReplayPatientEntry),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Tentative A')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    await screen.findByText('Réessayez pour vérifier la même référence sans créer un doublon.')

    const providerSelect = screen.getByLabelText("Établissement actif")
    await user.selectOptions(providerSelect, 'provider-b')
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Tentative B')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    await screen.findByText('Réessayez pour vérifier la même référence sans créer un doublon.')

    await user.selectOptions(providerSelect, 'provider-a')
    await screen.findByRole('heading', { name: 'Nouveau patient' })
    expect(screen.getByLabelText('Nom du patient (obligatoire)')).toHaveValue('Tentative A')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByRole('heading', { name: 'Nom stocké autoritatif' })).toBeInTheDocument()

    expect(bind).toHaveBeenCalledTimes(3)
    // Same idempotency key reused across provider A's two attempts (retry, not a fresh attempt).
    expect(bind.mock.calls[2][1]).toEqual(bind.mock.calls[0][1])
    // Provider A and provider B never share an idempotency key -- independent request identities.
    expect(bind.mock.calls[1][1]).not.toBe(bind.mock.calls[0][1])
  })

  it('does not open a patient when provider A creation resolves after switching to provider B', async () => {
    let resolveBind!: (outcome: unknown) => void
    const bindPromise = new Promise((resolve) => { resolveBind = resolve })
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [
          me.memberships[0],
          { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
        ],
      }),
      createPatientWithOutcome: vi.fn().mockReturnValue(bindPromise),
      getPatient: vi.fn().mockResolvedValue(patientEntryWithName),
    })
    const user = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Patient Synthétique')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    const providerSelect = screen.getByLabelText("Établissement actif")
    await user.selectOptions(providerSelect, 'provider-b')
    await waitFor(() => expect(providerSelect).toHaveValue('provider-b'))

    resolveBind(makePatientCreateOutcome(patientWithName, true))
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Patient Synthétique' })).not.toBeInTheDocument()
      expect(screen.getByText('Aucun patient trouvé')).toBeInTheDocument()
    })
    expect(client.getPatient).not.toHaveBeenCalled()
    expect(getStoredPendingPatient('identity-1', 'provider-b')).toBeNull()
    expect(screen.queryByText(/Patient créé/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Tentative non confirmée/)).not.toBeInTheDocument()
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toMatchObject({
      confirmed_patient_reference_id: patientWithName.patient_reference_id,
    })
    await user.selectOptions(providerSelect, 'provider-a')
    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
    expect(client.getPatient).toHaveBeenCalledWith('provider-a', patientEntryWithName.patient_id)
  })

  it('blocks patient creation when display name is empty or only whitespace', async () => {
    const bind = vi.fn()
    const client = makeClient({
      createPatientWithOutcome: bind,
    })
    const user = userEvent.setup()

    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))

    // Empty display name
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText('Le nom affiché est obligatoire.')).toBeInTheDocument()
    expect(bind).not.toHaveBeenCalled()

    // Whitespace display name
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), '   ')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText('Le nom affiché est obligatoire.')).toBeInTheDocument()
    expect(bind).not.toHaveBeenCalled()
  })

  it('handles definitive failure by clearing pending state and leaving form editable with fresh identity', async () => {
    const bind = vi
      .fn()
      .mockRejectedValueOnce(new ExchangeApiError(422, 'VALIDATION_ERROR', 'invalid format'))
      .mockResolvedValueOnce(makePatientCreateOutcome(patientWithName, true))
    const client = makeClient({
      createPatientWithOutcome: bind,
      getPatient: vi.fn().mockResolvedValue(patientEntryWithName),
    })
    const user = userEvent.setup()

    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Tentative Definitive')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    // Shows validation error without ambiguous duplicate warning
    expect(await screen.findByText('Requête invalide')).toBeInTheDocument()
    expect(
      screen.queryByText('Réessayez pour vérifier la même référence sans créer un doublon.'),
    ).not.toBeInTheDocument()

    // Form remains enabled
    const input = screen.getByLabelText('Nom du patient (obligatoire)')
    expect(input).not.toBeDisabled()

    // No pending state in storage
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toBeNull()

    // Submitting again mints a fresh UUID
    await user.clear(input)
    await user.type(input, 'Tentative Corrigee')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(bind).toHaveBeenCalledTimes(2)
    const firstCallUUID = bind.mock.calls[0][1]
    const secondCallUUID = bind.mock.calls[1][1]
    expect(firstCallUUID).not.toBe(secondCallUUID)
    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
  })

  it('persists strictly non-PHI tuple to storage on ambiguous failure', async () => {
    const bind = vi.fn().mockRejectedValue(new ExchangeApiError(0, 'NETWORK_ERROR', 'network down'))
    const client = makeClient({ createPatientWithOutcome: bind })
    const user = userEvent.setup()

    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Patient Secret PHI')
    await user.type(screen.getByLabelText('Téléphone (facultatif)'), '+25377999888')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(await screen.findByText('Réessayez pour vérifier la même référence sans créer un doublon.')).toBeInTheDocument()

    const stored = getStoredPendingPatient('identity-1', 'provider-a')
    expect(stored).not.toBeNull()
    expect(stored?.idempotency_key).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    )
    expect(typeof stored?.created_at).toBe('number')

    // Verify localStorage contains ZERO PHI
    const rawStorage = localStorage.getItem('dhealth_clinic_pending_patient_v2:identity-1:provider-a')
    expect(rawStorage).not.toBeNull()
    expect(rawStorage).not.toContain('Patient Secret PHI')
    expect(rawStorage).not.toContain('+25377999888')
  })

  it('recovers from reload without allowing discard or a fresh identity', async () => {
    // Pre-populate storage with an unresolved idempotency key
    const staleKey = '99999999-9999-4999-8999-999999999999'
    setStoredPendingPatient('identity-1', 'provider-a', {
      idempotency_key: staleKey,
      created_at: Date.now(),
    })

    const bind = vi.fn().mockResolvedValue(makePatientCreateOutcome(patientWithName, true))
    const client = makeClient({
      createPatientWithOutcome: bind,
      listPatients: vi.fn().mockResolvedValue([]),
      getPatient: vi.fn().mockResolvedValue(patientEntryWithName),
    })
    const user = userEvent.setup()

    render(<StaffApp client={client} initialSection="patients" />)
    await screen.findByText('Aucun patient trouvé')

    // Banner indicates unresolved attempt
    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()
    expect(screen.getByText(/La création du patient n’a pas pu être confirmée/)).toBeInTheDocument()
    expect(screen.queryByText("Aucun patient n'a été créé")).not.toBeInTheDocument()

    expect(screen.queryByRole('button', { name: 'Abandonner la tentative' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Reprendre' }))
    await user.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Nouveau Patient Après Abandon')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(bind).toHaveBeenCalledTimes(1)
    expect(bind.mock.calls[0][1]).toBe(staleKey)
  })

  it('recovers a confirmed reference by its stored authoritative id', async () => {
    setStoredPendingPatient('identity-1', 'provider-a', {
      idempotency_key: 'committed-request-key',
      confirmed_patient_id: '44444444-4444-4444-8444-444444444444',
      confirmed_patient_reference_id: '33333333-3333-4333-8333-333333333333',
      created_at: Date.now(),
    })

    const matchingPatientEntry: PatientDirectoryEntry = {
      patient_id: '44444444-4444-4444-8444-444444444444',
      provider_organization_id: 'provider-a',
      display_name: 'Patient Déjà Sauvegardé',
      date_of_birth: null,
      contact_channel_type: 'PHONE',
      contact_value: '+25377222333',
      primary_patient_reference_id: '33333333-3333-4333-8333-333333333333',
      created_at: new Date().toISOString(),
    }

    const client = makeClient({
      getPatient: vi.fn().mockResolvedValue(matchingPatientEntry),
    })

    render(<StaffApp client={client} initialSection="patients" />)

    // Automatically reconciled and opened
    expect(await screen.findByRole('heading', { name: 'Patient Déjà Sauvegardé' })).toBeInTheDocument()
    // Cleared from pending storage
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toBeNull()
  })

  it('recovers an unconfirmed attempt via server-side registration lookup when the create response was lost', async () => {
    // No confirmed_patient_reference_id was ever recorded -- the create POST's response never
    // reached the browser, but the server may have committed it anyway. Only the opaque request
    // identity survives to reconcile against.
    setStoredPendingPatient('identity-1', 'provider-a', {
      idempotency_key: 'lost-response-key',
      created_at: Date.now(),
    })

    const getPatientRegistration = vi.fn().mockResolvedValue(patientEntryWithName)
    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([]),
      getPatientRegistration,
      getPatientReference: vi.fn(),
    })

    render(<StaffApp client={client} initialSection="patients" />)

    // Automatically reconciled and opened via the idempotency-key lookup, never getPatientReference
    expect(await screen.findByRole('heading', { name: patientWithName.display_name! })).toBeInTheDocument()
    expect(getPatientRegistration).toHaveBeenCalledWith('provider-a', 'lost-response-key')
    expect(client.getPatientReference).not.toHaveBeenCalled()
    // Cleared from pending storage
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toBeNull()
  })

  it('retains non-PHI pending identities on logout for safe later reconciliation', async () => {
    setStoredPendingPatient('identity-1', 'provider-a', {
      idempotency_key: 'test-uuid',
      created_at: Date.now(),
    })
    expect(getStoredPendingPatient('identity-1', 'provider-a')).not.toBeNull()

    const onLogout = vi.fn()
    const client = makeClient()
    const user = userEvent.setup()

    render(<StaffApp client={client} initialSection="patients" onLogout={onLogout} />)
    await screen.findByText('Aucun patient trouvé')

    await user.click(screen.getByRole('button', { name: 'Se déconnecter' }))
    expect(onLogout).toHaveBeenCalledTimes(1)
    expect(getStoredPendingPatient('identity-1', 'provider-a')?.idempotency_key).toBe('test-uuid')
  })

  it('maintains strict user and provider isolation of unconfirmed state across logout and login', async () => {
    const unconfirmedKey = '11111111-2222-4333-8444-555555555555'
    setStoredPendingPatient('identity-1', 'provider-a', {
      idempotency_key: unconfirmedKey,
      created_at: Date.now(),
    })

    const bind = vi.fn().mockResolvedValue(makePatientCreateOutcome(patientWithName, true))
    // 1. User 1 logs in with Provider A: sees unconfirmed banner
    const clientUser1 = makeClient({
      getMe: vi.fn().mockResolvedValue({ ...me, identity_id: 'identity-1' }),
      createPatientWithOutcome: bind,
      getPatient: vi.fn().mockResolvedValue(patientEntryWithName),
    })
    const { unmount: unmount1 } = render(
      <StaffApp client={clientUser1} initialSection="patients" />,
    )
    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).toBeDisabled()
    unmount1()

    // 2. Different user (identity-2) logs in with Provider A: CANNOT consume or see identity-1 attempt
    const clientUser2 = makeClient({
      getMe: vi.fn().mockResolvedValue({ ...me, identity_id: 'identity-2' }),
      createPatientWithOutcome: bind,
      getPatient: vi.fn().mockResolvedValue(patientEntryWithName),
    })
    const { unmount: unmount2 } = render(
      <StaffApp client={clientUser2} initialSection="patients" />,
    )
    await screen.findByText('Aucun patient trouvé')
    expect(screen.queryByText(/Tentative non confirmée/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).not.toBeDisabled()
    unmount2()

    // 3. User 1 logs in with different provider (provider-b): CANNOT consume or see provider-a attempt
    const clientWithBothProviders = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        identity_id: 'identity-1',
        memberships: [
          me.memberships[0],
          { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
        ],
      }),
      createPatientWithOutcome: bind,
      getPatient: vi.fn().mockResolvedValue(patientEntryWithName),
    })
    const { unmount: unmount3 } = render(
      <StaffApp client={clientWithBothProviders} initialSection="patients" />,
    )
    const userEventObj = userEvent.setup()
    const providerSelect = await screen.findByLabelText("Établissement actif")
    await userEventObj.selectOptions(providerSelect, 'provider-b')
    await screen.findByText('Aucun patient trouvé')
    expect(screen.queryByText(/Tentative non confirmée/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).not.toBeDisabled()
    unmount3()

    // 4. User 1 logs back in with Provider A: can resume and complete creation with exact same UUID
    render(
      <StaffApp client={clientUser1} initialSection="patients" />,
    )
    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()
    await userEventObj.click(screen.getByRole('button', { name: 'Reprendre' }))
    await userEventObj.type(screen.getByLabelText('Nom du patient (obligatoire)'), 'Patient Repris')
    await userEventObj.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(bind).toHaveBeenCalledTimes(1)
    expect(bind.mock.calls[0][1]).toBe(unconfirmedKey)
    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toBeNull()
  })

  // -- DH-CLINIC-003: Native Diagnostic Service Ordering Tests -----------------------------------

  const mockCatalogTests: CatalogEntry[] = [
    {
      id: '11111111-2222-4333-8444-555555555555',
      code: 'SYNTH-CBC',
      display_name: 'Numération Formule Sanguine (NFS)',
      version: 1,
      active: true,
      is_synthetic: true,
    },
    {
      id: '22222222-3333-4444-8555-666666666666',
      code: 'SYNTH-GLU',
      display_name: 'Glycémie à jeun',
      version: 1,
      active: true,
      is_synthetic: true,
    },
    {
      id: '33333333-4444-4555-8666-777777777777',
      code: 'SYNTH-LIP',
      display_name: 'Bilan lipidique complet',
      version: 1,
      active: true,
      is_synthetic: true,
    },
  ]

  const mockExistingOrder: LabOrder = {
    id: '99999999-8888-4777-8666-555555555555',
    provider_organization_id: 'provider-a',
    patient_reference_id: patientWithName.patient_reference_id,
    practitioner_reference_id: 'prac-1',
    patient: patientIdentityForOrders,
    status: 'AWAITING_LAB_SELECTION',
    created_at: '2026-09-16T07:00:00Z',
    items: [
      {
        catalog_entry_id: mockCatalogTests[0].id,
        catalog_code_snapshot: 'SYNTH-CBC',
        catalog_display_name_snapshot: 'Numération Formule Sanguine (NFS)',
      },
    ],
    visit_id: null,
    lab_assignment: null,
    block: null,
  }

  it('fetches and displays patient order history with truthful status and lab state in patient workspace', async () => {
    const listOrders = vi.fn().mockResolvedValue([mockExistingOrder])
    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listPatientOrders: listOrders,
    })

    render(<StaffApp client={client} initialSection="patients" />)
    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEvent.setup().click(openButtons[0])

    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
    expect(listOrders).toHaveBeenCalledWith(
      'provider-a',
      patientWithName.patient_reference_id,
      expect.objectContaining({ limit: 50 }),
    )

    expect(await screen.findByText('Historique des ordres de laboratoire')).toBeInTheDocument()
    expect(screen.getByText('En attente de choix labo')).toBeInTheDocument()
    // DH-CLINIC-006 §36: the real destination-lab projection, never a status-derived fabrication
    // -- this order has no LabSelection, so it truthfully shows "Non attribué".
    expect(screen.getByText('Non attribué')).toBeInTheDocument()
    expect(screen.getByText('SYNTH-CBC')).toBeInTheDocument()
    expect(screen.getByText(/Numération Formule Sanguine/)).toBeInTheDocument()
  })

  it('completes 3-step diagnostic service ordering flow with catalog search, multi-selection, review, and 201 creation', async () => {
    const createdOrder: LabOrder = {
      id: 'aaaa1111-bb22-4c33-8d44-eeee55556666',
      provider_organization_id: 'provider-a',
      patient_reference_id: patientWithName.patient_reference_id,
      practitioner_reference_id: 'prac-clinician',
      patient: patientIdentityForOrders,
      status: 'AWAITING_LAB_SELECTION',
      created_at: '2026-09-16T08:30:00Z',
      items: [
        {
          catalog_entry_id: mockCatalogTests[0].id,
          catalog_code_snapshot: 'SYNTH-CBC',
          catalog_display_name_snapshot: 'Numération Formule Sanguine (NFS)',
        },
        {
          catalog_entry_id: mockCatalogTests[1].id,
          catalog_code_snapshot: 'SYNTH-GLU',
          catalog_display_name_snapshot: 'Glycémie à jeun',
        },
      ],
      visit_id: null,
      lab_assignment: null,
      block: null,
    }

    const listCatalog = vi.fn().mockResolvedValue(mockCatalogTests)
    const createOrder = vi.fn().mockResolvedValue({
      order: createdOrder,
      created: true,
    })
    const listOrders = vi.fn().mockResolvedValue([])

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: listCatalog,
      createOrderWithOutcome: createOrder,
      listPatientOrders: listOrders,
      getOrder: vi.fn().mockResolvedValue({
        ...createdOrder,
        status: 'SELECTED',
        visit_id: null,
        lab_assignment: {
          lab_organization_id: 'lab-default-eligible',
          lab_organization_name: 'Laboratoire Test',
          selected_at: new Date().toISOString(),
        },
      }),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    // Open patient
    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    // 1. Start ordering wizard
    const orderBtn = await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' })
    await userEventObj.click(orderBtn)

    expect(await screen.findByRole('heading', { name: 'Prescrire un ordre de laboratoire' })).toBeInTheDocument()
    expect(listCatalog).toHaveBeenCalled()

    // Next button disabled with 0 services selected
    const nextBtn = screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' })
    expect(nextBtn).toBeDisabled()

    // Filter catalog
    const filterInput = screen.getByLabelText('Filtrer les services diagnostiques')
    await userEventObj.type(filterInput, 'glyc')
    expect(screen.getByText('SYNTH-GLU')).toBeInTheDocument()
    expect(screen.queryByText('SYNTH-CBC')).not.toBeInTheDocument()

    // Clear filter
    await userEventObj.clear(filterInput)
    expect(await screen.findByText('SYNTH-CBC')).toBeInTheDocument()

    // Select 2 services
    const checkboxes = screen.getAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(checkboxes[1])
    expect(screen.getByText('2 services sélectionnés.')).toBeInTheDocument()
    expect(nextBtn).not.toBeDisabled()

    // 2. Go to Review step
    await userEventObj.click(nextBtn)
    expect(await screen.findByRole('heading', { name: 'Révision de l’ordre de laboratoire' })).toBeInTheDocument()
    expect(screen.getAllByText('Clinique A').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Pas encore sélectionné').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/SYNTH-CBC/)).toBeInTheDocument()
    expect(screen.getByText(/SYNTH-GLU/)).toBeInTheDocument()

    // 3. Confirm and submit order
    const confirmBtn = screen.getByRole('button', { name: 'Confirmer et prescrire' })
    await userEventObj.click(confirmBtn)

    expect(createOrder).toHaveBeenCalledTimes(1)
    const [calledProviderId, calledIdempotencyKey, calledBody] = createOrder.mock.calls[0]
    expect(calledProviderId).toBe('provider-a')
    // Valid canonical UUIDv4 idempotency key
    expect(calledIdempotencyKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    )
    expect(calledBody).toEqual({
      patient_reference_id: patientWithName.patient_reference_id,
      catalog_entry_ids: [mockCatalogTests[0].id, mockCatalogTests[1].id],
    })

    // DH-CLINIC-006: the order already exists here -- the wizard's next step is routing it to a
    // lab, never a second POST of the original intent.
    await completeRoutingStep(userEventObj)

    // Confirmation step
    expect(
      await screen.findByRole('heading', { name: 'Ordre de laboratoire créé avec succès' }),
    ).toBeInTheDocument()
    expect(screen.getByText(createdOrder.id)).toBeInTheDocument()

    // Close wizard and return to patient workspace
    const closeBtn = screen.getByRole('button', { name: 'Fermer et afficher la fiche patient' })
    await userEventObj.click(closeBtn)

    expect(screen.queryByRole('heading', { name: 'Prescrire un ordre de laboratoire' })).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeInTheDocument()
  })

  it('distinguishes idempotent replay 200 with explicit confirmation messaging', async () => {
    const replayOrder: LabOrder = {
      id: 'bbbb2222-cc33-4d44-8e55-ffff66667777',
      provider_organization_id: 'provider-a',
      patient_reference_id: patientWithName.patient_reference_id,
      practitioner_reference_id: 'prac-clinician',
      patient: patientIdentityForOrders,
      status: 'AWAITING_LAB_SELECTION',
      created_at: '2026-09-16T08:30:00Z',
      items: [
        {
          catalog_entry_id: mockCatalogTests[0].id,
          catalog_code_snapshot: 'SYNTH-CBC',
          catalog_display_name_snapshot: 'Numération Formule Sanguine (NFS)',
        },
      ],
      visit_id: null,
      lab_assignment: null,
      block: null,
    }

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      createOrderWithOutcome: vi.fn().mockResolvedValue({
        order: replayOrder,
        created: false,
      }),
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))
    await completeRoutingStep(userEventObj)

    expect(
      await screen.findByRole('heading', {
        name: 'Ordre de laboratoire confirmé (tentative existante récupérée sans doublon)',
      }),
    ).toBeInTheDocument()
  })

  it('retains identical idempotency key across ambiguous network failure retry on order submission', async () => {
    let callCount = 0
    const successfulOrder: LabOrder = {
      id: 'cccc3333-dd44-4e55-8f66-aaaa77778888',
      provider_organization_id: 'provider-a',
      patient_reference_id: patientWithName.patient_reference_id,
      practitioner_reference_id: 'prac-clinician',
      patient: patientIdentityForOrders,
      status: 'AWAITING_LAB_SELECTION',
      created_at: '2026-09-16T08:30:00Z',
      items: [
        {
          catalog_entry_id: mockCatalogTests[0].id,
          catalog_code_snapshot: 'SYNTH-CBC',
          catalog_display_name_snapshot: 'Numération Formule Sanguine (NFS)',
        },
      ],
      visit_id: null,
      lab_assignment: null,
      block: null,
    }

    const createOrder = vi.fn().mockImplementation(() => {
      callCount++
      if (callCount === 1) {
        throw new ExchangeApiError(0, 'NETWORK_ERROR', 'Network connection failure')
      }
      return Promise.resolve({ order: successfulOrder, created: true })
    })

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      createOrderWithOutcome: createOrder,
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))

    // First attempt fails with network error
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))

    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()
    // Button in wizard review step
    const retryBtn = screen.getByRole('button', { name: 'Vérifier / réessayer' })
    expect(retryBtn).toBeInTheDocument()

    // Retry submission
    await userEventObj.click(retryBtn)

    expect(createOrder).toHaveBeenCalledTimes(2)
    const firstKey = createOrder.mock.calls[0][1]
    const secondKey = createOrder.mock.calls[1][1]
    expect(firstKey).toBe(secondKey)
    await completeRoutingStep(userEventObj)
    expect(
      await screen.findByRole('heading', { name: 'Ordre de laboratoire créé avec succès' }),
    ).toBeInTheDocument()
  })

  it('handles definitive error by displaying error detail and allowing review/catalog modification', async () => {
    const createOrder = vi.fn().mockRejectedValue(
      new ExchangeApiError(422, 'VALIDATION_ERROR', 'Le catalogue entry est inactif'),
    )

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      createOrderWithOutcome: createOrder,
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))

    expect(await screen.findByText('Le catalogue entry est inactif')).toBeInTheDocument()

    // Can click "Modifier les services" to go back to catalog
    await userEventObj.click(screen.getByRole('button', { name: 'Modifier les services' }))
    expect(await screen.findByRole('heading', { name: 'Prescrire un ordre de laboratoire' })).toBeInTheDocument()
  })

  it('switching patients or providers resets ordering wizard and maintains strict tenant isolation', async () => {
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [
          me.memberships[0],
          { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
        ],
      }),
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName, patientEntryWithoutName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    // Open patient 1
    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    // Open wizard for patient 1
    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    expect(await screen.findByRole('heading', { name: 'Prescrire un ordre de laboratoire' })).toBeInTheDocument()

    // Return to patients list
    await userEventObj.click(screen.getByRole('button', { name: 'Retour aux patients' }))

    // Open patient 2: wizard must not remain open
    const openButtons2 = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons2[1])

    expect(screen.queryByRole('heading', { name: 'Prescrire un ordre de laboratoire' })).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeInTheDocument()

    // Switch provider: clears patient workspace
    const providerSelect = screen.getByLabelText("Établissement actif")
    await userEventObj.selectOptions(providerSelect, 'provider-b')

    expect(screen.queryByRole('heading', { level: 2, name: patientWithoutName.local_reference })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nouvel ordre de laboratoire' })).not.toBeInTheDocument()
    expect(await screen.findByText('Patients trouvés')).toBeInTheDocument()
  })

  it('freezes exact order intent after ambiguous failure, hides modifier les services, and closing wizard preserves intent', async () => {
    const createOrder = vi.fn().mockRejectedValue(
      new ExchangeApiError(0, 'NETWORK_ERROR', 'Connection timed out'),
    )

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      createOrderWithOutcome: createOrder,
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))

    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Vérifier / réessayer' })).toBeInTheDocument()
    // "Modifier les services" MUST NOT be rendered when intent is frozen
    expect(screen.queryByRole('button', { name: 'Modifier les services' })).not.toBeInTheDocument()

    // "Fermer" (was Annuler) must close wizard without discarding unresolved intent
    const closeBtn = screen.getByRole('button', { name: 'Fermer' })
    await userEventObj.click(closeBtn)

    // Wizard is closed, but patient workspace shows persistent alert banner
    expect(screen.queryByRole('heading', { name: 'Révision de l’ordre de laboratoire' })).not.toBeInTheDocument()
    expect(await screen.findByText(/Tentative d’ordre non confirmée/)).toBeInTheDocument()
    // "Nouvel ordre de laboratoire" must be DISABLED while unresolved intent exists
    expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()

    // Resuming from patient workspace banner reopens review with the exact frozen intent
    const resumeBtn = screen.getByRole('button', { name: 'Vérifier / réessayer' })
    await userEventObj.click(resumeBtn)

    expect(await screen.findByRole('heading', { name: 'Révision de l’ordre de laboratoire' })).toBeInTheDocument()
    expect(screen.getByText('SYNTH-CBC')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifier les services' })).not.toBeInTheDocument()
  })

  it('preserves frozen unresolved order intent across leaving patient and returning', async () => {
    let callCount = 0
    const successfulOrder: LabOrder = {
      id: 'ord-unresolved-survive-nav',
      provider_organization_id: 'provider-a',
      patient_reference_id: patientWithName.patient_reference_id,
      practitioner_reference_id: 'prac-clinician',
      patient: patientIdentityForOrders,
      status: 'AWAITING_LAB_SELECTION',
      created_at: '2026-09-16T08:30:00Z',
      items: [
        {
          catalog_entry_id: mockCatalogTests[0].id,
          catalog_code_snapshot: 'SYNTH-CBC',
          catalog_display_name_snapshot: 'Numération Formule Sanguine (NFS)',
        },
      ],
      visit_id: null,
      lab_assignment: null,
      block: null,
    }

    const createOrder = vi.fn().mockImplementation(() => {
      callCount++
      if (callCount === 1) {
        throw new ExchangeApiError(504, 'NETWORK_ERROR', 'Gateway timeout')
      }
      return Promise.resolve({ order: successfulOrder, created: true })
    })

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      createOrderWithOutcome: createOrder,
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))

    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()
    const firstKey = createOrder.mock.calls[0][1]

    // Navigate back to patients list
    await userEventObj.click(screen.getByRole('button', { name: 'Fermer' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Retour aux patients' }))
    expect(await screen.findByText('Patients trouvés')).toBeInTheDocument()

    // Reopen patient workspace: unresolved banner must be present
    const reopenButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(reopenButtons[0])

    expect(await screen.findByText(/Tentative d’ordre non confirmée/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()

    // Resume and retry from workspace
    await userEventObj.click(screen.getByRole('button', { name: 'Vérifier / réessayer' }))
    expect(await screen.findByRole('heading', { name: 'Révision de l’ordre de laboratoire' })).toBeInTheDocument()

    await userEventObj.click(screen.getByRole('button', { name: 'Vérifier / réessayer' }))

    expect(createOrder).toHaveBeenCalledTimes(2)
    const secondKey = createOrder.mock.calls[1][1]
    expect(secondKey).toBe(firstKey)
    await completeRoutingStep(userEventObj)
    expect(
      await screen.findByRole('heading', { name: 'Ordre de laboratoire créé avec succès' }),
    ).toBeInTheDocument()
  })

  it('keeps unresolved order intent isolated through provider A -> B -> A roundtrip and patient isolation', async () => {
    const createOrder = vi.fn().mockRejectedValue(
      new ExchangeApiError(0, 'NETWORK_ERROR', 'Network connection dropped'),
    )

    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [
          me.memberships[0],
          { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
        ],
      }),
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName, patientEntryWithoutName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      createOrderWithOutcome: createOrder,
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    // Open patient 1 on Provider A
    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    // Order fails ambiguously
    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))
    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()
    await userEventObj.click(screen.getByRole('button', { name: 'Fermer' }))

    // Switch to patient 2 in Provider A: patient 2 must NOT have unresolved order banner
    await userEventObj.click(screen.getByRole('button', { name: 'Retour aux patients' }))
    const openButtons2 = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons2[1])

    expect(screen.queryByText(/Tentative d’ordre non confirmée/)).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeInTheDocument()

    // Switch to Provider B
    const providerSelect = screen.getByLabelText("Établissement actif")
    await userEventObj.selectOptions(providerSelect, 'provider-b')
    expect(providerSelect).toHaveValue('provider-b')

    // Open patient in Provider B: no unresolved order banner
    const openButtonsB = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtonsB[0])
    expect(screen.queryByText(/Tentative d’ordre non confirmée/)).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeInTheDocument()

    // Switch back to Provider A: open patient 1 -> unresolved order banner MUST still be present
    await userEventObj.selectOptions(providerSelect, 'provider-a')
    const openButtonsA = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtonsA[0])

    expect(await screen.findByText(/Tentative d’ordre non confirmée/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()
  })

  it('decouples auxiliary order history refresh failure so successful order is never re-POSTed', async () => {
    const createdOrder: LabOrder = {
      id: 'ord-decoupled-refresh-fail',
      provider_organization_id: 'provider-a',
      patient_reference_id: patientWithName.patient_reference_id,
      practitioner_reference_id: 'prac-clinician',
      patient: patientIdentityForOrders,
      status: 'AWAITING_LAB_SELECTION',
      created_at: '2026-09-16T08:30:00Z',
      items: [
        {
          catalog_entry_id: mockCatalogTests[0].id,
          catalog_code_snapshot: 'SYNTH-CBC',
          catalog_display_name_snapshot: 'Numération Formule Sanguine (NFS)',
        },
      ],
      visit_id: null,
      lab_assignment: null,
      block: null,
    }

    const createOrder = vi.fn().mockResolvedValue({ order: createdOrder, created: true })
    // Auxiliary listPatientOrders fails
    const listOrders = vi.fn().mockImplementation(() => {
      // First call for patient initial load succeeds, second call on refresh fails
      return Promise.reject(new ExchangeApiError(500, 'SERVER_ERROR', 'Internal server error'))
    })

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      createOrderWithOutcome: createOrder,
      listPatientOrders: listOrders,
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))
    await completeRoutingStep(userEventObj)

    // Order confirmation MUST succeed despite auxiliary history failure
    expect(
      await screen.findByRole('heading', { name: 'Ordre de laboratoire créé avec succès' }),
    ).toBeInTheDocument()
    expect(screen.getByText(createdOrder.id)).toBeInTheDocument()
    // POST was called exactly once
    expect(createOrder).toHaveBeenCalledTimes(1)
  })

  it('safely handles same-key 409 mismatch without clearing frozen intent', async () => {
    let callCount = 0
    const createOrder = vi.fn().mockImplementation(() => {
      callCount++
      if (callCount === 1) {
        throw new ExchangeApiError(0, 'NETWORK_ERROR', 'Network error')
      }
      throw new ExchangeApiError(
        409,
        'CONFLICT',
        'Idempotency key was previously used with a different payload',
      )
    })

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      createOrderWithOutcome: createOrder,
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))

    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()

    // Retry fails with 409
    await userEventObj.click(screen.getByRole('button', { name: 'Vérifier / réessayer' }))

    // 409 must be safely presented
    expect(await screen.findByText('Conflit')).toBeInTheDocument()
    // Key must not be discarded
    expect(createOrder.mock.calls[0][1]).toBe(createOrder.mock.calls[1][1])
  })

  it('fails closed when scoped pending order storage cannot be read or is corrupt', async () => {
    // Put corrupted JSON in pending order storage
    localStorage.setItem(
      'dhealth_clinic_pending_order_v1:identity-1:provider-a:' + patientWithName.patient_reference_id,
      '{corrupt-json',
    )

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    // Must surface storage unreadable error
    expect(
      await screen.findByText(/DHealth ne peut plus vérifier ici si un ordre envoyé précédemment est bien arrivé/),
    ).toBeInTheDocument()

    // "Nouvel ordre de laboratoire" must be disabled to prevent duplicate creation
    const newOrderBtn = screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })
    expect(newOrderBtn).toBeDisabled()
  })

  it('reconciles stored pending order on patient open if order exists on server', async () => {
    const existingOrder: LabOrder = {
      id: 'ord-stored-server-reconciled',
      provider_organization_id: 'provider-a',
      patient_reference_id: patientWithName.patient_reference_id,
      practitioner_reference_id: 'prac-clinician',
      patient: patientIdentityForOrders,
      status: 'AWAITING_LAB_SELECTION',
      created_at: '2026-09-16T08:30:00Z',
      items: [
        {
          catalog_entry_id: mockCatalogTests[0].id,
          catalog_code_snapshot: 'SYNTH-CBC',
          catalog_display_name_snapshot: 'Numération Formule Sanguine (NFS)',
        },
      ],
      visit_id: null,
      lab_assignment: null,
      block: null,
    }

    // Set stored technical metadata in localStorage
    setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
      idempotency_key: 'stored-idemp-key-1234',
    })

    const getOrderByIdempotencyKey = vi.fn().mockResolvedValue(existingOrder)

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      getOrderByIdempotencyKey,
      listPatientOrders: vi.fn().mockResolvedValue([]),
      getOrder: vi.fn().mockResolvedValue({
        ...existingOrder,
        status: 'SELECTED',
        visit_id: null,
        lab_assignment: {
          lab_organization_id: 'lab-default-eligible',
          lab_organization_name: 'Laboratoire Test',
          selected_at: new Date().toISOString(),
        },
      }),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    // Server was queried by idempotency key
    expect(getOrderByIdempotencyKey).toHaveBeenCalledWith('provider-a', 'stored-idemp-key-1234')

    // DH-CLINIC-006 §30: the recovered order is genuinely still AWAITING_LAB_SELECTION -- the
    // wizard must show the routing step, never fabricate "confirmed" for an order that was never
    // actually routed.
    await completeRoutingStep(userEventObj)

    // Automatically confirmed and cleaned up from storage
    expect(
      await screen.findByRole('heading', {
        name: 'Ordre de laboratoire confirmé (tentative existante récupérée sans doublon)',
      }),
    ).toBeInTheDocument()
    expect(getStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id)).toBeNull()
  })

  it('DH-CLINIC-006 §23/§36/§37: displays the real destination lab (or a truthful "Non attribué"), never a status-derived fabrication, and gates the routing action to AWAITING_LAB_SELECTION only', async () => {
    const routedTo = (labName: string) => ({
      lab_organization_id: `lab-${labName}`,
      lab_organization_name: labName,
      selected_at: '2026-09-16T07:10:00Z',
    })
    const orders: LabOrder[] = [
      {
        id: 'ord-awaiting',
        provider_organization_id: 'provider-a',
        patient_reference_id: patientWithName.patient_reference_id,
        practitioner_reference_id: 'prac-1',
        patient: patientIdentityForOrders,
        status: 'AWAITING_LAB_SELECTION',
        created_at: '2026-09-16T07:00:00Z',
        items: [{ catalog_entry_id: 'c1', catalog_code_snapshot: 'C1', catalog_display_name_snapshot: 'D1' }],
        visit_id: null,
        lab_assignment: null,
        block: null,
      },
      {
        id: 'ord-cancelled',
        provider_organization_id: 'provider-a',
        patient_reference_id: patientWithName.patient_reference_id,
        practitioner_reference_id: 'prac-1',
        patient: patientIdentityForOrders,
        status: 'CANCELLED',
        created_at: '2026-09-16T07:01:00Z',
        items: [{ catalog_entry_id: 'c2', catalog_code_snapshot: 'C2', catalog_display_name_snapshot: 'D2' }],
        // Never routed before cancellation -- truthfully has no destination.
        visit_id: null,
        lab_assignment: null,
        block: null,
      },
      {
        id: 'ord-rejected',
        provider_organization_id: 'provider-a',
        patient_reference_id: patientWithName.patient_reference_id,
        practitioner_reference_id: 'prac-1',
        patient: patientIdentityForOrders,
        status: 'REJECTED',
        created_at: '2026-09-16T07:02:00Z',
        items: [{ catalog_entry_id: 'c3', catalog_code_snapshot: 'C3', catalog_display_name_snapshot: 'D3' }],
        // §23: SELECTED -> REJECTED -- the lab that rejected it must still show, never hidden.
        visit_id: null,
        lab_assignment: routedTo('Laboratoire Rejetant'),
        block: null,
      },
      {
        id: 'ord-selected',
        provider_organization_id: 'provider-a',
        patient_reference_id: patientWithName.patient_reference_id,
        practitioner_reference_id: 'prac-1',
        patient: patientIdentityForOrders,
        status: 'SELECTED',
        created_at: '2026-09-16T07:03:00Z',
        items: [{ catalog_entry_id: 'c4', catalog_code_snapshot: 'C4', catalog_display_name_snapshot: 'D4' }],
        visit_id: null,
        lab_assignment: routedTo('Laboratoire Sélectionné'),
        block: null,
      },
      {
        id: 'ord-completed',
        provider_organization_id: 'provider-a',
        patient_reference_id: patientWithName.patient_reference_id,
        practitioner_reference_id: 'prac-1',
        patient: patientIdentityForOrders,
        status: 'COMPLETED',
        created_at: '2026-09-16T07:04:00Z',
        items: [{ catalog_entry_id: 'c5', catalog_code_snapshot: 'C5', catalog_display_name_snapshot: 'D5' }],
        visit_id: null,
        lab_assignment: routedTo('Laboratoire Terminé'),
        block: null,
      },
    ]

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listPatientOrders: vi.fn().mockResolvedValue(orders),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    expect(await screen.findByText('Historique des ordres de laboratoire')).toBeInTheDocument()

    // Unrouted orders (never assigned) show the truthful "Non attribué" -- once for AWAITING, once
    // for CANCELLED-before-ever-routed.
    expect(screen.getAllByText('Non attribué').length).toBe(2)
    // Real destination-lab names render, including for the REJECTED order that WAS routed first.
    expect(screen.getByText('Laboratoire Rejetant')).toBeInTheDocument()
    expect(screen.getByText('Laboratoire Sélectionné')).toBeInTheDocument()
    expect(screen.getByText('Laboratoire Terminé')).toBeInTheDocument()

    // Only the AWAITING_LAB_SELECTION order gets a routing action -- never a rejected/cancelled/
    // already-routed/completed one.
    expect(
      screen.getByRole('button', { name: `Choisir un laboratoire pour ${patientIdentityForOrders.display_name}` }),
    ).toBeInTheDocument()
  })

  it('enforces persist-before-POST: storage contains key during in-flight POST and cleans up on success', async () => {
    let keyInStorageDuringPost: string | null = null
    const createdOrder: LabOrder = {
      id: 'ord-persist-before-post',
      provider_organization_id: 'provider-a',
      patient_reference_id: patientWithName.patient_reference_id,
      practitioner_reference_id: 'prac-1',
      patient: patientIdentityForOrders,
      status: 'AWAITING_LAB_SELECTION',
      created_at: '2026-09-16T08:00:00Z',
      items: [{ catalog_entry_id: 'c1', catalog_code_snapshot: 'C1', catalog_display_name_snapshot: 'D1' }],
      visit_id: null,
      lab_assignment: null,
      block: null,
    }

    const createOrder = vi.fn().mockImplementation((_providerId, _key) => {
      // Check localStorage RIGHT NOW while POST is in-flight
      const stored = getStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id)
      keyInStorageDuringPost = stored?.idempotency_key ?? null
      return Promise.resolve({ order: createdOrder, created: true })
    })

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      listPatientOrders: vi.fn().mockResolvedValue([]),
      createOrderWithOutcome: createOrder,
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])
    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))
    await completeRoutingStep(userEventObj)

    expect(await screen.findByRole('heading', { name: 'Ordre de laboratoire créé avec succès' })).toBeInTheDocument()

    // Key was verified to be in storage BEFORE POST returned
    expect(keyInStorageDuringPost).toBeTruthy()
    expect(keyInStorageDuringPost).toBe(createOrder.mock.calls[0][1])

    // After success, storage is cleaned up
    expect(getStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id)).toBeNull()
  })

  it('fails closed and NEVER calls POST if storage write fails', async () => {
    const createOrder = vi.fn()
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      listPatientOrders: vi.fn().mockResolvedValue([]),
      createOrderWithOutcome: createOrder,
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])
    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))

    // POST was NEVER called
    expect(createOrder).not.toHaveBeenCalled()

    // Storage unreadable alert is displayed
    expect(
      await screen.findByText(/DHealth ne peut plus vérifier ici si un ordre envoyé précédemment est bien arrivé/),
    ).toBeInTheDocument()

    setItemSpy.mockRestore()
  })

  it('fails closed and NEVER calls POST if storage read-back verification fails', async () => {
    const createOrder = vi.fn()
    let writeHappened = false
    const originalSetItem = Storage.prototype.setItem
    const originalGetItem = Storage.prototype.getItem

    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key, val) {
      writeHappened = true
      return originalSetItem.call(this, key, val)
    })
    // Mock getItem to return null on read-back
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key) {
      if (writeHappened && key.startsWith('dhealth_clinic_pending_order_v1:')) {
        return null // Simulate read-back failure / silent drop
      }
      return originalGetItem.call(this, key)
    })

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      listPatientOrders: vi.fn().mockResolvedValue([]),
      createOrderWithOutcome: createOrder,
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])
    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))

    // POST was NEVER called
    expect(createOrder).not.toHaveBeenCalled()

    // Storage unreadable alert is displayed
    expect(
      await screen.findByText(/DHealth ne peut plus vérifier ici si un ordre envoyé précédemment est bien arrivé/),
    ).toBeInTheDocument()

    vi.restoreAllMocks()
  })

  it('404 on idempotency key lookup preserves unresolved state, does not show abandon button, and keeps new order disabled', async () => {
    setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
      idempotency_key: 'stored-idemp-404-test',
    })

    const getOrderByIdempotencyKey = vi.fn().mockResolvedValue(null) // 404

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      getOrderByIdempotencyKey,
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    // Wait for reconciliation attempt
    expect(getOrderByIdempotencyKey).toHaveBeenCalledWith('provider-a', 'stored-idemp-404-test')

    // Banner indicates unresolved attempt
    expect(await screen.findByText(/Tentative d’ordre non confirmée/)).toBeInTheDocument()

    // Abandon button MUST NOT be present
    expect(screen.queryByRole('button', { name: /Abandonner/i })).not.toBeInTheDocument()

    // "Nouvel ordre de laboratoire" must remain disabled
    expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()
  })

  it('fails closed without Option-C reconstruction when the durable intent is unavailable', async () => {
    const frozenKey = 'frozen-key-after-reload-404'
    setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
      idempotency_key: frozenKey,
    })

    const createOrder = vi.fn()

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null), // 404
      createOrderWithOutcome: createOrder,
      listPatientOrders: vi.fn().mockResolvedValue([]),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])

    expect(await screen.findByText(/Tentative d’ordre non confirmée/)).toBeInTheDocument()

    // Click "Vérifier / réessayer"
    await userEventObj.click(screen.getByRole('button', { name: 'Vérifier / réessayer' }))

    // A missing canonical intent is a reconciliation failure, never permission
    // to reconstruct services from the catalog or issue another mutation.
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    expect(createOrder).not.toHaveBeenCalled()
    expect(getStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id)).not.toBeNull()
    if (createOrder.mock.calls.length > 0) {
    expect(await screen.findByText('Prescrire un ordre de laboratoire')).toBeInTheDocument()

    // Checkboxes are NOT deadlocked: clinician can select items
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    expect(checkboxes[0]).toBeChecked()

    // Advance to review
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    expect(await screen.findByText('Révision de l’ordre de laboratoire')).toBeInTheDocument()

    // Submit
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))

    // Verify submission used the EXACT frozen idempotency key from reload
    expect(createOrder).toHaveBeenCalledTimes(1)
    expect(createOrder.mock.calls[0][1]).toBe(frozenKey)

    // Succeeded and storage cleaned up
    expect(await screen.findByRole('heading', { name: 'Ordre de laboratoire créé avec succès' })).toBeInTheDocument()
    expect(getStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id)).toBeNull()
    }
  })

  it('guarantees zero calls to OpenMRS across native diagnostic ordering', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')

    const client = makeClient({
      listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
      listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
      listPatientOrders: vi.fn().mockResolvedValue([]),
      createOrderWithOutcome: vi.fn().mockResolvedValue({
        order: {
          id: 'ord-zero-openmrs',
          provider_organization_id: 'provider-a',
          patient_reference_id: patientWithName.patient_reference_id,
          practitioner_reference_id: 'prac-1',
          status: 'AWAITING_LAB_SELECTION',
          created_at: '2026-09-16T08:00:00Z',
          items: [{ catalog_entry_id: 'c1', catalog_code_snapshot: 'C1', catalog_display_name_snapshot: 'D1' }],
        },
        created: true,
      }),
    })

    const userEventObj = userEvent.setup()
    render(<StaffApp client={client} initialSection="patients" />)

    const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
    await userEventObj.click(openButtons[0])
    await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
    const checkboxes = await screen.findAllByRole('checkbox')
    await userEventObj.click(checkboxes[0])
    await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
    await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))
    await completeRoutingStep(userEventObj)

    expect(await screen.findByRole('heading', { name: 'Ordre de laboratoire créé avec succès' })).toBeInTheDocument()

    // Inspect all fetch calls
    for (const call of fetchSpy.mock.calls) {
      const input = call[0]
      const url = typeof input === 'string' ? input : (input instanceof URL ? input.toString() : (input as Request)?.url ?? '')
      expect(url).not.toMatch(/\/openmrs\//i)
    }
    fetchSpy.mockRestore()
  })

  // -- DH-CLINIC-003C negative-control coverage closure: the restored durable-intent branch -------
  //
  // The negative-control audit found that `getOrderIntent`/`getOrderIntentByIdempotencyKey` were
  // ALWAYS mocked to resolve `null` across this entire file, so the "a real uncommitted backend
  // intent was found on reload" branch (ClinicPatientsPage.tsx ~446-523) never executed under any
  // existing test -- NC7, NC8, NC9, NC10, NC11, and NC20 all targeted dead code as far as this
  // suite was concerned. These tests make that branch reachable for the first time.

  function makeValidDurableIntent(overrides: Partial<LabOrderIntent> = {}): LabOrderIntent {
    return {
      id: 'intent-backend-real-1',
      idempotency_key: 'reload-key-backend-real-1',
      provider_organization_id: 'provider-a',
      patient_reference_id: patientWithName.patient_reference_id,
      practitioner_reference_id: 'prac-original-author',
      committed_lab_order_id: null,
visit_id: null,
      created_at: '2026-09-16T08:00:00Z',
      items: [
        {
          id: 'intent-item-backend-1',
          catalog_entry_id: 'backend-only-entry-id',
          catalog_code_snapshot: 'BACKEND-SNAP-CODE',
          catalog_display_name_snapshot: 'Backend-Only Snapshot Display',
        },
      ],
      ...overrides,
    }
  }

  describe('restored durable-intent branch (NC7/NC8/NC9/NC10/NC11/NC20 coverage closure)', () => {
    it('TEST A: valid durable intent restores exact backend snapshots and stays read-only', async () => {
      const key = 'reload-key-test-a'
      setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
        idempotency_key: key,
      })

      const intent = makeValidDurableIntent({ idempotency_key: key })
      const getOrderIntentByIdempotencyKey = vi.fn().mockResolvedValue(intent)
      const getOrderIntent = vi.fn().mockResolvedValue(null)
      const createOrderIntentWithOutcome = vi.fn()
      const commitOrderIntentWithOutcome = vi.fn()

      const client = makeClient({
        listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        getOrderIntentByIdempotencyKey,
        getOrderIntent,
        createOrderIntentWithOutcome,
        commitOrderIntentWithOutcome,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])

      // Reachability gate: prove the intent lookup actually fired -- without this assertion a
      // test that silently takes the null-foundIntent path would pass for the wrong reason.
      await waitFor(() =>
        expect(getOrderIntentByIdempotencyKey).toHaveBeenCalledWith('provider-a', key),
      )
      expect(getOrderIntent).not.toHaveBeenCalled()

      expect(await screen.findByText(/Tentative d’ordre non confirmée/)).toBeInTheDocument()
      await userEventObj.click(screen.getByRole('button', { name: 'Vérifier / réessayer' }))

      expect(
        await screen.findByRole('heading', { name: 'Révision de l’ordre de laboratoire' }),
      ).toBeInTheDocument()

      // Services displayed come from foundIntent.items -- values deliberately distinct from
      // mockCatalogTests, so a pass here proves the backend snapshot rendered, not the catalog.
      expect(screen.getByText('BACKEND-SNAP-CODE')).toBeInTheDocument()
      expect(screen.getByText(/Backend-Only Snapshot Display/)).toBeInTheDocument()

      // Durable intent remains immutable/read-only: editable catalog never reopens.
      expect(
        screen.queryByRole('heading', { name: 'Prescrire un ordre de laboratoire' }),
      ).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Modifier les services' })).not.toBeInTheDocument()

      // No new registration, no new idempotency key.
      expect(createOrderIntentWithOutcome).not.toHaveBeenCalled()
      expect(commitOrderIntentWithOutcome).not.toHaveBeenCalled()

      // Pending recovery identity remains stored, still scoped to the exact frozen key.
      const stored = getStoredPendingOrder(
        'identity-1',
        'provider-a',
        patientWithName.patient_reference_id,
      )
      expect(stored).not.toBeNull()
      expect(stored?.idempotency_key).toBe(key)
      expect(stored?.intent_id).toBe(intent.id)
    })

    async function renderMismatchScenario(intentOverrides: Partial<LabOrderIntent>) {
      const key = 'reload-key-mismatch'
      setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
        idempotency_key: key,
      })

      const intent = makeValidDurableIntent({ idempotency_key: key, ...intentOverrides })
      const getOrderIntentByIdempotencyKey = vi.fn().mockResolvedValue(intent)
      const commitOrderIntentWithOutcome = vi.fn()
      const createOrderIntentWithOutcome = vi.fn()

      const client = makeClient({
        listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        getOrderIntentByIdempotencyKey,
        commitOrderIntentWithOutcome,
        createOrderIntentWithOutcome,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])

      await waitFor(() => expect(getOrderIntentByIdempotencyKey).toHaveBeenCalled())

      return { key, intent, commitOrderIntentWithOutcome, createOrderIntentWithOutcome, userEventObj }
    }

    async function assertFailedClosed(
      commitOrderIntentWithOutcome: ReturnType<typeof vi.fn>,
      createOrderIntentWithOutcome: ReturnType<typeof vi.fn>,
      key: string,
    ) {
      // Fail closed: corrupted-storage banner shown, new-order creation blocked.
      expect(await screen.findByText(/Création d’ordres bloquée sur cet appareil/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()

      // Recovered services never attached/rendered -- catalog wizard never opens.
      expect(
        screen.queryByRole('heading', { name: 'Révision de l’ordre de laboratoire' }),
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole('heading', { name: 'Prescrire un ordre de laboratoire' }),
      ).not.toBeInTheDocument()
      expect(screen.queryByText('BACKEND-SNAP-CODE')).not.toBeInTheDocument()

      // Commit is never called on mismatched data.
      expect(commitOrderIntentWithOutcome).not.toHaveBeenCalled()
      // No new idempotency key/registration either.
      expect(createOrderIntentWithOutcome).not.toHaveBeenCalled()

      // Stored recovery identity remains -- fail-closed retains the record for a human to
      // investigate, it does not discard it.
      const stored = getStoredPendingOrder(
        'identity-1',
        'provider-a',
        patientWithName.patient_reference_id,
      )
      expect(stored).not.toBeNull()
      expect(stored?.idempotency_key).toBe(key)
    }

    it('TEST B (NC9): recovered provider mismatch fails closed', async () => {
      const { key, commitOrderIntentWithOutcome, createOrderIntentWithOutcome } =
        await renderMismatchScenario({ provider_organization_id: 'provider-mismatched' })
      await assertFailedClosed(commitOrderIntentWithOutcome, createOrderIntentWithOutcome, key)
    })

    it('TEST C (NC10): recovered patient mismatch fails closed', async () => {
      const { key, commitOrderIntentWithOutcome, createOrderIntentWithOutcome } =
        await renderMismatchScenario({ patient_reference_id: 'patient-mismatched' })
      await assertFailedClosed(commitOrderIntentWithOutcome, createOrderIntentWithOutcome, key)
    })

    it('TEST D (NC11): recovered idempotency-key mismatch fails closed', async () => {
      const { key, commitOrderIntentWithOutcome, createOrderIntentWithOutcome } =
        await renderMismatchScenario({ idempotency_key: 'a-completely-different-key' })
      await assertFailedClosed(commitOrderIntentWithOutcome, createOrderIntentWithOutcome, key)
    })

    it('TEST E: recovered intent-ID mismatch (storage already had intent_id) fails closed', async () => {
      const key = 'reload-key-intent-id-mismatch'
      setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
        idempotency_key: key,
        intent_id: 'intent-expected-by-storage',
      })

      // Storage already knows an intent_id, so this scenario resolves via getOrderIntent (by id),
      // not getOrderIntentByIdempotencyKey.
      const intent = makeValidDurableIntent({
        id: 'intent-DIFFERENT-from-storage',
        idempotency_key: key,
      })
      const getOrderIntent = vi.fn().mockResolvedValue(intent)
      const commitOrderIntentWithOutcome = vi.fn()
      const createOrderIntentWithOutcome = vi.fn()

      const client = makeClient({
        listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        getOrderIntent,
        commitOrderIntentWithOutcome,
        createOrderIntentWithOutcome,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])

      await waitFor(() =>
        expect(getOrderIntent).toHaveBeenCalledWith('provider-a', 'intent-expected-by-storage'),
      )

      await assertFailedClosed(commitOrderIntentWithOutcome, createOrderIntentWithOutcome, key)
    })

    it('TEST H (NC20): recovered uncommitted intent is prior ambiguity, never a fresh attempt', async () => {
      const key = 'reload-key-test-h'
      setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
        idempotency_key: key,
      })

      const intent = makeValidDurableIntent({ idempotency_key: key })
      const getOrderIntentByIdempotencyKey = vi.fn().mockResolvedValue(intent)
      const createOrderIntentWithOutcome = vi.fn()
      const commitOrderIntentWithOutcome = vi
        .fn()
        .mockRejectedValue(new ExchangeApiError(422, 'VALIDATION_ERROR', 'Commit refusé'))

      const client = makeClient({
        listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        getOrderIntentByIdempotencyKey,
        createOrderIntentWithOutcome,
        commitOrderIntentWithOutcome,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])
      await waitFor(() => expect(getOrderIntentByIdempotencyKey).toHaveBeenCalled())

      // Treated as prior ambiguity/reconciliation, not a fresh attempt: the "new order" button
      // is disabled, and opening the wizard lands directly on review (never on catalog/reselection).
      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()

      await userEventObj.click(await screen.findByRole('button', { name: 'Vérifier / réessayer' }))
      expect(
        await screen.findByRole('heading', { name: 'Révision de l’ordre de laboratoire' }),
      ).toBeInTheDocument()
      expect(
        screen.queryByRole('heading', { name: 'Prescrire un ordre de laboratoire' }),
      ).not.toBeInTheDocument()

      // The review-step primary button reflects reconciliation, not a fresh "Confirmer et
      // prescrire" -- proving orderSubmitState is 'uncertain', not 'idle'.
      const commitButton = screen.getByRole('button', { name: 'Vérifier / réessayer' })
      await userEventObj.click(commitButton)

      // Commit was attempted using the existing intent -- never a new registration/key.
      await waitFor(() => expect(commitOrderIntentWithOutcome).toHaveBeenCalledTimes(1))
      expect(commitOrderIntentWithOutcome).toHaveBeenCalledWith('provider-a', intent.id)
      expect(createOrderIntentWithOutcome).not.toHaveBeenCalled()

      // After a definitive commit failure, recovery identity must remain -- not discarded as if
      // this were an ordinary first attempt.
      const stored = getStoredPendingOrder(
        'identity-1',
        'provider-a',
        patientWithName.patient_reference_id,
      )
      expect(stored).not.toBeNull()
      expect(stored?.idempotency_key).toBe(key)
      expect(stored?.intent_id).toBe(intent.id)
    })

    it('TEST F (NC8/NC25): stale Patient A reconciliation cannot lock or attach under Patient B', async () => {
      const keyA = 'reload-key-patient-a'
      setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
        idempotency_key: keyA,
      })

      let resolveA!: (intent: LabOrderIntent | null) => void
      const intentAPromise = new Promise<LabOrderIntent | null>((resolve) => {
        resolveA = resolve
      })
      const intentA = makeValidDurableIntent({ idempotency_key: keyA })

      const commitOrderIntentWithOutcome = vi.fn()
      const getOrderIntentByIdempotencyKey = vi.fn((_providerId: string, key: string) =>
        key === keyA ? intentAPromise : Promise.resolve(null),
      )

      const client = makeClient({
        listPatients: vi.fn().mockResolvedValue([patientEntryWithName, patientEntryWithoutName]),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        getOrderIntentByIdempotencyKey,
        commitOrderIntentWithOutcome,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      // 1-2. Open Patient A -- kicks off the (still-pending) intent lookup for A, proving
      // reconciliation begins.
      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])
      await waitFor(() => expect(getOrderIntentByIdempotencyKey).toHaveBeenCalled())

      // 3. Switch to Patient B (no stored order of her own) while A's request is unresolved.
      await userEventObj.click(screen.getByRole('button', { name: 'Retour aux patients' }))
      const openButtons2 = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons2[1])
      expect(
        await screen.findByRole('heading', { name: 'Nom non renseigné' }),
      ).toBeInTheDocument()

      // 4-5. THE DEFECT REGRESSION: Patient B must not be stuck in reconciliation and her normal
      // new-order action must be available -- BEFORE A's stale response ever resolves. On the old
      // implementation this failed: B inherited A's still-true `reconcilingStoredOrder` flag.
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeEnabled(),
      )
      expect(screen.queryByText(/Tentative d’ordre non confirmée/)).not.toBeInTheDocument()

      // 6. Now resolve Patient A's delayed response with a VALID non-null durable intent.
      await act(async () => {
        resolveA(intentA)
        await intentAPromise
      })

      // 7-9. A's stale response must not attach under B: no backend snapshot, no banner, new-order
      // still available.
      expect(screen.queryByText('BACKEND-SNAP-CODE')).not.toBeInTheDocument()
      expect(screen.queryByText(/Tentative d’ordre non confirmée/)).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeEnabled()

      // 10. No commit occurs for A while scoped to B.
      expect(commitOrderIntentWithOutcome).not.toHaveBeenCalled()

      // Patient A's own recovery identity remains untouched and scoped to A.
      const storedA = getStoredPendingOrder(
        'identity-1',
        'provider-a',
        patientWithName.patient_reference_id,
      )
      expect(storedA).not.toBeNull()
      expect(storedA?.idempotency_key).toBe(keyA)
      const storedB = getStoredPendingOrder(
        'identity-1',
        'provider-a',
        patientWithoutName.patient_reference_id,
      )
      expect(storedB).toBeNull()
    })

    it('TEST F2 (NC25): stale Patient A resolution cannot clear Patient B\'s OWN active reconciliation', async () => {
      // Guards against the naive "fix" of always forcing reconcilingStoredOrder to false: if
      // Patient B ALSO has her own genuinely-pending reconciliation, A's resolution (stale, from a
      // different generation/patient) must never be the thing that clears B's flag -- only B's own
      // current-generation completion may do that.
      const keyA = 'reload-key-f2-patient-a'
      const keyB = 'reload-key-f2-patient-b'
      setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
        idempotency_key: keyA,
      })
      setStoredPendingOrder('identity-1', 'provider-a', patientWithoutName.patient_reference_id, {
        idempotency_key: keyB,
      })

      let resolveA!: (intent: LabOrderIntent | null) => void
      const intentAPromise = new Promise<LabOrderIntent | null>((resolve) => {
        resolveA = resolve
      })
      // Patient B's own lookup never resolves in this test -- her reconciliation must stay active.
      const intentBPromise = new Promise<LabOrderIntent | null>(() => {})

      const getOrderIntentByIdempotencyKey = vi.fn((_providerId: string, key: string) => {
        if (key === keyA) return intentAPromise
        if (key === keyB) return intentBPromise
        return Promise.resolve(null)
      })

      const client = makeClient({
        listPatients: vi.fn().mockResolvedValue([patientEntryWithName, patientEntryWithoutName]),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        getOrderIntentByIdempotencyKey,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      // Open Patient A -- her reconciliation begins.
      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])
      await waitFor(() =>
        expect(getOrderIntentByIdempotencyKey).toHaveBeenCalledWith('provider-a', keyA),
      )

      // Switch to Patient B, who has her OWN pending reconciliation.
      await userEventObj.click(screen.getByRole('button', { name: 'Retour aux patients' }))
      const openButtons2 = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons2[1])
      await waitFor(() =>
        expect(getOrderIntentByIdempotencyKey).toHaveBeenCalledWith('provider-a', keyB),
      )

      // B's own reconciliation is genuinely in flight -- new-order creation stays blocked.
      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()

      // Now A's STALE response resolves. It must NOT clear B's active reconciliation.
      await act(async () => {
        resolveA(makeValidDurableIntent({ idempotency_key: keyA }))
        await intentAPromise
      })

      // B remains correctly blocked -- A's stale completion did not fail B open.
      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()
      expect(screen.queryByText('BACKEND-SNAP-CODE')).not.toBeInTheDocument()
    })

    it('TEST G (NC7/NC25): stale Provider A reconciliation cannot lock or attach under Provider B', async () => {
      const keyA = 'reload-key-provider-a'
      setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
        idempotency_key: keyA,
      })

      let resolveA!: (intent: LabOrderIntent | null) => void
      const intentAPromise = new Promise<LabOrderIntent | null>((resolve) => {
        resolveA = resolve
      })
      const intentA = makeValidDurableIntent({ idempotency_key: keyA })

      const commitOrderIntentWithOutcome = vi.fn()
      const getOrderIntentByIdempotencyKey = vi.fn((providerId: string) =>
        providerId === 'provider-a' ? intentAPromise : Promise.resolve(null),
      )

      const client = makeClient({
        getMe: vi.fn().mockResolvedValue({
          ...me,
          memberships: [
            me.memberships[0],
            { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
          ],
        }),
        listPatients: vi.fn((providerId: string) =>
          Promise.resolve(providerId === 'provider-a' ? [patientEntryWithName] : [patientEntryWithoutName]),
        ),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        getOrderIntentByIdempotencyKey,
        commitOrderIntentWithOutcome,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      // Open the Provider-A patient -- kicks off the (still-pending) intent lookup for A.
      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])
      await waitFor(() => expect(getOrderIntentByIdempotencyKey).toHaveBeenCalled())

      // Switch to Provider B before A's lookup resolves, and open B's own (unrelated) patient.
      const providerSelect = screen.getByLabelText("Établissement actif")
      await userEventObj.selectOptions(providerSelect, 'provider-b')
      expect(await screen.findByText('Patients trouvés')).toBeInTheDocument()
      const openButtonsB = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtonsB[0])
      expect(
        await screen.findByRole('heading', { name: 'Nom non renseigné' }),
      ).toBeInTheDocument()

      // THE DEFECT REGRESSION: Provider B's patient must not be stuck in reconciliation, and
      // her normal new-order action must be available -- BEFORE A's stale response resolves.
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeEnabled(),
      )
      expect(screen.queryByText(/Tentative d’ordre non confirmée/)).not.toBeInTheDocument()

      // Now resolve Provider A's delayed response with a VALID durable intent.
      await act(async () => {
        resolveA(intentA)
        await intentAPromise
      })

      // Nothing from Provider A leaks into Provider B's UI.
      expect(screen.queryByText('BACKEND-SNAP-CODE')).not.toBeInTheDocument()
      expect(screen.queryByText(/Tentative d’ordre non confirmée/)).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeEnabled()
      expect(commitOrderIntentWithOutcome).not.toHaveBeenCalled()

      const storedA = getStoredPendingOrder(
        'identity-1',
        'provider-a',
        patientWithName.patient_reference_id,
      )
      expect(storedA).not.toBeNull()
      expect(storedA?.idempotency_key).toBe(keyA)
    })

    it('TEST G2 (NC25): stale Provider A resolution cannot clear Provider B\'s OWN active reconciliation', async () => {
      const keyA = 'reload-key-g2-provider-a'
      const keyB = 'reload-key-g2-provider-b'
      setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
        idempotency_key: keyA,
      })
      setStoredPendingOrder('identity-1', 'provider-b', patientWithoutName.patient_reference_id, {
        idempotency_key: keyB,
      })

      let resolveA!: (intent: LabOrderIntent | null) => void
      const intentAPromise = new Promise<LabOrderIntent | null>((resolve) => {
        resolveA = resolve
      })
      // Provider B's own lookup never resolves in this test -- her reconciliation must stay active.
      const intentBPromise = new Promise<LabOrderIntent | null>(() => {})

      const getOrderIntentByIdempotencyKey = vi.fn((_providerId: string, key: string) => {
        if (key === keyA) return intentAPromise
        if (key === keyB) return intentBPromise
        return Promise.resolve(null)
      })

      const client = makeClient({
        getMe: vi.fn().mockResolvedValue({
          ...me,
          memberships: [
            me.memberships[0],
            { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
          ],
        }),
        listPatients: vi.fn((providerId: string) =>
          Promise.resolve(providerId === 'provider-a' ? [patientEntryWithName] : [patientEntryWithoutName]),
        ),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        getOrderIntentByIdempotencyKey,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])
      await waitFor(() =>
        expect(getOrderIntentByIdempotencyKey).toHaveBeenCalledWith('provider-a', keyA),
      )

      const providerSelect = screen.getByLabelText("Établissement actif")
      await userEventObj.selectOptions(providerSelect, 'provider-b')
      const openButtonsB = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtonsB[0])
      await waitFor(() =>
        expect(getOrderIntentByIdempotencyKey).toHaveBeenCalledWith('provider-b', keyB),
      )

      // B's own reconciliation is genuinely in flight -- new-order creation stays blocked.
      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()

      // A's STALE response resolves -- must not clear B's active reconciliation.
      await act(async () => {
        resolveA(makeValidDurableIntent({ idempotency_key: keyA }))
        await intentAPromise
      })

      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()
      expect(screen.queryByText('BACKEND-SNAP-CODE')).not.toBeInTheDocument()
    })

    // -- DH-CLINIC-003C negative-control coverage closure: NC4 permanent regression -------------
    //
    // The audit found no test mocks a rejection at the COMMIT step specifically (only at
    // registration, via the legacy-shim path). These exercise the real two-step Option-A flow:
    // registration succeeds, durable recovery is persisted, THEN commit is rejected.

    async function commitStepRejectionScenario(error: ExchangeApiError) {
      const commitOrderIntentWithOutcome = vi.fn().mockRejectedValue(error)
      const createOrderIntentWithOutcome = vi.fn().mockImplementation(
        (providerId: string, key: string, body: { patient_reference_id: string; catalog_entry_ids: string[] }) =>
          Promise.resolve({
            created: true,
            intent: {
              id: `intent-${key}`,
              idempotency_key: key,
              provider_organization_id: providerId,
              patient_reference_id: body.patient_reference_id,
              practitioner_reference_id: 'prac-clinician',
              committed_lab_order_id: null,
visit_id: null,
              created_at: new Date().toISOString(),
              items: body.catalog_entry_ids.map((id) => ({
                catalog_entry_id: id,
                catalog_code_snapshot: 'CODE',
                catalog_display_name_snapshot: 'Display',
              })),
            },
          }),
      )

      const client = makeClient({
        listPatients: vi.fn().mockResolvedValue([patientEntryWithName]),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        createOrderIntentWithOutcome,
        commitOrderIntentWithOutcome,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])
      await userEventObj.click(await screen.findByRole('button', { name: 'Nouvel ordre de laboratoire' }))
      const checkboxes = await screen.findAllByRole('checkbox')
      await userEventObj.click(checkboxes[0])
      await userEventObj.click(screen.getByRole('button', { name: 'Suivant : Réviser l’ordre' }))
      await userEventObj.click(screen.getByRole('button', { name: 'Confirmer et prescrire' }))

      await waitFor(() => expect(createOrderIntentWithOutcome).toHaveBeenCalledTimes(1))
      await waitFor(() => expect(commitOrderIntentWithOutcome).toHaveBeenCalledTimes(1))

      return { createOrderIntentWithOutcome, commitOrderIntentWithOutcome, userEventObj }
    }

    it('TEST I (NC4): fresh registration succeeds, commit returns 422 -- recovery retained', async () => {
      const { createOrderIntentWithOutcome } = await commitStepRejectionScenario(
        new ExchangeApiError(422, 'VALIDATION_ERROR', 'Commit refusé (validation)'),
      )

      // Pending durable recovery identity remains in localStorage, WITH the intent ID.
      const stored = getStoredPendingOrder(
        'identity-1',
        'provider-a',
        patientWithName.patient_reference_id,
      )
      expect(stored).not.toBeNull()
      expect(stored?.intent_id).toBeTruthy()

      // No new key generated, no re-registration.
      expect(createOrderIntentWithOutcome).toHaveBeenCalledTimes(1)

      // Catalog does not reopen as a fresh submission; services are not reconstructed.
      expect(
        screen.queryByRole('heading', { name: 'Prescrire un ordre de laboratoire' }),
      ).not.toBeInTheDocument()

      // User remains in reconciliation/retry state.
      expect(screen.getByRole('button', { name: 'Vérifier / réessayer' })).toBeInTheDocument()
    })

    it('TEST J (NC4): fresh registration succeeds, commit returns 403 -- recovery retained', async () => {
      const { createOrderIntentWithOutcome } = await commitStepRejectionScenario(
        new ExchangeApiError(403, 'FORBIDDEN', 'Commit refusé (autorisation)'),
      )

      const stored = getStoredPendingOrder(
        'identity-1',
        'provider-a',
        patientWithName.patient_reference_id,
      )
      expect(stored).not.toBeNull()
      expect(stored?.intent_id).toBeTruthy()
      expect(createOrderIntentWithOutcome).toHaveBeenCalledTimes(1)
      expect(
        screen.queryByRole('heading', { name: 'Prescrire un ordre de laboratoire' }),
      ).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Vérifier / réessayer' })).toBeInTheDocument()
    })
  })

  describe('unreadable recovery state cannot leak across scope (NC26)', () => {
    it('NC26-A: recovered-intent mismatch on Patient A does not poison Patient B', async () => {
      const keyA = 'nc26a-key-patient-a'
      setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
        idempotency_key: keyA,
      })

      const mismatchedIntent = makeValidDurableIntent({
        idempotency_key: keyA,
        patient_reference_id: 'some-other-patient-entirely',
      })
      const getOrderIntentByIdempotencyKey = vi.fn().mockResolvedValue(mismatchedIntent)
      const commitOrderIntentWithOutcome = vi.fn()

      const client = makeClient({
        listPatients: vi.fn().mockResolvedValue([patientEntryWithName, patientEntryWithoutName]),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        getOrderIntentByIdempotencyKey,
        commitOrderIntentWithOutcome,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      // Open Patient A -- the mismatch resolves and A becomes fail-closed.
      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])
      await waitFor(() => expect(getOrderIntentByIdempotencyKey).toHaveBeenCalled())
      expect(await screen.findByText(/Création d’ordres bloquée sur cet appareil/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()

      // Switch to Patient B, who has no recovery problem of her own.
      await userEventObj.click(screen.getByRole('button', { name: 'Retour aux patients' }))
      const openButtons2 = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons2[1])
      expect(
        await screen.findByRole('heading', { name: 'Nom non renseigné' }),
      ).toBeInTheDocument()

      // B is not poisoned by A's fail-closed state.
      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeEnabled()
      expect(screen.queryByText(/Création d’ordres bloquée sur cet appareil/)).not.toBeInTheDocument()
      expect(screen.queryByText('BACKEND-SNAP-CODE')).not.toBeInTheDocument()
      expect(commitOrderIntentWithOutcome).not.toHaveBeenCalled()
    })

    it('NC26-B: corrupt Patient A storage does not poison Patient B', async () => {
      localStorage.setItem(
        'dhealth_clinic_pending_order_v1:identity-1:provider-a:' +
          patientWithName.patient_reference_id,
        '{corrupt-json-not-parseable',
      )

      const client = makeClient({
        listPatients: vi.fn().mockResolvedValue([patientEntryWithName, patientEntryWithoutName]),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])
      expect(await screen.findByText(/Création d’ordres bloquée sur cet appareil/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()

      await userEventObj.click(screen.getByRole('button', { name: 'Retour aux patients' }))
      const openButtons2 = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons2[1])
      expect(
        await screen.findByRole('heading', { name: 'Nom non renseigné' }),
      ).toBeInTheDocument()

      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeEnabled()
      expect(screen.queryByText(/Création d’ordres bloquée sur cet appareil/)).not.toBeInTheDocument()
    })

    it('NC26-C: a genuinely global storage failure re-arms fail-closed on every scope, not just the first', async () => {
      const getItemSpy = vi
        .spyOn(Storage.prototype, 'getItem')
        .mockImplementation(() => {
          throw new Error('Storage API disabled')
        })

      try {
        const client = makeClient({
          listPatients: vi.fn().mockResolvedValue([patientEntryWithName, patientEntryWithoutName]),
          listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
          listPatientOrders: vi.fn().mockResolvedValue([]),
          getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        })

        const userEventObj = userEvent.setup()
        render(<StaffApp client={client} initialSection="patients" />)

        const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
        await userEventObj.click(openButtons[0])
        expect(await screen.findByText(/Création d’ordres bloquée sur cet appareil/)).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()

        // Switch to Patient B -- storage is STILL globally broken, so the synchronous scope reset
        // must NOT make B fail open; B's own effect run must detect the same global failure and
        // re-arm fail-closed.
        await userEventObj.click(screen.getByRole('button', { name: 'Retour aux patients' }))
        const openButtons2 = await screen.findAllByRole('button', { name: 'Ouvrir' })
        await userEventObj.click(openButtons2[1])
        expect(
          await screen.findByRole('heading', { name: 'Nom non renseigné' }),
        ).toBeInTheDocument()

        expect(await screen.findByText(/Création d’ordres bloquée sur cet appareil/)).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()
      } finally {
        getItemSpy.mockRestore()
      }
    })

    it('NC26-D: Provider A scoped bad recovery state does not poison Provider B', async () => {
      const keyA = 'nc26d-key-provider-a'
      setStoredPendingOrder('identity-1', 'provider-a', patientWithName.patient_reference_id, {
        idempotency_key: keyA,
      })

      const mismatchedIntent = makeValidDurableIntent({
        idempotency_key: keyA,
        provider_organization_id: 'provider-mismatched-entirely',
      })
      const getOrderIntentByIdempotencyKey = vi.fn().mockResolvedValue(mismatchedIntent)

      const client = makeClient({
        getMe: vi.fn().mockResolvedValue({
          ...me,
          memberships: [
            me.memberships[0],
            { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
          ],
        }),
        listPatients: vi.fn((providerId: string) =>
          Promise.resolve(providerId === 'provider-a' ? [patientEntryWithName] : [patientEntryWithoutName]),
        ),
        listCatalogTests: vi.fn().mockResolvedValue(mockCatalogTests),
        listPatientOrders: vi.fn().mockResolvedValue([]),
        getOrderByIdempotencyKey: vi.fn().mockResolvedValue(null),
        getOrderIntentByIdempotencyKey,
      })

      const userEventObj = userEvent.setup()
      render(<StaffApp client={client} initialSection="patients" />)

      const openButtons = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtons[0])
      await waitFor(() => expect(getOrderIntentByIdempotencyKey).toHaveBeenCalled())
      expect(await screen.findByText(/Création d’ordres bloquée sur cet appareil/)).toBeInTheDocument()

      const providerSelect = screen.getByLabelText("Établissement actif")
      await userEventObj.selectOptions(providerSelect, 'provider-b')
      const openButtonsB = await screen.findAllByRole('button', { name: 'Ouvrir' })
      await userEventObj.click(openButtonsB[0])
      expect(
        await screen.findByRole('heading', { name: 'Nom non renseigné' }),
      ).toBeInTheDocument()

      expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeEnabled()
      expect(screen.queryByText(/Création d’ordres bloquée sur cet appareil/)).not.toBeInTheDocument()
    })
  })
})
