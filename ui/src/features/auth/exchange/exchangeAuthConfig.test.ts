import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExchangeAuthConfigError, resolveExchangeAuthConfig } from './exchangeAuthConfig'

describe('resolveExchangeAuthConfig', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('derives id.<domain> from a trusted app.<domain> production origin', () => {
    const config = resolveExchangeAuthConfig({ protocol: 'https:', hostname: 'app.example.org' })
    expect(config.url).toBe('https://id.example.org')
    expect(config.realm).toBe('dhealth-exchange')
    expect(config.clientId).toBe('dhealth-native-ui')
  })

  it('preserves the scheme when deriving the identity-provider origin', () => {
    const config = resolveExchangeAuthConfig({ protocol: 'http:', hostname: 'app.localhost' })
    expect(config.url).toBe('http://id.localhost')
  })

  // §21: no clever guessing on a hostname that does not match the trusted shape.
  it('fails closed on a bare hostname with no app. prefix and no override', () => {
    expect(() => resolveExchangeAuthConfig({ protocol: 'http:', hostname: 'localhost' })).toThrow(
      ExchangeAuthConfigError,
    )
  })

  it('fails closed on an unrelated hostname shape', () => {
    expect(() =>
      resolveExchangeAuthConfig({ protocol: 'https:', hostname: 'evil.attacker.example' }),
    ).toThrow(ExchangeAuthConfigError)
  })

  it('never guesses id.<domain> for a hostname only superficially containing "app."', () => {
    // "myapp.example.org" must not be mistaken for the trusted "app.example.org" shape.
    expect(() =>
      resolveExchangeAuthConfig({ protocol: 'https:', hostname: 'myapp.example.org' }),
    ).toThrow(ExchangeAuthConfigError)
  })

  it('uses an explicit VITE_EXCHANGE_KEYCLOAK_URL override instead of deriving one', () => {
    vi.stubEnv('VITE_EXCHANGE_KEYCLOAK_URL', 'http://keycloak.internal:8080')
    const config = resolveExchangeAuthConfig({ protocol: 'http:', hostname: 'localhost' })
    expect(config.url).toBe('http://keycloak.internal:8080')
  })

  it('honors realm/client-id overrides independently of the URL override', () => {
    vi.stubEnv('VITE_EXCHANGE_KEYCLOAK_URL', 'http://keycloak.internal:8080')
    vi.stubEnv('VITE_EXCHANGE_KEYCLOAK_REALM', 'custom-realm')
    vi.stubEnv('VITE_EXCHANGE_KEYCLOAK_CLIENT_ID', 'custom-client')
    const config = resolveExchangeAuthConfig({ protocol: 'http:', hostname: 'localhost' })
    expect(config.realm).toBe('custom-realm')
    expect(config.clientId).toBe('custom-client')
  })
})
