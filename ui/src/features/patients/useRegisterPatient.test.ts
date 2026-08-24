import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useRegisterPatient } from './useRegisterPatient'
import * as client from '../../api/client'
import { ApiError, type IdentifierSource, type OpenMrsPatient, type PatientRegistration } from '../../api/types'

vi.mock('../../api/client', () => ({
  discoverIdentifierSource: vi.fn(),
  generateIdentifier: vi.fn(),
  createPatient: vi.fn(),
  findPatientByIdentifier: vi.fn(),
}))

const SOURCE: IdentifierSource = { uuid: 'src', identifierTypeUuid: 'type', locationBehavior: 'NOT_USED' }
const REGISTRATION: PatientRegistration = {
  givenName: 'Synthetique',
  familyName: 'Test',
  gender: 'F',
  birthdate: '1995-06-15',
}

function mocked() {
  return {
    discover: vi.mocked(client.discoverIdentifierSource),
    generate: vi.mocked(client.generateIdentifier),
    create: vi.mocked(client.createPatient),
    find: vi.mocked(client.findPatientByIdentifier),
  }
}

describe('useRegisterPatient', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  it('succeeds through the full idgen sequence: discover -> generate -> create', async () => {
    const { discover, generate, create } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockResolvedValue({ uuid: 'new-uuid', display: '100001W - Test' })

    const { result } = renderHook(() => useRegisterPatient())
    await act(async () => {
      await result.current.register(REGISTRATION)
    })

    expect(result.current.state).toEqual({
      status: 'success',
      patient: { uuid: 'new-uuid', display: '100001W - Test' },
    })
    expect(generate).toHaveBeenCalledWith('src')
    expect(create).toHaveBeenCalledWith(REGISTRATION, '100001W', SOURCE)
  })

  it('surfaces a definite rejection (e.g. validation) without ever calling reconciliation', async () => {
    const { discover, generate, create, find } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockRejectedValue(new ApiError('VALIDATION_ERROR', 'bad'))

    const { result } = renderHook(() => useRegisterPatient())
    await act(async () => {
      await result.current.register(REGISTRATION)
    })

    expect(result.current.state).toEqual({ status: 'error', code: 'VALIDATION_ERROR' })
    expect(find).not.toHaveBeenCalled()
  })

  it('reconciles an ambiguous write and reports success when exactly one match is found', async () => {
    const { discover, generate, create, find } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockRejectedValue(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))
    find.mockResolvedValue([{ uuid: 'committed-uuid', display: '100001W - Test' }])

    const { result } = renderHook(() => useRegisterPatient())
    await act(async () => {
      await result.current.register(REGISTRATION)
    })

    expect(find).toHaveBeenCalledWith('100001W')
    expect(result.current.state).toEqual({
      status: 'success',
      patient: { uuid: 'committed-uuid', display: '100001W - Test' },
    })
  })

  it('reconciles an ambiguous write to an explicit unresolved state when zero matches are found', async () => {
    const { discover, generate, create, find } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockRejectedValue(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))
    find.mockResolvedValue([])

    const { result } = renderHook(() => useRegisterPatient())
    await act(async () => {
      await result.current.register(REGISTRATION)
    })

    expect(result.current.state).toEqual({ status: 'error', code: 'AMBIGUOUS_WRITE' })
  })

  it('stops rather than picking a winner when reconciliation unexpectedly finds more than one match', async () => {
    const { discover, generate, create, find } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockRejectedValue(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))
    find.mockResolvedValue([
      { uuid: 'a', display: 'A' },
      { uuid: 'b', display: 'B' },
    ])

    const { result } = renderHook(() => useRegisterPatient())
    await act(async () => {
      await result.current.register(REGISTRATION)
    })

    expect(result.current.state).toEqual({ status: 'error', code: 'SERVER_ERROR' })
  })

  it('never issues a second POST /patient when reconciling an ambiguous write', async () => {
    const { discover, generate, create, find } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockRejectedValue(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))
    find.mockResolvedValue([])

    const { result } = renderHook(() => useRegisterPatient())
    await act(async () => {
      await result.current.register(REGISTRATION)
    })

    expect(create).toHaveBeenCalledTimes(1)
  })

  it('passes through a transient reconciling status before landing on the final outcome', async () => {
    const { discover, generate, create, find } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockRejectedValue(new ApiError('AMBIGUOUS_WRITE', 'uncertain'))
    let resolveFind: (value: OpenMrsPatient[]) => void = () => {}
    find.mockReturnValue(new Promise((resolve) => (resolveFind = resolve)))

    const { result } = renderHook(() => useRegisterPatient())
    const pending = result.current.register(REGISTRATION)

    await waitFor(() => expect(result.current.state.status).toBe('reconciling'))
    resolveFind([])
    await act(async () => {
      await pending
    })
    expect(result.current.state).toEqual({ status: 'error', code: 'AMBIGUOUS_WRITE' })
  })

  it('reset returns to idle', async () => {
    const { discover, generate, create } = mocked()
    discover.mockResolvedValue(SOURCE)
    generate.mockResolvedValue('100001W')
    create.mockResolvedValue({ uuid: 'x', display: 'x' })

    const { result } = renderHook(() => useRegisterPatient())
    await act(async () => {
      await result.current.register(REGISTRATION)
    })
    act(() => result.current.reset())
    expect(result.current.state).toEqual({ status: 'idle' })
  })
})
