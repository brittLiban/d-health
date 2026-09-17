import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ExchangeAuthProvider } from './ExchangeAuthProvider'
import { useExchangeAuth } from './ExchangeAuthContext'

const {
  initMock,
  loginMock,
  logoutMock,
  updateTokenMock,
  getLastInstance,
  FakeKeycloak,
} = vi.hoisted(() => {
  const initMock = vi.fn()
  const loginMock = vi.fn().mockResolvedValue(undefined)
  const logoutMock = vi.fn().mockResolvedValue(undefined)
  const updateTokenMock = vi.fn().mockResolvedValue(false)

  let lastInstance: InstanceType<typeof FakeKeycloak> | null = null

  class FakeKeycloak {
    authenticated = false
    token: string | undefined = undefined
    onAuthRefreshError?: () => void
    onAuthLogout?: () => void
    init = initMock
    login = loginMock
    logout = logoutMock
    updateToken = updateTokenMock
    config: unknown

    constructor(config: unknown) {
      this.config = config
      lastInstance = this
    }
  }

  return {
    initMock,
    loginMock,
    logoutMock,
    updateTokenMock,
    getLastInstance: () => lastInstance,
    setLastInstance: (value: InstanceType<typeof FakeKeycloak> | null) => {
      lastInstance = value
    },
    FakeKeycloak,
  }
})

vi.mock('keycloak-js', () => ({
  default: FakeKeycloak,
}))

function Probe() {
  const auth = useExchangeAuth()
  return (
    <div>
      <span data-testid="status">{auth.status}</span>
      <span data-testid="error">{auth.error ?? ''}</span>
      <span data-testid="has-client">{auth.client ? 'yes' : 'no'}</span>
      <button onClick={auth.retry}>retry</button>
      <button onClick={auth.logout}>logout</button>
    </div>
  )
}

describe('ExchangeAuthProvider', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_EXCHANGE_KEYCLOAK_URL', 'https://id.example.org')
    initMock.mockReset()
    loginMock.mockClear()
    logoutMock.mockClear()
    updateTokenMock.mockReset().mockResolvedValue(false)
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('starts in "initializing" before init() resolves', () => {
    initMock.mockReturnValue(new Promise(() => {})) // never resolves during this test
    render(
      <ExchangeAuthProvider>
        <Probe />
      </ExchangeAuthProvider>,
    )
    expect(screen.getByTestId('status').textContent).toBe('initializing')
    expect(screen.getByTestId('has-client').textContent).toBe('no')
  })

  it('transitions to "authenticating" and calls login() when check-sso finds no session', async () => {
    initMock.mockResolvedValue(false)
    render(
      <ExchangeAuthProvider>
        <Probe />
      </ExchangeAuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('authenticating'))
    expect(loginMock).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('has-client').textContent).toBe('no')
  })

  it('initializes with check-sso, not login-required, and PKCE S256', async () => {
    initMock.mockResolvedValue(true)
    render(
      <ExchangeAuthProvider>
        <Probe />
      </ExchangeAuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('authenticated'))
    expect(initMock).toHaveBeenCalledWith(
      expect.objectContaining({ onLoad: 'check-sso', pkceMethod: 'S256' }),
    )
  })

  it('reaches "authenticated" with a client once an existing SSO session is found', async () => {
    initMock.mockImplementation(async () => {
      getLastInstance()!.authenticated = true
      getLastInstance()!.token = 'token-abc'
      return true
    })
    render(
      <ExchangeAuthProvider>
        <Probe />
      </ExchangeAuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('authenticated'))
    expect(screen.getByTestId('has-client').textContent).toBe('yes')
  })

  it('does not construct a client while merely authenticating', async () => {
    initMock.mockResolvedValue(false)
    render(
      <ExchangeAuthProvider>
        <Probe />
      </ExchangeAuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('authenticating'))
    expect(screen.getByTestId('has-client').textContent).toBe('no')
  })

  it('fails closed to "error" with a truthful, non-technical message on a config failure', async () => {
    // No override and jsdom's default hostname does not match app.<domain>.
    vi.unstubAllEnvs()
    render(
      <ExchangeAuthProvider>
        <Probe />
      </ExchangeAuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('error'))
    expect(screen.getByTestId('has-client').textContent).toBe('no')
    expect(screen.getByTestId('error').textContent).not.toMatch(/PKCE|OIDC|token/i)
  })

  it('fails closed to "error" when init() itself rejects', async () => {
    initMock.mockRejectedValue(new Error('network unreachable'))
    render(
      <ExchangeAuthProvider>
        <Probe />
      </ExchangeAuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('error'))
    expect(screen.getByTestId('has-client').textContent).toBe('no')
  })

  it('retry() re-runs initialization from scratch', async () => {
    initMock.mockRejectedValueOnce(new Error('transient'))
    const user = (await import('@testing-library/user-event')).default.setup()
    render(
      <ExchangeAuthProvider>
        <Probe />
      </ExchangeAuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('error'))

    initMock.mockImplementation(async () => {
      getLastInstance()!.authenticated = true
      getLastInstance()!.token = 'token-after-retry'
      return true
    })
    await user.click(screen.getByText('retry'))

    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('authenticated'))
    expect(screen.getByTestId('has-client').textContent).toBe('yes')
  })

  it('a refresh failure transitions status to "error" and drops the client', async () => {
    initMock.mockImplementation(async () => {
      getLastInstance()!.authenticated = true
      getLastInstance()!.token = 'token-abc'
      return true
    })
    updateTokenMock.mockRejectedValue(new Error('refresh failed'))

    render(
      <ExchangeAuthProvider>
        <Probe />
      </ExchangeAuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('authenticated'))

    // Simulates keycloak-js's own notification path (e.g. its background
    // refresh timer) failing, independent of a manual getAuthToken() call.
    getLastInstance()!.onAuthRefreshError?.()

    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('error'))
    expect(screen.getByTestId('has-client').textContent).toBe('no')
  })

  it('logout() calls keycloak.logout with a redirect back to the app root', async () => {
    initMock.mockResolvedValue(true)
    const user = (await import('@testing-library/user-event')).default.setup()
    render(
      <ExchangeAuthProvider>
        <Probe />
      </ExchangeAuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('authenticated'))

    await user.click(screen.getByText('logout'))

    expect(logoutMock).toHaveBeenCalledTimes(1)
    const options = logoutMock.mock.calls[0]?.[0]
    expect(options?.redirectUri).toMatch(/\/$/)
  })
})
