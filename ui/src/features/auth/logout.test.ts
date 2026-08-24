import { afterEach, describe, expect, it, vi } from 'vitest'
import { logoutLocalSessions, navigateToKeycloakEndSession } from './logout'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('logoutLocalSessions', () => {
  it('DELETEs /openmrs/ws/rest/v1/session with the CSRF-boundary header', async () => {
    const fetchSpy = vi.fn().mockImplementation((url: string) => {
      if (url === '/openmrs/ws/rest/v1/session') return Promise.resolve({ ok: true })
      if (url === '/OpenELIS-Global/Home') return Promise.resolve({ ok: true, text: async () => '' })
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchSpy)

    const result = await logoutLocalSessions()

    expect(result.openmrs).toBe('ok')
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(init.method).toBe('DELETE')
    expect((init.headers as Record<string, string>)['X-Daher-App-Request']).toBe('1')
  })

  it('reports the OpenMRS leg as failed on a non-ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url === '/openmrs/ws/rest/v1/session') return Promise.resolve({ ok: false })
        return Promise.resolve({ ok: true, text: async () => '' })
      }),
    )
    const result = await logoutLocalSessions()
    expect(result.openmrs).toBe('failed')
  })

  it('reports the OpenMRS leg as failed when fetch itself rejects', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url === '/openmrs/ws/rest/v1/session') return Promise.reject(new TypeError('network'))
        return Promise.resolve({ ok: true, text: async () => '' })
      }),
    )
    const result = await logoutLocalSessions()
    expect(result.openmrs).toBe('failed')
  })

  it('scrapes the real _csrf token from an authenticated OpenELIS Home page and posts it to Logout', async () => {
    const fetchSpy = vi.fn().mockImplementation((url: string) => {
      if (url === '/openmrs/ws/rest/v1/session') return Promise.resolve({ ok: true })
      if (url === '/OpenELIS-Global/Home') {
        return Promise.resolve({
          ok: true,
          text: async () => '<input type="hidden" name="_csrf" value="real-token-123">',
        })
      }
      if (url === '/OpenELIS-Global/Logout') return Promise.resolve({ ok: true })
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchSpy)

    const result = await logoutLocalSessions()

    expect(result.openelis).toBe('ok')
    const logoutCall = fetchSpy.mock.calls.find(([url]) => url === '/OpenELIS-Global/Logout')
    expect(logoutCall).toBeDefined()
    const [, init] = logoutCall as [string, RequestInit]
    expect(init.method).toBe('POST')
    expect((init.body as URLSearchParams).get('_csrf')).toBe('real-token-123')
  })

  it('never invents a CSRF token — reports the OpenELIS leg as skipped when Home carries none', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url === '/openmrs/ws/rest/v1/session') return Promise.resolve({ ok: true })
        if (url === '/OpenELIS-Global/Home') return Promise.resolve({ ok: true, text: async () => '<html></html>' })
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )
    const result = await logoutLocalSessions()
    expect(result.openelis).toBe('skipped')
  })

  it('reports the OpenELIS leg as skipped when there is no live session to scrape a token from', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url === '/openmrs/ws/rest/v1/session') return Promise.resolve({ ok: true })
        if (url === '/OpenELIS-Global/Home') return Promise.resolve({ ok: false })
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )
    const result = await logoutLocalSessions()
    expect(result.openelis).toBe('skipped')
  })
})

describe('navigateToKeycloakEndSession', () => {
  it('navigates to the id. origin end-session endpoint with a post-logout redirect back to app.', () => {
    const assignSpy = vi.fn()
    vi.stubGlobal('location', {
      protocol: 'https:',
      hostname: 'app.example.org',
      host: 'app.example.org',
      assign: assignSpy,
    })

    navigateToKeycloakEndSession()

    expect(assignSpy).toHaveBeenCalledTimes(1)
    const [url] = assignSpy.mock.calls[0] as [string]
    const parsed = new URL(url)
    expect(parsed.hostname).toBe('id.example.org')
    expect(parsed.pathname).toBe('/realms/daher/protocol/openid-connect/logout')
    expect(parsed.searchParams.get('client_id')).toBe('openmrs')
    expect(parsed.searchParams.get('post_logout_redirect_uri')).toBe('https://app.example.org/')
  })
})
