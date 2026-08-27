import { describe, expect, it } from 'vitest'

// §10/§13/§6 — these must fail if a future change reintroduces any of these
// behaviours, regardless of how the implementation is refactored. Mirrors
// admission/permanentGuards.test.ts's static-source-check pattern.

const featureModules = import.meta.glob('./*.{ts,tsx}', {
  eager: true,
  query: '?raw',
  import: 'default',
}) as Record<string, string>

function featureSourceText(): string {
  return Object.entries(featureModules)
    .filter(([path]) => !path.includes('.test.'))
    .map(([, content]) => content)
    .join('\n')
}

describe('permanent safety guards — static source checks', () => {
  it('never references discharge/end-visit actions (§10 — display only, no ending an admission)', () => {
    const text = featureSourceText()
    expect(text).not.toMatch(/Sortir\b/)
    expect(text).not.toMatch(/Discharge/i)
    expect(text).not.toMatch(/End Visit/i)
    expect(text).not.toMatch(/stopDatetime\s*:\s*['"]/) // never constructs a stop-visit payload
  })

  it('never references bed assignment (out of this ticket\'s scope, §13)', () => {
    const text = featureSourceText()
    expect(text).not.toMatch(/Assign Beds/i)
    expect(text).not.toMatch(/\/beds\b/)
  })

  it('never references clinical content this ticket must not invent (§13: pregnancy, labour, orders, results, diagnoses)', () => {
    const text = featureSourceText()
    expect(text).not.toMatch(/partograph/i)
    expect(text).not.toMatch(/diagnos/i)
    expect(text).not.toMatch(/prenatal|prénatal/i)
  })

  it('never references browser persistence (localStorage/sessionStorage/indexedDB) in the maternity feature', () => {
    expect(featureSourceText()).not.toMatch(/localStorage|sessionStorage|indexedDB/i)
  })

  it('never references a triage/severity ranking (§7 — ordering is UX-only, never a clinical signal)', () => {
    const text = featureSourceText()
    expect(text).not.toMatch(/triage/i)
    expect(text).not.toMatch(/severity/i)
  })

  // The rendered-DOM equivalent — never a UUID, visit id, or backend
  // terminology in what a clinician actually sees — is component-tested in
  // MaternityBoardPage.test.tsx ("never renders a UUID..."), which checks
  // real DOM text content. A source-text scan can't distinguish that from
  // this app's own OpenMrsPatient/OpenMrsSession type names, which every
  // patient-facing feature imports and cannot avoid.
})
