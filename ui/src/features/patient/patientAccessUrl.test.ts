/**
 * DH-PATIENT-001 §13/§22/§43/§69, CH6: the patient access URL contract.
 *
 * These are the assertions that would go red if the bearer credential were ever moved out of the
 * URL fragment -- the single most consequential security property of this ticket, because a
 * credential in a query string or path is transmitted to the server and lands in access logs.
 */

import { describe, expect, it, vi } from 'vitest'
import {
  buildPatientAccessUrl,
  readAccessTokenFromFragment,
  scrubAccessFragment,
} from './patientAccessUrl'

const TOKEN = 'kR3n9Xq_Tb7LmAz2vWc4dEfGhIjKlMnOpQrStUvWxYz'

describe('buildPatientAccessUrl', () => {
  it('CH6/§69: puts the credential in the fragment, never a query parameter or path segment', () => {
    const url = buildPatientAccessUrl('https://app.example', TOKEN)

    expect(url).toBe(`https://app.example/?app=patient#access=${TOKEN}`)
    expect(url).toContain('?app=patient#access=')

    // The three shapes §13 explicitly forbids.
    expect(url).not.toContain(`?access=`)
    expect(url).not.toContain(`?token=`)
    expect(url).not.toContain(`/${TOKEN}`)

    // Everything before the '#' is what a server would receive; the token must not be in it.
    const [serverVisible] = url.split('#')
    expect(serverVisible).not.toContain(TOKEN)
  })

  it('builds from the given origin rather than a hard-coded hostname', () => {
    expect(buildPatientAccessUrl('https://app.localhost', TOKEN)).toBe(
      `https://app.localhost/?app=patient#access=${TOKEN}`,
    )
    // A trailing slash on the origin must not produce a doubled separator.
    expect(buildPatientAccessUrl('https://app.localhost/', TOKEN)).toBe(
      `https://app.localhost/?app=patient#access=${TOKEN}`,
    )
  })
})

describe('readAccessTokenFromFragment', () => {
  it('PT1: parses the credential out of the fragment', () => {
    expect(readAccessTokenFromFragment(`#access=${TOKEN}`)).toBe(TOKEN)
    expect(readAccessTokenFromFragment(`access=${TOKEN}`)).toBe(TOKEN)
    expect(readAccessTokenFromFragment(`#/access=${TOKEN}`)).toBe(TOKEN)
    expect(readAccessTokenFromFragment(`#other=1&access=${TOKEN}`)).toBe(TOKEN)
  })

  it('returns null for an absent, empty, or wrong-key fragment', () => {
    expect(readAccessTokenFromFragment('')).toBeNull()
    expect(readAccessTokenFromFragment('#')).toBeNull()
    expect(readAccessTokenFromFragment('#access=')).toBeNull()
    expect(readAccessTokenFromFragment('#token=abc')).toBeNull()
  })

  it('rejects malformed shapes without ever explaining why to the caller', () => {
    // Not the URL-safe base64 alphabet a real grant uses.
    expect(readAccessTokenFromFragment('#access=has spaces')).toBeNull()
    expect(readAccessTokenFromFragment('#access=<script>')).toBeNull()
    // Longer than the adapter's own _MAX_PATIENT_BEARER_TOKEN_LENGTH (512) DoS bound.
    expect(readAccessTokenFromFragment(`#access=${'a'.repeat(513)}`)).toBeNull()
    expect(readAccessTokenFromFragment(`#access=${'a'.repeat(512)}`)).toBe('a'.repeat(512))
  })
})

describe('scrubAccessFragment', () => {
  it('PT2: removes the fragment while preserving ?app=patient, with no navigation', () => {
    const replaceState = vi.fn()
    const windowRef = {
      history: { replaceState },
      location: { pathname: '/', search: '?app=patient', hash: `#access=${TOKEN}` },
    } as unknown as Window

    scrubAccessFragment(windowRef)

    expect(replaceState).toHaveBeenCalledTimes(1)
    expect(replaceState).toHaveBeenCalledWith(null, '', '/?app=patient')
    // The replacement URL must not carry the credential anywhere.
    expect(String(replaceState.mock.calls[0][2])).not.toContain(TOKEN)
  })

  it('is idempotent, which is what makes it safe under React StrictMode double-invocation', () => {
    const replaceState = vi.fn()
    const windowRef = {
      history: { replaceState },
      location: { pathname: '/', search: '?app=patient', hash: '' },
    } as unknown as Window

    scrubAccessFragment(windowRef)
    scrubAccessFragment(windowRef)

    expect(replaceState.mock.calls[0]).toEqual(replaceState.mock.calls[1])
  })
})
