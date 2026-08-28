import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useEndAdmission } from './useEndAdmission'
import * as client from '../../api/client'
import { ApiError } from '../../api/types'
import type { AdmissionReadState } from './useAdmission'

vi.mock('../../api/client', () => ({
  endAdmission: vi.fn(),
}))

const VISIT_UUID = 'visit-1'

function mocked() {
  return { endAdmission: vi.mocked(client.endAdmission) }
}

function stillComplete(): AdmissionReadState {
  return {
    kind: 'resolved',
    status: 'COMPLETE',
    visit: { uuid: VISIT_UUID, visitType: { uuid: 'vt' }, location: { uuid: 'loc' }, stopDatetime: null, encounters: [] },
  }
}

function noLongerActive(): AdmissionReadState {
  return { kind: 'resolved', status: 'NOT_ADMITTED' }
}

describe('useEndAdmission — the eight required states', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  it('starts ACTIVE', () => {
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))
    expect(result.current.state.kind).toBe('ACTIVE')
  })

  it('open() moves ACTIVE -> CONFIRMATION, without calling the API', () => {
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const m = mocked()
    const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))
    act(() => result.current.open())
    expect(result.current.state.kind).toBe('CONFIRMATION')
    expect(m.endAdmission).not.toHaveBeenCalled()
  })

  it('cancel() moves CONFIRMATION -> ACTIVE, without calling the API', () => {
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const m = mocked()
    const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))
    act(() => result.current.open())
    act(() => result.current.cancel())
    expect(result.current.state.kind).toBe('ACTIVE')
    expect(m.endAdmission).not.toHaveBeenCalled()
  })

  it('confirm() moves through SUBMITTING to ENDED on a fresh closure, calling endAdmission with the visit uuid exactly once', async () => {
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const m = mocked()
    m.endAdmission.mockResolvedValueOnce({ status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
    const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

    act(() => result.current.open())
    const confirmPromise = act(async () => {
      await result.current.confirm()
    })
    await confirmPromise

    expect(m.endAdmission).toHaveBeenCalledTimes(1)
    expect(m.endAdmission).toHaveBeenCalledWith(VISIT_UUID)
    expect(result.current.state).toEqual({ kind: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
    // ENDED must not auto-reconcile -- only dismiss() does.
    expect(reconcile).not.toHaveBeenCalled()
  })

  it('confirm() lands on ALREADY_ENDED, distinct from ENDED, on a zero-write idempotent close', async () => {
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const m = mocked()
    m.endAdmission.mockResolvedValueOnce({ status: 'ALREADY_ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
    const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

    act(() => result.current.open())
    await act(async () => {
      await result.current.confirm()
    })

    expect(result.current.state).toEqual({ kind: 'ALREADY_ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
  })

  it('dismiss() from ENDED reconciles exactly once and returns to ACTIVE', async () => {
    const reconcile = vi.fn().mockResolvedValue(noLongerActive())
    const m = mocked()
    m.endAdmission.mockResolvedValueOnce({ status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
    const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

    act(() => result.current.open())
    await act(async () => {
      await result.current.confirm()
    })
    await act(async () => {
      await result.current.dismiss()
    })

    expect(reconcile).toHaveBeenCalledTimes(1)
    expect(result.current.state.kind).toBe('ACTIVE')
  })

  it('a NOT_AUTHORIZED failure lands on UNAVAILABLE, not ERROR', async () => {
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const m = mocked()
    m.endAdmission.mockRejectedValueOnce(new ApiError('NOT_AUTHORIZED', 'Accès refusé.'))
    const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

    act(() => result.current.open())
    await act(async () => {
      await result.current.confirm()
    })

    expect(result.current.state.kind).toBe('UNAVAILABLE')
  })

  it('a definite VALIDATION_ERROR/NOT_FOUND/SERVER_ERROR failure lands on ERROR with the code preserved', async () => {
    for (const code of ['VALIDATION_ERROR', 'NOT_FOUND', 'SERVER_ERROR'] as const) {
      const reconcile = vi.fn().mockResolvedValue(stillComplete())
      const m = mocked()
      m.endAdmission.mockRejectedValueOnce(new ApiError(code, 'x'))
      const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

      act(() => result.current.open())
      await act(async () => {
        await result.current.confirm()
      })

      expect(result.current.state).toEqual({ kind: 'ERROR', code })
    }
  })

  it('a non-ApiError failure lands on ERROR as SERVER_ERROR, never an uncaught throw', async () => {
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const m = mocked()
    m.endAdmission.mockRejectedValueOnce(new Error('unexpected'))
    const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

    act(() => result.current.open())
    await act(async () => {
      await result.current.confirm()
    })

    expect(result.current.state).toEqual({ kind: 'ERROR', code: 'SERVER_ERROR' })
  })

  it('retry() from ERROR resubmits directly (no re-confirmation) and can reach ENDED', async () => {
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const m = mocked()
    m.endAdmission
      .mockRejectedValueOnce(new ApiError('SERVER_ERROR', 'x'))
      .mockResolvedValueOnce({ status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
    const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

    act(() => result.current.open())
    await act(async () => {
      await result.current.confirm()
    })
    expect(result.current.state.kind).toBe('ERROR')

    await act(async () => {
      await result.current.retry()
    })

    expect(m.endAdmission).toHaveBeenCalledTimes(2)
    expect(result.current.state.kind).toBe('ENDED')
  })

  describe('AMBIGUOUS_WRITE resolution (§13) — reconciliation decides the outcome, the mutation is never resent', () => {
    it('an ambiguous write followed by reconciliation showing the Visit no longer active resolves to ENDED', async () => {
      const reconcile = vi.fn().mockResolvedValue(noLongerActive())
      const m = mocked()
      m.endAdmission.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'x'))
      const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

      act(() => result.current.open())
      await act(async () => {
        await result.current.confirm()
      })

      expect(reconcile).toHaveBeenCalledTimes(1)
      expect(result.current.state).toEqual({ kind: 'ENDED', stopDatetime: null })
      expect(m.endAdmission).toHaveBeenCalledTimes(1) // never resent
    })

    it('an ambiguous write followed by reconciliation showing the Visit still COMPLETE resolves to a retryable ERROR, not silently AMBIGUOUS forever', async () => {
      const reconcile = vi.fn().mockResolvedValue(stillComplete())
      const m = mocked()
      m.endAdmission.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'x'))
      const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

      act(() => result.current.open())
      await act(async () => {
        await result.current.confirm()
      })

      expect(result.current.state).toEqual({ kind: 'ERROR', code: 'AMBIGUOUS_WRITE' })
      expect(m.endAdmission).toHaveBeenCalledTimes(1)
    })

    it('an ambiguous write whose own reconciliation also fails stays AMBIGUOUS', async () => {
      const reconcile = vi.fn().mockRejectedValue(new Error('network down'))
      const m = mocked()
      m.endAdmission.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'x'))
      const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

      act(() => result.current.open())
      await act(async () => {
        await result.current.confirm()
      })

      expect(result.current.state.kind).toBe('AMBIGUOUS')
    })

    it('verifyAgain() from AMBIGUOUS re-runs reconciliation and never resubmits the mutation', async () => {
      const m = mocked()
      m.endAdmission.mockRejectedValueOnce(new ApiError('AMBIGUOUS_WRITE', 'x'))
      // First reconciliation attempt (inside confirm()) fails outright -> AMBIGUOUS.
      // Second attempt (verifyAgain()) succeeds and shows the Visit no longer active -> ENDED.
      const reconcile = vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce(noLongerActive())
      const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

      act(() => result.current.open())
      await act(async () => {
        await result.current.confirm()
      })
      expect(result.current.state.kind).toBe('AMBIGUOUS')

      await act(async () => {
        await result.current.verifyAgain()
      })

      expect(m.endAdmission).toHaveBeenCalledTimes(1) // still never resent
      expect(result.current.state).toEqual({ kind: 'ENDED', stopDatetime: null })
    })
  })

  it('a second confirm() while SUBMITTING is a no-op — never fires a second request', async () => {
    const reconcile = vi.fn().mockResolvedValue(stillComplete())
    const m = mocked()
    let resolveFirst: (value: { status: 'ENDED'; stopDatetime: string }) => void = () => {}
    m.endAdmission.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFirst = resolve
      }),
    )
    const { result } = renderHook(() => useEndAdmission(VISIT_UUID, reconcile))

    act(() => result.current.open())
    await act(async () => {
      const first = result.current.confirm()
      const second = result.current.confirm()
      resolveFirst({ status: 'ENDED', stopDatetime: '2026-08-28T00:00:00.000Z' })
      await Promise.all([first, second])
    })

    expect(m.endAdmission).toHaveBeenCalledTimes(1)
  })
})
