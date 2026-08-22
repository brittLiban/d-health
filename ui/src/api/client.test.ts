import { afterEach, describe, expect, it, vi } from 'vitest'
import { searchPatients } from './client'
import { ApiError } from './types'

function mockFetchOnce(response: { ok: boolean; status: number; json: () => Promise<unknown> }) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response))
}

describe('searchPatients', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns results on a successful search', async () => {
    mockFetchOnce({
      ok: true,
      status: 200,
      json: async () => ({ results: [{ uuid: '1', display: 'Test Patient' }] }),
    })
    const results = await searchPatients('Test')
    expect(results).toEqual([{ uuid: '1', display: 'Test Patient' }])
  })

  it('returns an empty array for a nonexistent term rather than throwing', async () => {
    mockFetchOnce({ ok: true, status: 200, json: async () => ({ results: [] }) })
    const results = await searchPatients('nobody')
    expect(results).toEqual([])
  })

  it('maps a normalized NOT_AUTHORIZED body to an ApiError with that code', async () => {
    mockFetchOnce({
      ok: false,
      status: 401,
      json: async () => ({ error: { code: 'NOT_AUTHORIZED', message: 'nope' } }),
    })
    await expect(searchPatients('x')).rejects.toMatchObject({ code: 'NOT_AUTHORIZED' })
  })

  it('falls back to SERVER_ERROR for an unrecognized error code', async () => {
    mockFetchOnce({
      ok: false,
      status: 500,
      json: async () => ({ error: { code: 'SOMETHING_UNEXPECTED', message: 'boom' } }),
    })
    await expect(searchPatients('x')).rejects.toMatchObject({ code: 'SERVER_ERROR' })
  })

  it('throws NETWORK_ERROR when fetch itself rejects', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(searchPatients('x')).rejects.toBeInstanceOf(ApiError)
    await expect(searchPatients('x')).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
  })
})
