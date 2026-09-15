import { StrictMode } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../api/exchangeClient'
import type {
  ClinicPatientSummary,
  ExchangeMeResponse,
  PatientReferenceBindRequest,
} from '../../api/exchangeTypes'
import {
  clearAllStoredPendingPatients,
  getStoredPendingPatient,
  setStoredPendingPatient,
} from './pendingPatientStorage'
import { ClinicApp } from './ClinicApp'

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
    },
  ],
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue(me),
    listOrders: vi.fn().mockResolvedValue([]),
    getOrder: vi.fn(),
    getOrderResults: vi.fn(),
    getReleaseVersions: vi.fn(),
    bindPatientReference: vi.fn(),
    bindPatientReferenceWithOutcome: vi.fn(),
    listPatientReferences: vi.fn().mockResolvedValue([]),
    getPatientReference: vi.fn(),
    ...overrides,
  } as unknown as ExchangeClient
}

describe('Clinic native patient workflow', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    clearAllStoredPendingPatients()
    localStorage.clear()
  })

  it('accepts completed patient requests under the application Strict Mode lifecycle', async () => {
    const client = makeClient({
      listPatientReferences: vi.fn().mockResolvedValue([patientWithName]),
    })

    render(
      <StrictMode>
        <ClinicApp client={client} initialTab="patients" />
      </StrictMode>,
    )

    expect(await screen.findByText('Patient Synthétique')).toBeInTheDocument()
  })

  it('renders loading, results, opens an existing patient, and uses the truthful null-name fallback', async () => {
    let resolveList!: (patients: ClinicPatientSummary[]) => void
    const listPromise = new Promise<ClinicPatientSummary[]>((resolve) => { resolveList = resolve })
    const client = makeClient({ listPatientReferences: vi.fn().mockReturnValue(listPromise) })

    render(<ClinicApp client={client} initialTab="patients" />)
    expect(await screen.findByText('Chargement des patients…')).toBeInTheDocument()

    resolveList([patientWithName, patientWithoutName])
    expect(await screen.findByText('Patient Synthétique')).toBeInTheDocument()
    expect(screen.getAllByText(patientWithoutName.local_reference)).toHaveLength(2)

    const openButtons = screen.getAllByRole('button', { name: 'Ouvrir' })
    await userEvent.setup().click(openButtons[1])
    expect(await screen.findByRole('heading', { name: patientWithoutName.local_reference })).toBeInTheDocument()
    expect(screen.getByText(patientWithoutName.patient_reference_id)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouvel ordre de laboratoire' })).toBeDisabled()
  })

  it('shows the empty state, creates a minimal opaque reference, fetches it, and opens the workspace', async () => {
    const bind = vi.fn().mockResolvedValue({
      reference: {
        id: patientWithName.patient_reference_id,
        source_organization_id: 'provider-a',
        source_system: 'dhealth-clinic',
        source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
        source_identity_value: patientWithName.local_reference,
      },
      created: true,
    })
    const get = vi.fn().mockResolvedValue(patientWithName)
    const client = makeClient({
      bindPatientReferenceWithOutcome: bind,
      getPatientReference: get,
    })

    render(<ClinicApp client={client} initialTab="patients" />)
    expect(await screen.findByText('Aucun patient trouvé')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Patient Synthétique')
    await user.type(screen.getByLabelText('Téléphone (facultatif)'), '+25377000000')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
    expect(bind).toHaveBeenCalledTimes(1)
    const submitted = bind.mock.calls[0][1]
    expect(submitted).toMatchObject({
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      display_name: 'Patient Synthétique',
      contact_channel_type: 'PHONE',
      contact_value: '+25377000000',
    })
    expect(submitted.source_identity_value).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    )
    expect(get).toHaveBeenCalledWith(
      'provider-a',
      patientWithName.patient_reference_id,
      undefined,
    )
  })

  it('submits provider-local search text and renders the returned result', async () => {
    const list = vi.fn()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([patientWithName])
    const client = makeClient({ listPatientReferences: list })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="patients" />)

    await screen.findByText('Aucun patient trouvé')
    await user.type(screen.getByLabelText('Rechercher un patient'), 'Synthétique')
    await user.click(screen.getByRole('button', { name: 'Rechercher' }))

    expect(await screen.findByText('Patient Synthétique')).toBeInTheDocument()
    expect(list).toHaveBeenLastCalledWith('provider-a', {
      query: 'Synthétique',
      limit: 50,
      token: undefined,
    })
  })

  it('reuses the original opaque identity and payload after an ambiguous response and tab navigation', async () => {
    const bind = vi
      .fn()
      .mockRejectedValueOnce(new ExchangeApiError(0, 'NETWORK_ERROR', 'raw transport detail'))
      .mockResolvedValueOnce({
        reference: {
          id: patientWithName.patient_reference_id,
          source_organization_id: 'provider-a',
          source_system: 'dhealth-clinic',
          source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
          source_identity_value: patientWithName.local_reference,
        },
        created: false,
      })
    const client = makeClient({
      bindPatientReferenceWithOutcome: bind,
      getPatientReference: vi.fn().mockResolvedValue(authoritativeReplayPatient),
    })
    const user = userEvent.setup()

    render(<ClinicApp client={client} initialTab="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Patient Synthétique')
    await user.type(screen.getByLabelText('Téléphone (facultatif)'), '+25377000000')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText('Réessayez pour vérifier la même référence sans créer un doublon.')).toBeInTheDocument()
    expect(screen.queryByText('raw transport detail')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Accueil' }))
    await screen.findByRole('heading', { name: 'Accueil Clinique' })
    await user.click(screen.getByRole('button', { name: 'Patients' }))
    await screen.findByRole('heading', { name: 'Nouveau patient' })
    expect(screen.getByLabelText('Nom affiché (obligatoire)')).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(await screen.findByRole('heading', { name: 'Nom stocké autoritatif' })).toBeInTheDocument()
    expect(screen.getByText('+25377111111')).toBeInTheDocument()
    expect(screen.queryByText('+25377000000')).not.toBeInTheDocument()
    expect(bind).toHaveBeenCalledTimes(2)
    expect(bind.mock.calls[1][1]).toEqual(bind.mock.calls[0][1])
  })

  it('retains the exact pending tuple when authoritative detail retrieval fails', async () => {
    const bind = vi.fn().mockResolvedValue({
      reference: {
        id: patientWithName.patient_reference_id,
        source_organization_id: 'provider-a',
        source_system: 'dhealth-clinic',
        source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
        source_identity_value: patientWithName.local_reference,
      },
      created: false,
    })
    const get = vi.fn()
      .mockRejectedValueOnce(new ExchangeApiError(403, 'FORBIDDEN', 'raw detail failure'))
      .mockResolvedValueOnce(authoritativeReplayPatient)
    const client = makeClient({
      bindPatientReferenceWithOutcome: bind,
      getPatientReference: get,
    })
    const user = userEvent.setup()

    render(<ClinicApp client={client} initialTab="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Nom saisi')
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
      const bind = vi.fn().mockResolvedValue({
        reference: {
          id: patientWithName.patient_reference_id,
          source_organization_id: 'provider-a',
          source_system: 'dhealth-clinic',
          source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
          source_identity_value: patientWithName.local_reference,
        },
        created,
      })
      const get = vi
        .fn()
        .mockRejectedValueOnce(error)
        .mockResolvedValueOnce(patientWithName)
      const client = makeClient({ bindPatientReferenceWithOutcome: bind, getPatientReference: get })
      const user = userEvent.setup()
      render(<ClinicApp client={client} initialTab="patients" />)
      await screen.findByText('Aucun patient trouvé')
      await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
      await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Patient Synthétique')
      await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

      // Confirmed recovery state
      expect(await screen.findByRole('button', { name: 'Réessayer l’ouverture' })).toBeInTheDocument()
      expect(screen.queryByText(/Tentative non confirmée/)).not.toBeInTheDocument()
      expect(screen.getByText(/Patient créé/)).toBeInTheDocument()
      expect(bind).toHaveBeenCalledTimes(1)
      const identity = bind.mock.calls[0][1].source_identity_value
      expect(typeof identity).toBe('string')
      expect(getStoredPendingPatient('identity-1', 'provider-a')).toMatchObject({
        source_identity_value: identity,
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
    const client = makeClient({ bindPatientReferenceWithOutcome: bind })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Patient Synthétique')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText(/Impossible de preparer la recuperation sure/)).toBeInTheDocument()
    expect(bind).not.toHaveBeenCalled()
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toBeNull()
  })

  it('fails closed without POST when storage read-back throws during pre-POST verification', async () => {
    const bind = vi.fn()
    const client = makeClient({ bindPatientReferenceWithOutcome: bind })
    const originalGetItem = Storage.prototype.getItem
    // Allow initial mount check to succeed, but throw when verifying write before POST
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key: string) {
      if (key.startsWith('dhealth_clinic_pending_patient_v1:identity-1:provider-a')) {
        // Return null for the initial query, but throw if setStoredPendingPatient calls getItem
        if (this.length > 0) {
          throw new Error('read-after-write failed')
        }
      }
      return originalGetItem.call(this, key)
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Patient Synthétique')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText(/Impossible de preparer la recuperation sure/)).toBeInTheDocument()
    expect(bind).not.toHaveBeenCalled()
  })

  it('fails closed without POST when storage read-back is mismatched during pre-POST verification', async () => {
    const bind = vi.fn()
    const client = makeClient({ bindPatientReferenceWithOutcome: bind })
    const originalGetItem = Storage.prototype.getItem
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key: string) {
      if (key.startsWith('dhealth_clinic_pending_patient_v1:identity-1:provider-a')) {
        // If an entry was written, simulate corrupted read-back
        if (this.length > 0) {
          return '{"corrupted":true}'
        }
      }
      return originalGetItem.call(this, key)
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Patient Synthétique')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText(/Impossible de preparer la recuperation sure/)).toBeInTheDocument()
    expect(bind).not.toHaveBeenCalled()
  })

  it('fails closed when the scoped pending-store cannot be read', async () => {
    const bind = vi.fn()
    const client = makeClient({ bindPatientReferenceWithOutcome: bind })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    render(<ClinicApp client={client} initialTab="patients" />)
    await screen.findByText('Aucun patient trouvé')
    expect(await screen.findByText(/Impossible de verifier la recuperation sure/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).toBeDisabled()
    expect(bind).not.toHaveBeenCalled()
  })

  it('renders a sanitized recoverable error without exposing raw backend details', async () => {
    const client = makeClient({
      listPatientReferences: vi.fn().mockRejectedValue(
        new ExchangeApiError(500, 'SERVER_ERROR', 'Traceback: secret database details'),
      ),
    })
    render(<ClinicApp client={client} initialTab="patients" />)

    expect(await screen.findByText('Erreur du serveur')).toBeInTheDocument()
    expect(screen.queryByText(/Traceback|secret database details/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Réessayer' })).toBeInTheDocument()
  })

  it('does not restore stale provider A results after switching to provider B', async () => {
    let resolveA!: (patients: ClinicPatientSummary[]) => void
    const providerAPromise = new Promise<ClinicPatientSummary[]>((resolve) => { resolveA = resolve })
    const client = makeClient({
      getMe: vi.fn().mockResolvedValue({
        ...me,
        memberships: [
          me.memberships[0],
          { ...me.memberships[0], organization_id: 'provider-b', organization_name: 'Clinique B' },
        ],
      }),
      listPatientReferences: vi.fn((providerId: string) =>
        providerId === 'provider-a' ? providerAPromise : Promise.resolve([patientWithoutName]),
      ),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="patients" />)

    await user.selectOptions(await screen.findByLabelText("Sélectionner l'établissement"), 'provider-b')
    expect(await screen.findAllByText(patientWithoutName.local_reference)).toHaveLength(2)
    await act(async () => {
      resolveA([patientWithName])
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
      listPatientReferences: vi.fn((providerId: string) =>
        Promise.resolve(providerId === 'provider-a' ? [patientWithName] : [patientWithoutName]),
      ),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="patients" />)

    await screen.findByText('Patient Synthétique')
    await user.click(screen.getByRole('button', { name: 'Ouvrir' }))
    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText("Sélectionner l'établissement"), 'provider-b')
    expect(await screen.findAllByText(patientWithoutName.local_reference)).toHaveLength(2)
    expect(screen.queryByRole('heading', { name: 'Patient Synthétique' })).not.toBeInTheDocument()
  })

  it('keeps unresolved creation identities isolated through an A to B to A provider roundtrip', async () => {
    let providerAAttempts = 0
    const bind = vi.fn((providerId: string, _body: PatientReferenceBindRequest) => {
      if (providerId === 'provider-a') {
        providerAAttempts += 1
        if (providerAAttempts === 1) {
          return Promise.reject(new ExchangeApiError(0, 'NETWORK_ERROR', 'provider A transport'))
        }
        return Promise.resolve({
          reference: {
            id: patientWithName.patient_reference_id,
            source_organization_id: 'provider-a',
            source_system: 'dhealth-clinic',
            source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
            source_identity_value: patientWithName.local_reference,
          },
          created: false,
        })
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
      bindPatientReferenceWithOutcome: bind,
      getPatientReference: vi.fn().mockResolvedValue(authoritativeReplayPatient),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="patients" />)

    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Tentative A')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    await screen.findByText('Réessayez pour vérifier la même référence sans créer un doublon.')

    const providerSelect = screen.getByLabelText("Sélectionner l'établissement")
    await user.selectOptions(providerSelect, 'provider-b')
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Tentative B')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    await screen.findByText('Réessayez pour vérifier la même référence sans créer un doublon.')

    await user.selectOptions(providerSelect, 'provider-a')
    await screen.findByRole('heading', { name: 'Nouveau patient' })
    expect(screen.getByLabelText('Nom affiché (obligatoire)')).toHaveValue('Tentative A')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByRole('heading', { name: 'Nom stocké autoritatif' })).toBeInTheDocument()

    expect(bind).toHaveBeenCalledTimes(3)
    expect(bind.mock.calls[2][1]).toEqual(bind.mock.calls[0][1])
    expect(bind.mock.calls[1][1].source_identity_value).not.toBe(
      bind.mock.calls[0][1].source_identity_value,
    )
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
      bindPatientReferenceWithOutcome: vi.fn().mockReturnValue(bindPromise),
      getPatientReference: vi.fn().mockResolvedValue(patientWithName),
    })
    const user = userEvent.setup()
    render(<ClinicApp client={client} initialTab="patients" />)

    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Patient Synthétique')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    const providerSelect = screen.getByLabelText("Sélectionner l'établissement")
    await user.selectOptions(providerSelect, 'provider-b')
    await waitFor(() => expect(providerSelect).toHaveValue('provider-b'))

    resolveBind({
      reference: {
        id: patientWithName.patient_reference_id,
        source_organization_id: 'provider-a',
        source_system: 'dhealth-clinic',
        source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
        source_identity_value: patientWithName.local_reference,
      },
      created: true,
    })
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Patient Synthétique' })).not.toBeInTheDocument()
      expect(screen.getByText('Aucun patient trouvé')).toBeInTheDocument()
    })
    expect(client.getPatientReference).not.toHaveBeenCalled()
    expect(getStoredPendingPatient('identity-1', 'provider-b')).toBeNull()
    expect(screen.queryByText(/Patient créé/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Tentative non confirmée/)).not.toBeInTheDocument()
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toMatchObject({
      confirmed_patient_reference_id: patientWithName.patient_reference_id,
    })
    await user.selectOptions(providerSelect, 'provider-a')
    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
    expect(client.getPatientReference).toHaveBeenCalledWith(
      'provider-a', patientWithName.patient_reference_id, undefined,
    )
  })

  it('blocks patient creation when display name is empty or only whitespace', async () => {
    const bind = vi.fn()
    const client = makeClient({
      bindPatientReferenceWithOutcome: bind,
    })
    const user = userEvent.setup()

    render(<ClinicApp client={client} initialTab="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))

    // Empty display name
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText('Le nom affiché est obligatoire.')).toBeInTheDocument()
    expect(bind).not.toHaveBeenCalled()

    // Whitespace display name
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), '   ')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))
    expect(await screen.findByText('Le nom affiché est obligatoire.')).toBeInTheDocument()
    expect(bind).not.toHaveBeenCalled()
  })

  it('handles definitive failure by clearing pending state and leaving form editable with fresh identity', async () => {
    const bind = vi
      .fn()
      .mockRejectedValueOnce(new ExchangeApiError(422, 'VALIDATION_ERROR', 'invalid format'))
      .mockResolvedValueOnce({
        reference: {
          id: patientWithName.patient_reference_id,
          source_organization_id: 'provider-a',
          source_system: 'dhealth-clinic',
          source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
          source_identity_value: patientWithName.local_reference,
        },
        created: true,
      })
    const client = makeClient({
      bindPatientReferenceWithOutcome: bind,
      getPatientReference: vi.fn().mockResolvedValue(patientWithName),
    })
    const user = userEvent.setup()

    render(<ClinicApp client={client} initialTab="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Tentative Definitive')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    // Shows validation error without ambiguous duplicate warning
    expect(await screen.findByText('Requête invalide')).toBeInTheDocument()
    expect(
      screen.queryByText('Réessayez pour vérifier la même référence sans créer un doublon.'),
    ).not.toBeInTheDocument()

    // Form remains enabled
    const input = screen.getByLabelText('Nom affiché (obligatoire)')
    expect(input).not.toBeDisabled()

    // No pending state in storage
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toBeNull()

    // Submitting again mints a fresh UUID
    await user.clear(input)
    await user.type(input, 'Tentative Corrigee')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(bind).toHaveBeenCalledTimes(2)
    const firstCallUUID = bind.mock.calls[0][1].source_identity_value
    const secondCallUUID = bind.mock.calls[1][1].source_identity_value
    expect(firstCallUUID).not.toBe(secondCallUUID)
    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
  })

  it('persists strictly non-PHI tuple to storage on ambiguous failure', async () => {
    const bind = vi.fn().mockRejectedValue(new ExchangeApiError(0, 'NETWORK_ERROR', 'network down'))
    const client = makeClient({ bindPatientReferenceWithOutcome: bind })
    const user = userEvent.setup()

    render(<ClinicApp client={client} initialTab="patients" />)
    await screen.findByText('Aucun patient trouvé')
    await user.click(screen.getByRole('button', { name: 'Nouveau patient' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Patient Secret PHI')
    await user.type(screen.getByLabelText('Téléphone (facultatif)'), '+25377999888')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(await screen.findByText('Réessayez pour vérifier la même référence sans créer un doublon.')).toBeInTheDocument()

    const stored = getStoredPendingPatient('identity-1', 'provider-a')
    expect(stored).not.toBeNull()
    expect(stored?.source_system).toBe('dhealth-clinic')
    expect(stored?.source_identity_system).toBe('urn:dhealth:clinic:patient-reference-id')
    expect(stored?.source_identity_value).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    )
    expect(typeof stored?.created_at).toBe('number')

    // Verify localStorage contains ZERO PHI
    const rawStorage = localStorage.getItem('dhealth_clinic_pending_patient_v1:identity-1:provider-a')
    expect(rawStorage).not.toBeNull()
    expect(rawStorage).not.toContain('Patient Secret PHI')
    expect(rawStorage).not.toContain('+25377999888')
  })

  it('recovers from reload without allowing discard or a fresh identity', async () => {
    // Pre-populate storage with an unresolved UUID
    const staleUuid = '99999999-9999-4999-8999-999999999999'
    setStoredPendingPatient('identity-1', 'provider-a', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: staleUuid,
      created_at: Date.now(),
    })

    const bind = vi.fn().mockResolvedValue({
      reference: {
        id: patientWithName.patient_reference_id,
        source_organization_id: 'provider-a',
        source_system: 'dhealth-clinic',
        source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
        source_identity_value: patientWithName.local_reference,
      },
      created: true,
    })
    const client = makeClient({
      bindPatientReferenceWithOutcome: bind,
      listPatientReferences: vi.fn().mockResolvedValue([]),
      getPatientReference: vi.fn().mockResolvedValue(patientWithName),
    })
    const user = userEvent.setup()

    render(<ClinicApp client={client} initialTab="patients" />)
    await screen.findByText('Aucun patient trouvé')

    // Banner indicates unresolved attempt
    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()
    expect(screen.getByText(/La création du patient n’a pas pu être confirmée/)).toBeInTheDocument()
    expect(screen.queryByText("Aucun patient n'a été créé")).not.toBeInTheDocument()

    expect(screen.queryByRole('button', { name: 'Abandonner la tentative' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Reprendre' }))
    await user.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Nouveau Patient Après Abandon')
    await user.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(bind).toHaveBeenCalledTimes(1)
    expect(bind.mock.calls[0][1].source_identity_value).toBe(staleUuid)
  })

  it('recovers a confirmed reference by its stored authoritative id', async () => {
    const committedUuid = '88888888-8888-4888-8888-888888888888'
    setStoredPendingPatient('identity-1', 'provider-a', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: committedUuid,
      confirmed_patient_reference_id: '33333333-3333-4333-8333-333333333333',
      created_at: Date.now(),
    })

    const matchingPatient: ClinicPatientSummary = {
      patient_reference_id: '33333333-3333-4333-8333-333333333333',
      display_name: 'Patient Déjà Sauvegardé',
      contact_channel_type: 'PHONE',
      contact_value: '+25377222333',
      local_reference: committedUuid,
    }

    const client = makeClient({
      getPatientReference: vi.fn().mockResolvedValue(matchingPatient),
    })

    render(<ClinicApp client={client} initialTab="patients" />)

    // Automatically reconciled and opened
    expect(await screen.findByRole('heading', { name: 'Patient Déjà Sauvegardé' })).toBeInTheDocument()
    // Cleared from pending storage
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toBeNull()
  })

  it('retains non-PHI pending identities on logout for safe later reconciliation', async () => {
    setStoredPendingPatient('identity-1', 'provider-a', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: 'test-uuid',
      created_at: Date.now(),
    })
    expect(getStoredPendingPatient('identity-1', 'provider-a')).not.toBeNull()

    const onLogout = vi.fn()
    const client = makeClient()
    const user = userEvent.setup()

    render(<ClinicApp client={client} initialTab="patients" onLogout={onLogout} />)
    await screen.findByText('Aucun patient trouvé')

    await user.click(screen.getByRole('button', { name: 'Se déconnecter' }))
    expect(onLogout).toHaveBeenCalledTimes(1)
    expect(getStoredPendingPatient('identity-1', 'provider-a')?.source_identity_value).toBe('test-uuid')
  })

  it('maintains strict user and provider isolation of unconfirmed state across logout and login', async () => {
    const unconfirmedUuid = '11111111-2222-4333-8444-555555555555'
    setStoredPendingPatient('identity-1', 'provider-a', {
      source_system: 'dhealth-clinic',
      source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
      source_identity_value: unconfirmedUuid,
      created_at: Date.now(),
    })

    const bind = vi.fn().mockResolvedValue({
      reference: {
        id: patientWithName.patient_reference_id,
        source_organization_id: 'provider-a',
        source_system: 'dhealth-clinic',
        source_identity_system: 'urn:dhealth:clinic:patient-reference-id',
        source_identity_value: unconfirmedUuid,
      },
      created: true,
    })
    // 1. User 1 logs in with Provider A: sees unconfirmed banner
    const clientUser1 = makeClient({
      getMe: vi.fn().mockResolvedValue({ ...me, identity_id: 'identity-1' }),
      bindPatientReferenceWithOutcome: bind,
      getPatientReference: vi.fn().mockResolvedValue(patientWithName),
    })
    const { unmount: unmount1 } = render(
      <ClinicApp client={clientUser1} initialTab="patients" />,
    )
    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).toBeDisabled()
    unmount1()

    // 2. Different user (identity-2) logs in with Provider A: CANNOT consume or see identity-1 attempt
    const clientUser2 = makeClient({
      getMe: vi.fn().mockResolvedValue({ ...me, identity_id: 'identity-2' }),
      bindPatientReferenceWithOutcome: bind,
      getPatientReference: vi.fn().mockResolvedValue(patientWithName),
    })
    const { unmount: unmount2 } = render(
      <ClinicApp client={clientUser2} initialTab="patients" />,
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
      bindPatientReferenceWithOutcome: bind,
      getPatientReference: vi.fn().mockResolvedValue(patientWithName),
    })
    const { unmount: unmount3 } = render(
      <ClinicApp client={clientWithBothProviders} initialTab="patients" />,
    )
    const userEventObj = userEvent.setup()
    const providerSelect = await screen.findByLabelText("Sélectionner l'établissement")
    await userEventObj.selectOptions(providerSelect, 'provider-b')
    await screen.findByText('Aucun patient trouvé')
    expect(screen.queryByText(/Tentative non confirmée/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nouveau patient' })).not.toBeDisabled()
    unmount3()

    // 4. User 1 logs back in with Provider A: can resume and complete creation with exact same UUID
    render(
      <ClinicApp client={clientUser1} initialTab="patients" />,
    )
    expect(await screen.findByText(/Tentative non confirmée/)).toBeInTheDocument()
    await userEventObj.click(screen.getByRole('button', { name: 'Reprendre' }))
    await userEventObj.type(screen.getByLabelText('Nom affiché (obligatoire)'), 'Patient Repris')
    await userEventObj.click(screen.getByRole('button', { name: 'Créer et ouvrir' }))

    expect(bind).toHaveBeenCalledTimes(1)
    expect(bind.mock.calls[0][1].source_identity_value).toBe(unconfirmedUuid)
    expect(await screen.findByRole('heading', { name: 'Patient Synthétique' })).toBeInTheDocument()
    expect(getStoredPendingPatient('identity-1', 'provider-a')).toBeNull()
  })
})
