/**
 * DH-MVP-POLISH-001 — the central design-token layer, guarded statically.
 *
 * Before this ticket, 21 custom properties were referenced across the feature
 * stylesheets and defined nowhere: `--color-neutral-600`, `--font-size-sm` and
 * friends. A `var()` that resolves to nothing makes the whole declaration
 * invalid, so those rules silently did nothing — text rendered at whatever size
 * it inherited and "muted" labels came out full-strength ink. Nothing failed;
 * the UI was simply not the one the stylesheets described.
 *
 * That class of defect is invisible to a rendering test under jsdom, which does
 * not resolve custom properties across a real cascade. It is, however, trivially
 * visible to a source scan — so these tests read the files from disk.
 *
 * Read from disk rather than imported: vitest runs with `css: false`, so
 * `import './index.css'` yields an empty stub and any assertion against it would
 * pass vacuously. `process.cwd()` rather than `import.meta.url` because under
 * jsdom the latter is not a `file:` URL.
 */

import { readdirSync, readFileSync } from 'node:fs'
import { extname, join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC_DIR = join(process.cwd(), 'src')
const INDEX_CSS_PATH = join(SRC_DIR, 'index.css')

/** Dirent-based and lazy: a recursive statSync walk executed at module scope
 *  blocks the vitest worker handshake on a 9p-mounted checkout. */
function collectSourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      collectSourceFiles(full, out)
      continue
    }
    if (SCANNED_EXTENSIONS.has(extname(entry.name))) out.push(full)
  }
  return out
}

const SCANNED_EXTENSIONS = new Set([".css", ".ts", ".tsx"])

let scanned: { files: string[]; contents: Map<string, string> } | null = null

/** Read once, reuse across tests: every file in this tree is read from a
 *  slow mount, and five tests each re-reading it is the difference between
 *  a fast suite and a worker timeout. */
function scan() {
  if (scanned) return scanned
  const files = collectSourceFiles(SRC_DIR)
  const contents = new Map(files.map((f) => [f, readFileSync(f, "utf8")]))
  scanned = { files, contents }
  return scanned
}

function indexCssText(): string {
  return readFileSync(INDEX_CSS_PATH, "utf8")
}


/** Names declared at the top level of the `:root` block, i.e. two-space indent. */
function definedTokens(css: string): Set<string> {
  return new Set(Array.from(css.matchAll(/^ {2}(--[a-z0-9-]+)\s*:/gm), (m) => m[1]))
}

/** Prose is not a stylesheet. A doc comment that mentions a token by name
 *  (this file does so several times) must not be mistaken for a reference to
 *  it — the same trap that made an earlier static scan in this repository
 *  flag its own documentation. */
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/^\s*\/\/.*$/gm, ' ')
}

/** Every `var(--x)` reference, with or without a fallback value. */
function referencedTokens(text: string): string[] {
  return Array.from(text.matchAll(/var\(\s*(--[a-z0-9-]+)/g), (m) => m[1])
}

describe('design token layer', () => {
  it('reads index.css and the source tree from disk', () => {
    // Guards every assertion below: if the reads silently produced nothing, the
    // set comparisons would pass against empty inputs and prove nothing.
    const { files } = scan()
    expect(indexCssText().length).toBeGreaterThan(2000)
    expect(indexCssText()).toContain(':root {')
    expect(files.length).toBeGreaterThan(50)
  })

  it('defines every custom property referenced anywhere in the application', () => {
    const defined = definedTokens(indexCssText())
    const { files, contents } = scan()
    const missing = new Map<string, string[]>()
    // This file quotes token names in its own regexes and prose; it declares no styles.
    const SELF = 'designTokens.test.ts'

    for (const file of files) {
      if (relative(SRC_DIR, file) === SELF) continue
      for (const token of referencedTokens(stripComments(contents.get(file) ?? ''))) {
        if (defined.has(token)) continue
        const where = missing.get(token) ?? []
        where.push(relative(SRC_DIR, file))
        missing.set(token, where)
      }
    }

    // Reported as a readable map so a failure names the token and the files.
    expect(Object.fromEntries(missing)).toEqual({})
  })

  it('keeps the raw palette as the only place literal colours are written', () => {
    // Semantic tokens must point at the `--dh-*` layer rather than restating a
    // hex, so retargeting the product's colour stays a one-block change.
    const semanticColourLines = indexCssText()
      .split('\n')
      .filter((line) => /^ {2}--color-[a-z0-9-]+\s*:/.test(line))

    expect(semanticColourLines.length).toBeGreaterThan(20)
    for (const line of semanticColourLines) {
      expect(line).toMatch(/:\s*var\(--dh-[a-z0-9-]+\)\s*;/)
    }
  })

  it('carries the approved palette values', () => {
    const css = indexCssText()
    expect(css).toContain('--dh-bone: #f6f4ef')
    expect(css).toContain('--dh-ink: #14201f')
    expect(css).toContain('--dh-petrol: #0b4f4a')
    expect(css).toContain('--dh-brass: #8a5e10')
    expect(css).toContain('--dh-clay: #9a3412')
  })

  it('keeps the minimum touch target at 44px and a comfortable target at 56px', () => {
    expect(indexCssText()).toMatch(/--touch-target-min:\s*44px/)
    expect(indexCssText()).toMatch(/--touch-target-comfortable:\s*56px/)
  })
})

describe('typefaces are self-hosted', () => {
  it('fetches no font from a third-party CDN', () => {
    // A clinic on a degraded link must not block text on a host it cannot
    // reach, and a request to a font CDN would disclose which application is
    // open and from where.
    const { files, contents } = scan()
    const offenders = files.filter((file) =>
      relative(SRC_DIR, file) !== 'designTokens.test.ts' &&
      /fonts\.googleapis\.com|fonts\.gstatic\.com|use\.typekit\.net|cdnjs\.cloudflare\.com|cdn\.jsdelivr\.net/.test(
        contents.get(file) ?? '',
      ),
    )
    expect(offenders.map((file) => relative(SRC_DIR, file))).toEqual([])
  })

  it('declares its faces only through the bundled @fontsource packages', () => {
    const fonts = readFileSync(join(SRC_DIR, 'fonts.ts'), 'utf8')
    const imports = Array.from(fonts.matchAll(/^import '([^']+)'/gm), (m) => m[1])

    expect(imports.length).toBeGreaterThan(0)
    for (const specifier of imports) {
      expect(specifier).toMatch(/^@fontsource\//)
    }
  })

  it('is loaded by the application entry point', () => {
    const mainTsx = readFileSync(join(SRC_DIR, 'main.tsx'), 'utf8')
    expect(mainTsx).toMatch(/import '\.\/fonts\.ts'/)
  })

  it('names a real fallback after every self-hosted face', () => {
    // Text must stay legible before the face loads, and if it never loads.
    const css = indexCssText()
    for (const stack of ['--font-family-sans', '--font-family-serif', '--font-family-mono']) {
      const line = css.split('\n').find((l) => l.includes(stack + ':'))
      expect(line, stack).toBeDefined()
      expect(line!.split(',').length).toBeGreaterThan(2)
    }
  })
})
