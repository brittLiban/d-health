import { describe, expect, it, vi } from 'vitest'
import { createExchangeTokenProvider, TOKEN_REFRESH_WINDOW_SECONDS } from './exchangeTokenProvider'

function makeFakeKeycloak(overrides: Partial<{ authenticated: boolean; token: string | undefined }> = {}) {
  return {
    authenticated: overrides.authenticated ?? true,
    token: overrides.token ?? 'token-v1',
    updateToken: vi.fn(),
  }
}

describe('createExchangeTokenProvider', () => {
  it('returns the current token after a successful refresh check', async () => {
    const keycloak = makeFakeKeycloak()
    keycloak.updateToken.mockResolvedValue(false) // not refreshed, still valid
    const onFailure = vi.fn()
    const getAuthToken = createExchangeTokenProvider(keycloak as never, onFailure)

    const token = await getAuthToken()

    expect(token).toBe('token-v1')
    expect(keycloak.updateToken).toHaveBeenCalledWith(TOKEN_REFRESH_WINDOW_SECONDS)
    expect(onFailure).not.toHaveBeenCalled()
  })

  it('returns the refreshed token when updateToken actually refreshes it', async () => {
    const keycloak = makeFakeKeycloak()
    keycloak.updateToken.mockImplementation(async () => {
      keycloak.token = 'token-v2-refreshed'
      return true
    })
    const getAuthToken = createExchangeTokenProvider(keycloak as never, vi.fn())

    const token = await getAuthToken()

    expect(token).toBe('token-v2-refreshed')
  })

  it('returns null and reports failure when refresh fails — never the stale prior token', async () => {
    const keycloak = makeFakeKeycloak({ token: 'stale-token' })
    keycloak.updateToken.mockRejectedValue(new Error('refresh failed'))
    const onFailure = vi.fn()
    const getAuthToken = createExchangeTokenProvider(keycloak as never, onFailure)

    const token = await getAuthToken()

    expect(token).toBeNull()
    expect(onFailure).toHaveBeenCalledTimes(1)
  })

  it('returns null without attempting a refresh when there is no authenticated session', async () => {
    const keycloak = makeFakeKeycloak({ authenticated: false })
    const getAuthToken = createExchangeTokenProvider(keycloak as never, vi.fn())

    const token = await getAuthToken()

    expect(token).toBeNull()
    expect(keycloak.updateToken).not.toHaveBeenCalled()
  })

  it('single-flights concurrent callers into exactly one refresh attempt', async () => {
    const keycloak = makeFakeKeycloak()
    let resolveUpdate!: (value: boolean) => void
    keycloak.updateToken.mockImplementation(
      () =>
        new Promise<boolean>((resolve) => {
          resolveUpdate = resolve
        }),
    )
    const getAuthToken = createExchangeTokenProvider(keycloak as never, vi.fn())

    const first = getAuthToken()
    const second = getAuthToken()
    const third = getAuthToken()

    expect(keycloak.updateToken).toHaveBeenCalledTimes(1)
    resolveUpdate(false)

    const [a, b, c] = await Promise.all([first, second, third])
    expect(a).toBe('token-v1')
    expect(b).toBe('token-v1')
    expect(c).toBe('token-v1')
    expect(keycloak.updateToken).toHaveBeenCalledTimes(1)
  })

  it('starts a fresh refresh attempt after a prior one has completed', async () => {
    const keycloak = makeFakeKeycloak()
    keycloak.updateToken.mockResolvedValue(false)
    const getAuthToken = createExchangeTokenProvider(keycloak as never, vi.fn())

    await getAuthToken()
    await getAuthToken()

    expect(keycloak.updateToken).toHaveBeenCalledTimes(2)
  })
})
