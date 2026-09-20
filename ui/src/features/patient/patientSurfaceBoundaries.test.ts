/**
 * DH-PATIENT-001 §67/§14/§20, AC12/AC19: static proofs about the patient surface's *source*, not
 * only its behaviour.
 *
 * Behavioural tests prove what the code does when the paths under test are exercised. These prove
 * something a behavioural test cannot: that certain capabilities are absent from the surface
 * altogether, so no future code path can reach them by accident. Reading the files is the point —
 * it is what makes "PatientApp cannot call the marketplace" a property of the tree rather than of
 * the one render we happened to test.
 */

import { describe, expect, it } from 'vitest'

/*
 * Sources are read through Vite's own `import.meta.glob(..., '?raw')` rather than `node:fs`:
 * it resolves against the real module graph (so a file moved out of this directory stops being
 * scanned, loudly, instead of silently), needs no Node type declarations in the browser-targeted
 * `tsconfig.app.json`, and keeps these assertions running under the same resolver the application
 * itself is built with.
 */
const PATIENT_SOURCES = import.meta.glob('./**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const QR_SOURCE = (
  import.meta.glob('../clinic/components/PatientAccessQr.tsx', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>
)['../clinic/components/PatientAccessQr.tsx']

const PACKAGE_JSON = (
  import.meta.glob('../../../package.json', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>
)['../../../package.json']

/**
 * Strip comments before scanning.
 *
 * This is not a convenience: the patient feature's own documentation *names* every forbidden API
 * precisely in order to explain why it is absent ("never written to `localStorage`", "nothing
 * imports `keycloak-js`"). Scanning raw text would therefore fail on the very comments that
 * document the property being asserted — and, worse, the obvious "fix" would be to delete the
 * explanation. The claim these tests make is about executable code, so that is what they read.
 *
 * Deliberately conservative: block comments, and whole lines that begin with `//` or a JSDoc `*`.
 * A trailing `//` comment on a code line is left in place rather than risk truncating a string
 * literal containing `//` (a URL), which would silently weaken the very checks below.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => {
      const trimmed = line.trimStart()
      return !trimmed.startsWith('//') && !trimmed.startsWith('*')
    })
    .join('\n')
}

describe('DHealth Patient surface boundaries', () => {
  const files = Object.entries(PATIENT_SOURCES)
    // Test files legitimately *name* the forbidden routes in order to assert their absence.
    .filter(([path]) => !/\.test\.tsx?$/.test(path))
    .map(([path, source]) => ({ path, source: stripComments(source) }))

  it('finds the patient feature source to inspect (guards against a silently empty sweep)', () => {
    expect(files.length).toBeGreaterThanOrEqual(4)
  })

  it('§67/AC19: never references a marketplace endpoint', () => {
    for (const { path, source } of files) {
      expect(source, `${path} references the patient lab-options route`).not.toContain(
        '/patient/order/lab-options',
      )
      expect(source, `${path} references the patient selection route`).not.toContain(
        '/patient/order/selection',
      )
      expect(source, `${path} references lab-options`).not.toContain('lab-options')
    }
  })

  it('§20/AC2: never imports Keycloak or the OIDC auth layer', () => {
    for (const { path, source } of files) {
      expect(source, `${path} imports keycloak-js`).not.toContain('keycloak-js')
      expect(source, `${path} imports the exchange auth layer`).not.toContain('features/auth')
      expect(source, `${path} imports ExchangeAuthProvider/Gate`).not.toMatch(/ExchangeAuth\w+/)
    }
  })

  it('§23/AC6: never touches browser storage', () => {
    for (const { path, source } of files) {
      for (const api of ['localStorage', 'sessionStorage', 'indexedDB', 'document.cookie']) {
        expect(source, `${path} uses ${api} — the patient credential must stay in memory only`)
          .not.toContain(api)
      }
    }
  })

  it('§24: does not reuse the clinician OIDC ExchangeClient', () => {
    for (const { path, source } of files) {
      expect(source, `${path} imports ExchangeClient`).not.toMatch(/from '.*\/exchangeClient'/)
    }
  })
})

describe('QR generation is local-only (§14/§69, AC11/AC12)', () => {
  const qrSource = stripComments(QR_SOURCE)

  it('AC12: contains no external QR/image/analytics service', () => {
    for (const host of [
      'chart.googleapis.com',
      'api.qrserver.com',
      'quickchart.io',
      'qrcode.tec-it.com',
      'google.com/chart',
    ]) {
      expect(qrSource).not.toContain(host)
    }
    // No network call of any kind: the encoder is synchronous and in-process.
    expect(qrSource).not.toMatch(/\bfetch\s*\(|XMLHttpRequest|new Image\(|axios/)
    // The only absolute URL in this file is the SVG namespace — an XML identifier that is never
    // dereferenced, not a fetch. Any other URL appearing here would mean the access URL (which
    // carries the patient's bearer credential) could be leaving the page.
    expect(qrSource.match(/https?:\/\/[^'"\s]+/g) ?? []).toEqual(['http://www.w3.org/2000/svg'])
  })

  it('renders modules as elements rather than injecting markup', () => {
    expect(qrSource).not.toContain('dangerouslySetInnerHTML')
    expect(qrSource).toContain('isDark')
  })

  it('AC11: the pinned local encoder is a real dependency, not a CDN script', () => {
    const packageJson = JSON.parse(PACKAGE_JSON) as { dependencies: Record<string, string> }
    // Pinned exactly (no ^ or ~), per this repository's "pin everything" rule.
    expect(packageJson.dependencies['qrcode-generator']).toMatch(/^\d+\.\d+\.\d+$/)
  })
})
