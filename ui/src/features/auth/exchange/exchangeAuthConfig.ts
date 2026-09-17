/**
 * DH-CLINIC-004A: resolves the public Keycloak configuration native DHealth
 * browser apps (Clinic today, Lab later) use to authenticate against the
 * `dhealth-exchange` realm's public `dhealth-native-ui` client.
 *
 * Everything here is PUBLIC configuration only (Keycloak URL, realm, client
 * ID) -- never a secret. See DECISIONS.md ADR-050.
 */

export interface ExchangeAuthConfig {
  url: string
  realm: string
  clientId: string
}

export class ExchangeAuthConfigError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ExchangeAuthConfigError'
  }
}

const DEFAULT_REALM = 'dhealth-exchange'
const DEFAULT_CLIENT_ID = 'dhealth-native-ui'

function readViteEnv(name: string): string | undefined {
  if (typeof import.meta === 'undefined') return undefined
  const value = (import.meta.env as Record<string, unknown> | undefined)?.[name]
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

export interface OriginLike {
  protocol: string
  hostname: string
}

/**
 * Fails closed rather than guessing: the only trusted derivation is the
 * production shape `app.<domain>` -> `https://id.<domain>`, and only when the
 * hostname genuinely matches that pattern. Anything else (bare `localhost`
 * during `npm run dev`, an unrecognized host, a future domain shape) must
 * supply an explicit `VITE_EXCHANGE_KEYCLOAK_URL` override or this throws --
 * it never silently sends authentication to a guessed host (ticket §21).
 */
export function resolveExchangeAuthConfig(
  origin: OriginLike = typeof window !== 'undefined'
    ? window.location
    : { protocol: 'https:', hostname: '' },
): ExchangeAuthConfig {
  const realm = readViteEnv('VITE_EXCHANGE_KEYCLOAK_REALM') || DEFAULT_REALM
  const clientId = readViteEnv('VITE_EXCHANGE_KEYCLOAK_CLIENT_ID') || DEFAULT_CLIENT_ID

  const overrideUrl = readViteEnv('VITE_EXCHANGE_KEYCLOAK_URL')
  if (overrideUrl) {
    return { url: overrideUrl, realm, clientId }
  }

  const match = /^app\.(.+)$/.exec(origin.hostname)
  if (!match) {
    throw new ExchangeAuthConfigError(
      `Cannot derive the DHealth Exchange identity-provider origin from hostname "${origin.hostname}". ` +
        'Set VITE_EXCHANGE_KEYCLOAK_URL explicitly for this environment rather than guessing one.',
    )
  }

  const baseDomain = match[1]
  return { url: `${origin.protocol}//id.${baseDomain}`, realm, clientId }
}
