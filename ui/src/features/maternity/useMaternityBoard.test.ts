import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useMaternityBoard } from './useMaternityBoard'
import * as client from '../../api/client'
import { ApiError } from '../../api/types'
import { DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, MATERNITE_LOCATION_UUID } from '../../api/admissionConstants'
import type { MaternityBoardVisit } from '../../api/types'

vi.mock('../../api/client', () => ({
  getMaternityBoardVisits: vi.fn(),
}))

function mocked() {
  return { get: vi.mocked(client.getMaternityBoardVisits) }
}

function maternityVisit(overrides: Partial<MaternityBoardVisit> = {}): MaternityBoardVisit {
  return {
    uuid: 'visit-1',
    startDatetime: '2026-08-20T08:00:00.000+0000',
    stopDatetime: null,
    visitType: { uuid: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID, display: 'Daher Maternity Admission' },
    location: { uuid: MATERNITE_LOCATION_UUID, display: 'Maternité' },
    patient: { uuid: 'p1', display: 'Patiente Un' },
    ...overrides,
  }
}

describe('useMaternityBoard', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  it('starts loading, then resolves with derived entries', async () => {
    const m = mocked()
    m.get.mockResolvedValue({ visits: [maternityVisit()], truncated: false })
    const { result } = renderHook(() => useMaternityBoard())

    expect(result.current.state.kind).toBe('loading')
    await waitFor(() => expect(result.current.state.kind).toBe('resolved'))
    if (result.current.state.kind !== 'resolved') throw new Error('unreachable')
    expect(result.current.state.entries).toHaveLength(1)
    expect(result.current.state.truncated).toBe(false)
  })

  it('resolves to an empty entries array, not an error, when nobody is admitted', async () => {
    const m = mocked()
    m.get.mockResolvedValue({ visits: [], truncated: false })
    const { result } = renderHook(() => useMaternityBoard())
    await waitFor(() => expect(result.current.state.kind).toBe('resolved'))
    if (result.current.state.kind !== 'resolved') throw new Error('unreachable')
    expect(result.current.state.entries).toEqual([])
  })

  it('a NOT_AUTHORIZED failure (Reception) resolves to the neutral "unavailable" state, never an error banner', async () => {
    const m = mocked()
    m.get.mockRejectedValue(new ApiError('NOT_AUTHORIZED', 'Accès refusé.'))
    const { result } = renderHook(() => useMaternityBoard())
    await waitFor(() => expect(result.current.state.kind).toBe('unavailable'))
  })

  it('a network failure resolves to an error state carrying NETWORK_ERROR', async () => {
    const m = mocked()
    m.get.mockRejectedValue(new ApiError('NETWORK_ERROR', 'Connexion au serveur impossible.'))
    const { result } = renderHook(() => useMaternityBoard())
    await waitFor(() => expect(result.current.state.kind).toBe('error'))
    if (result.current.state.kind !== 'error') throw new Error('unreachable')
    expect(result.current.state.code).toBe('NETWORK_ERROR')
  })

  it('refresh() re-fetches — reflects an admission made elsewhere without an app restart', async () => {
    const m = mocked()
    m.get.mockResolvedValueOnce({ visits: [], truncated: false })
    const { result } = renderHook(() => useMaternityBoard())
    await waitFor(() => expect(result.current.state.kind).toBe('resolved'))

    m.get.mockResolvedValueOnce({ visits: [maternityVisit()], truncated: false })
    await act(async () => {
      await result.current.refresh()
    })

    expect(m.get).toHaveBeenCalledTimes(2)
    if (result.current.state.kind !== 'resolved') throw new Error('unreachable')
    expect(result.current.state.entries).toHaveLength(1)
  })

  it('a slower first fetch never clobbers a faster later one', async () => {
    const m = mocked()
    let resolveFirst!: (value: { visits: MaternityBoardVisit[]; truncated: boolean }) => void
    m.get.mockImplementationOnce(
      () => new Promise((resolve) => { resolveFirst = resolve }),
    )
    const { result } = renderHook(() => useMaternityBoard())

    m.get.mockResolvedValueOnce({ visits: [maternityVisit({ uuid: 'v-second' })], truncated: false })
    await act(async () => {
      await result.current.refresh()
    })
    await waitFor(() => expect(result.current.state.kind).toBe('resolved'))

    // The stale first request now resolves — must not overwrite the second's result.
    resolveFirst({ visits: [], truncated: false })
    await Promise.resolve()

    if (result.current.state.kind !== 'resolved') throw new Error('unreachable')
    expect(result.current.state.entries).toHaveLength(1)
  })
})
