import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RegisterPatientForm } from './RegisterPatientForm'
import * as client from '../../api/client'
import { ApiError, type IdentifierSource } from '../../api/types'

vi.mock('../../api/client', () => ({
  discoverIdentifierSource: vi.fn(),
  generateIdentifier: vi.fn(),
  createPatient: vi.fn(),
  findPatientByIdentifier: vi.fn(),
}))

const SOURCE: IdentifierSource = { uuid: 'src', identifierTypeUuid: 'type', locationBehavior: 'NOT_USED' }

function mocked() {
  return {
    discover: vi.mocked(client.discoverIdentifierSource),
    generate: vi.mocked(client.generateIdentifier),
    create: vi.mocked(client.createPatient),
    find: vi.mocked(client.findPatientByIdentifier),
  }
}

async function fillForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Prénom'), 'Synthetique')
  await user.type(screen.getByLabelText('Nom'), 'Test')
  await user.selectOptions(screen.getByLabelText('Genre'), 'F')
  const birthdate = screen.getByLabelText('Date de naissance')
  await user.click(birthdate)
  await user.paste('1995-06-15')
}

describe('RegisterPatientForm', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  it('renders every field with a visible, associated label, French-first', () => {
    render(<RegisterPatientForm initialQuery="" onCancel={vi.fn()} onCreated={vi.fn()} />)
    expect(screen.getByLabelText('Prénom')).toBeInTheDocument()
    expect(screen.getByLabelText('Nom')).toBeInTheDocument()
    expect(screen.getByLabelText('Genre')).toBeInTheDocument()
    expect(screen.getByLabelText('Date de naissance')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enregistrer le patient' })).toBeInTheDocument()
  })

  it('prefills given/family name only when the search query unambiguously splits into two tokens', () => {
    render(<RegisterPatientForm initialQuery="Aicha Guelleh" onCancel={vi.fn()} onCreated={vi.fn()} />)
    expect(screen.getByLabelText('Prénom')).toHaveValue('Aicha')
    expect(screen.getByLabelText('Nom')).toHaveValue('Guelleh')
  })

  it('never guesses a name split for a single-token or identifier-shaped query', () => {
    render(<RegisterPatientForm initialQuery="10001MJ" onCancel={vi.fn()} onCreated={vi.fn()} />)
    expect(screen.getByLabelText('Prénom')).toHaveValue('')
    expect(screen.getByLabelText('Nom')).toHaveValue('')
  })

  it('shows a validation message and never calls the API when a field is missing', async () => {
    const { discover } = mocked()
    const user = userEvent.setup()
    render(<RegisterPatientForm initialQuery="" onCancel={vi.fn()} onCreated={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: 'Enregistrer le patient' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Veuillez renseigner tous les champs.')
    expect(discover).not.toHaveBeenCalled()
  })

  it('shows a loading state and then calls onCreated on success', async () => {
    const { discover, generate, create } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockResolvedValue({ uuid: 'new-uuid', display: '100001W - Synthetique Test' })
    const onCreated = vi.fn()
    const user = userEvent.setup()

    render(<RegisterPatientForm initialQuery="" onCancel={vi.fn()} onCreated={onCreated} />)
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: 'Enregistrer le patient' }))

    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith({ uuid: 'new-uuid', display: '100001W - Synthetique Test' }),
    )
  })

  it('shows a safe French error, never backend internals, on a definite rejection', async () => {
    const { discover, generate, create } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockRejectedValue(new ApiError('NOT_AUTHORIZED', 'Privileges required: Add Patients'))
    const user = userEvent.setup()

    render(<RegisterPatientForm initialQuery="" onCancel={vi.fn()} onCreated={vi.fn()} />)
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: 'Enregistrer le patient' }))

    const alert = await screen.findByText('Accès refusé')
    expect(alert).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('Privileges required')
    expect(document.body.textContent).not.toContain('org.openmrs')
  })

  it('shows the unresolved-write message and does not auto-retry on an ambiguous write with zero reconciliation matches', async () => {
    const { discover, generate, create, find } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockRejectedValue(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))
    find.mockResolvedValue([])
    const user = userEvent.setup()

    render(<RegisterPatientForm initialQuery="" onCancel={vi.fn()} onCreated={vi.fn()} />)
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: 'Enregistrer le patient' }))

    expect(await screen.findByText('État inconnu')).toBeInTheDocument()
    expect(create).toHaveBeenCalledTimes(1)
  })

  it('a manual retry after an unresolved write starts a fresh attempt, not a resubmission of stale state', async () => {
    const { discover, generate, create, find } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValueOnce('100001W').mockResolvedValueOnce('100002X')
    create.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))
    create.mockResolvedValueOnce({ uuid: 'new-uuid', display: '100002X - Synthetique Test' })
    find.mockResolvedValue([])
    const onCreated = vi.fn()
    const user = userEvent.setup()

    render(<RegisterPatientForm initialQuery="" onCancel={vi.fn()} onCreated={onCreated} />)
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: 'Enregistrer le patient' }))
    await screen.findByText('État inconnu')

    await user.click(screen.getByRole('button', { name: 'Réessayer' }))

    await waitFor(() => expect(generate).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith({ uuid: 'new-uuid', display: '100002X - Synthetique Test' }),
    )
  })

  it('calls onCancel without ever calling the API', async () => {
    const { discover } = mocked()
    const onCancel = vi.fn()
    const user = userEvent.setup()
    render(<RegisterPatientForm initialQuery="" onCancel={onCancel} onCreated={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: 'Annuler' }))
    expect(onCancel).toHaveBeenCalled()
    expect(discover).not.toHaveBeenCalled()
  })

  it('never persists registration data to localStorage, sessionStorage, or IndexedDB', async () => {
    const { discover, generate, create } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockResolvedValue({ uuid: 'new-uuid', display: '100001W - Synthetique Test' })
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
    const indexedDbOpenSpy = window.indexedDB ? vi.spyOn(window.indexedDB, 'open') : undefined
    const user = userEvent.setup()

    render(<RegisterPatientForm initialQuery="" onCancel={vi.fn()} onCreated={vi.fn()} />)
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: 'Enregistrer le patient' }))
    await waitFor(() => expect(create).toHaveBeenCalled())

    expect(setItemSpy).not.toHaveBeenCalled()
    if (indexedDbOpenSpy) {
      expect(indexedDbOpenSpy).not.toHaveBeenCalled()
    }
  })
})
