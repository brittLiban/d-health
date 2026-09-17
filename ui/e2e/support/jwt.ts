/**
 * DH-CLINIC-004B: decodes ONLY the header and payload of a JWT for claim
 * assertions (iss/aud/azp/sub/exp). Deliberately does not verify the
 * signature (that is the adapter's job, proven indirectly by the adapter
 * accepting the token at all) and deliberately never returns or logs the
 * raw token string or its signature segment.
 */
export interface DecodedJwt {
  header: Record<string, unknown>
  payload: Record<string, unknown>
}

export function decodeJwtClaimsOnly(token: string): DecodedJwt {
  const parts = token.split('.')
  if (parts.length !== 3) throw new Error('not a JWT (expected 3 dot-separated segments)')
  return {
    header: JSON.parse(Buffer.from(parts[0], 'base64url').toString('utf-8')),
    payload: JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf-8')),
  }
}
