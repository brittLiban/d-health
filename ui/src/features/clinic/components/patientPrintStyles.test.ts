/**
 * DH-PATIENT-001 §17: the print stylesheet's *structure*.
 *
 * jsdom does not apply print media rules, evaluate the cascade, or paginate, so no test in this
 * repository can prove what a printer actually emits — the handoff states that plainly rather
 * than implying otherwise. What these assertions do protect is the specific, fragile mechanism
 * the sheet depends on, which a reasonable-looking refactor would silently break:
 *
 * - the rules must be **global**, not CSS-module-scoped, because the Clinic sidebar and header
 *   they hide are mounted far above the component that owns the sheet;
 * - the hiding must use `visibility`, not `display: none`, because hiding an ancestor with
 *   `display` would also remove the printable subtree nested inside it;
 * - `[data-print-root]` must be the selector both the stylesheet and the component agree on.
 */

/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import handoffRaw from './PatientAccessHandoff.tsx?raw'

/*
 * The stylesheet is read from disk rather than imported.
 *
 * Vitest runs with its default `css: false`, which stubs every CSS module in the graph -- so
 * `import indexCss from '.../index.css?raw'` yields an empty string, and an assertion against it
 * would pass or fail for reasons that have nothing to do with the stylesheet's contents. (That is
 * not hypothetical: this test was first written that way and silently compared against `''`.)
 * Enabling `css: true` globally to work around it would change how every other test in the suite
 * sees CSS modules, which is a much larger change than this one assertion warrants.
 *
 * The `node:` imports are why this file carries a `types="node"` reference: the app's tsconfig
 * targets the browser and deliberately exposes only `vite/client` globally.
 */
const indexCss = readFileSync(join(process.cwd(), 'src', 'index.css'), 'utf-8')

/**
 * The component's own documentation names `data-print-root` while explaining it, so the
 * "exactly one print root" count below has to look at code, not prose -- see
 * `features/patient/patientSurfaceBoundaries.test.ts` for the same reasoning at greater length.
 */
const handoffSource = handoffRaw
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .split('\n')
  .filter((line) => {
    const trimmed = line.trimStart()
    return !trimmed.startsWith('//') && !trimmed.startsWith('*')
  })
  .join('\n')

describe('patient handoff print styles (§17)', () => {
  it('actually read the stylesheet (guards against asserting against an empty string)', () => {
    // Without this, a resolution change that hands back '' would make every `not.toMatch`
    // assertion below pass vacuously -- exactly the failure mode this file already hit once.
    expect(indexCss.length).toBeGreaterThan(500)
    expect(indexCss).toContain(':root')
  })

  it('defines a print media block in the global stylesheet', () => {
    expect(indexCss).toMatch(/@media\s+print\s*\{/)
  })

  it('hides the surrounding Clinic chrome and re-reveals only the print root', () => {
    const printBlock = indexCss.slice(indexCss.indexOf('@media print'))

    expect(printBlock).toMatch(/#root\s*\*\s*\{\s*visibility:\s*hidden/)
    expect(printBlock).toMatch(/\[data-print-root\][\s\S]{0,60}visibility:\s*visible/)
  })

  it('uses visibility rather than display:none, which would remove the nested sheet too', () => {
    const printBlock = indexCss.slice(indexCss.indexOf('@media print'))
    expect(printBlock).not.toMatch(/#root\s*\*\s*\{[^}]*display:\s*none/)
  })

  it('the component marks exactly the sheet with the selector the stylesheet targets', () => {
    expect(handoffSource).toContain('data-print-root')
    // One print root only: two would print two overlapping absolutely-positioned sheets.
    expect(handoffSource.match(/data-print-root/g)?.length).toBe(1)
  })
})
