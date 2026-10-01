/**
 * DHEALTH-LAB-REPORT-001/ADR-072 §6: the print stylesheet's *structure*.
 *
 * jsdom applies no print media rules, evaluates no cascade and paginates nothing, so no test in this
 * repository can prove what a printer emits — `ui/e2e/native/16-lab-report.spec.ts` does that in a
 * real Chromium. What these assertions protect is the specific, fragile mechanism the report depends
 * on, which a reasonable-looking refactor would silently break:
 *
 *  - the application-hiding rule must be **global** and must key on `body.dh-report-open`, because a
 *    CSS-module-scoped rule cannot reach `#root`;
 *  - the report's own overlay must become `position: static` in print, because a fixed or absolutely
 *    positioned root does not fragment across pages and this report may legitimately run to several;
 *  - DH-PATIENT-001's `[data-print-root]` mechanism must be left exactly as it was — the two
 *    mechanisms are independent on purpose;
 *  - `thead` must be a `table-header-group` and rows must not split, or a two-page report loses its
 *    column headings and can break between a label and its value.
 *
 * Both files are read from disk rather than imported: vitest runs with its default `css: false`,
 * which stubs every CSS module in the graph, so `import styles from './x.module.css'` would yield an
 * empty object and every assertion below would pass vacuously. That is not hypothetical — see
 * `features/clinic/components/patientPrintStyles.test.ts`, which hit exactly that.
 */

/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import documentRaw from './ReleasedReportDocument.tsx?raw'

/**
 * Comments are stripped before anything is asserted.
 *
 * Both stylesheets explain their own rules in prose, and that prose quotes the very declarations
 * these tests assert are absent -- `.serviceBlock`'s comment says why it is deliberately not
 * `break-inside: avoid`, and the module's header says no `@page` rule is set. Asserting over raw
 * text made both of those negative assertions fail against the explanation of themselves. The
 * assertions are about the stylesheet's behaviour, so they read declarations, not documentation.
 */
function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

const indexCss = stripComments(readFileSync(join(process.cwd(), 'src', 'index.css'), 'utf-8'))
const moduleCss = stripComments(
  readFileSync(
    join(process.cwd(), 'src', 'features', 'report', 'ReleasedReportDocument.module.css'),
    'utf-8',
  ),
)

function printBlockOf(css: string): string {
  const start = css.indexOf('@media print')
  expect(start).toBeGreaterThan(-1)
  return css.slice(start)
}

describe('released report print styles — the files were actually read', () => {
  it('read both stylesheets, so the assertions below are not vacuous', () => {
    // Lengths are checked after comment stripping, so a stripper that ate the whole file would
    // fail here rather than making every `not.toContain` below pass vacuously.
    expect(indexCss.length).toBeGreaterThan(500)
    expect(indexCss).toContain(':root')
    expect(moduleCss.length).toBeGreaterThan(500)
    expect(moduleCss).toContain('.overlay')
    expect(moduleCss).not.toContain('/*')
  })
})

describe('released report print styles — the application is removed, not partially hidden', () => {
  it('hides the whole application subtree while the report is open', () => {
    const printBlock = printBlockOf(indexCss)
    expect(printBlock).toMatch(/body\.dh-report-open\s+#root\s*\{\s*display:\s*none/)
  })

  it('keys that rule on the body class the component sets', () => {
    // The stylesheet and the component have to agree on the name, and only the component can say
    // what it actually adds to `document.body`.
    expect(documentRaw).toContain("classList.add('dh-report-open')")
    expect(documentRaw).toContain("classList.remove('dh-report-open')")
  })

  it('renders through a portal, which is what puts the report outside #root', () => {
    // `display: none` on `#root` only removes the application if the report is not inside it.
    expect(documentRaw).toContain('createPortal')
    expect(documentRaw).toContain('document.body')
  })

  it('leaves DH-PATIENT-001 handoff print mechanism untouched', () => {
    const printBlock = printBlockOf(indexCss)
    // Still hiding by visibility and still revealing `[data-print-root]`: this ticket added a second
    // independent mechanism rather than rewriting the handoff's.
    expect(printBlock).toMatch(/#root\s*\*\s*\{\s*visibility:\s*hidden/)
    expect(printBlock).toMatch(/\[data-print-root\][\s\S]{0,60}visibility:\s*visible/)
  })

  it('does not give the report a [data-print-root], which would reposition it absolutely', () => {
    // The global `[data-print-root]` rule sets `position: absolute`. Inheriting it is exactly what
    // would stop a multi-page report from fragmenting.
    expect(documentRaw).not.toContain('data-print-root')
  })
})

describe('released report print styles — the document paginates', () => {
  it('returns the overlay to static flow in print', () => {
    const printBlock = printBlockOf(moduleCss)
    expect(printBlock).toMatch(/\.overlay\s*\{[^}]*position:\s*static/)
    expect(printBlock).toMatch(/\.overlay\s*\{[^}]*overflow:\s*visible/)
  })

  it('removes the action bar from print', () => {
    const printBlock = printBlockOf(moduleCss)
    expect(printBlock).toMatch(/\.actions\s*\{\s*display:\s*none/)
  })

  it('repeats the table head on every page and never splits a row', () => {
    const printBlock = printBlockOf(moduleCss)
    expect(printBlock).toMatch(/\.table\s+thead\s*\{\s*display:\s*table-header-group/)
    expect(printBlock).toMatch(/\.table\s+tr\s*\{\s*break-inside:\s*avoid/)
  })

  it('lets a long service block continue onto the next page rather than be pushed whole', () => {
    const printBlock = printBlockOf(moduleCss)
    expect(printBlock).toMatch(/\.serviceBlock\s*\{[\s\S]{0,400}?break-inside:\s*auto/)
    // And the one thing that must NOT be there: avoiding a break inside the block would clip a
    // report longer than a page.
    expect(printBlock).not.toMatch(/\.serviceBlock\s*\{[^}]*break-inside:\s*avoid/)
  })

  it('keeps a heading with the content it labels', () => {
    const printBlock = printBlockOf(moduleCss)
    expect(printBlock).toMatch(/break-after:\s*avoid/)
  })
})

describe('released report print styles — the phone layout stays on screen', () => {
  it('scopes the stacked narrow layout to screen only', () => {
    // Print resolves a width media query against the page box (A4 is ~794px at 96dpi). An unscoped
    // `max-width: 760px` would be one printer margin away from emitting the phone layout on paper.
    expect(moduleCss).toContain('@media screen and (max-width: 760px)')
    expect(moduleCss).not.toMatch(/@media\s*\(max-width:\s*760px\)/)
  })

  it('sets no @page rule, because the application does not control paper size or margins', () => {
    expect(moduleCss).not.toContain('@page')
    expect(indexCss).not.toContain('@page')
  })
})
