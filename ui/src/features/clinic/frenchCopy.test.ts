import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

/**
 * DH-USABILITY-001 §27/§32/§41 — French that is actually French, on the screens staff use.
 *
 * Three messages shipped as "Impossible de verifier la recuperation sure d'une creation
 * precedente." Nothing caught it: every test asserted the string it found rather than the string a
 * French speaker would read, so the assertions agreed with the defect. CLAUDE.md puts this above
 * presentation -- French is first class here, not a localisation afterthought -- and a clinic whose
 * software misspells its own language in an error message has been told something about how
 * carefully the rest of it was made.
 *
 * The copy is read through the TypeScript parser rather than a regex over the file. A first
 * attempt matched quoted spans with a regular expression and was worthless: it desynchronised on
 * every short literal and every apostrophe in JSX text (`l'état`), then tested the garbage between
 * stray quotes, and it never saw JSX text at all -- which is where most of what a user reads
 * actually lives. It passed, and the copy it was supposed to be checking contained a live match.
 *
 * Word boundaries are Unicode-aware for the same reason: JavaScript's `\b` is ASCII-only, so
 * `\benregistre\b` matches inside "enregistrée" -- a correctly accented word would be reported as
 * the error. The list therefore also excludes words whose unaccented form is legitimate French
 * ("il enregistre", "il effectue"); this catches spellings that are always wrong, not everything
 * that is wrong.
 */

const FILES = [
  'pages/ClinicPatientsPage.tsx',
  'pages/ClinicOrdersPage.tsx',
  'pages/ClinicResultsPage.tsx',
  'components/PatientAccessHandoff.tsx',
  '../lab/pages/LabOrdersPage.tsx',
  '../lab/pages/LabReleasePage.tsx',
  '../lab/components/NativeResultEntryForm.tsx',
  '../lab/components/ReleaseVersionHistory.tsx',
]

/**
 * Words whose unaccented spelling is never correct in French. Anything with a legitimate
 * unaccented form (`enregistre`, `effectue`, `presente` as conjugated verbs) is deliberately
 * absent: a guard that argues with correct copy gets switched off.
 */
const UNACCENTED = [
  'verifier',
  'verifiez',
  'verification',
  'recuperation',
  'creation',
  'creee',
  'precedente',
  'precedent',
  'libere',
  'resultat',
  'resultats',
  'reessayer',
  'donnees',
  'numero',
  'echec',
  'reussi',
  'selectionne',
  'completee',
  'terminee',
  'requete',
  'ete',
]

const OFFENDING = new RegExp(`(?<![\\p{L}\\p{M}])(${UNACCENTED.join('|')})(?![\\p{L}\\p{M}])`, 'iu')

/** Every literal and JSX text run in a file: what a reader can actually end up seeing. */
function copyStrings(path: string): string[] {
  const source = ts.createSourceFile(
    path,
    readFileSync(path, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  )
  const found: string[] = []
  const visit = (node: ts.Node) => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node) ||
      ts.isJsxText(node)
    ) {
      const text = node.text.trim()
      if (text) found.push(text)
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  return found
}

function pathOf(relative: string): string {
  return fileURLToPath(new URL(relative, import.meta.url))
}

describe('French copy on the clinic and laboratory screens', () => {
  it('FR1: no string or JSX text uses an unaccented spelling of an always-accented word', () => {
    const offences: string[] = []
    for (const relative of FILES) {
      for (const text of copyStrings(pathOf(relative))) {
        const match = text.match(OFFENDING)
        if (match) offences.push(`${relative}: "${match[1]}" in ${text.slice(0, 90)}`)
      }
    }
    expect(offences).toEqual([])
  })

  it('FR1-NC: the check reacts to the defect and not to correctly accented copy', () => {
    // The guard is only worth having if this pair holds: the shipped spelling is reported, and
    // the accented word it is nearly identical to is not. `\b` would fail the second one.
    expect("Impossible de verifier la recuperation sure d'une creation precedente.").toMatch(
      OFFENDING,
    )
    expect('la décision enregistrée est affichée ci-dessous').not.toMatch(OFFENDING)
    expect("DHealth enregistre ici qu'un suivi est requis").not.toMatch(OFFENDING)
    expect('Résultats libérés').not.toMatch(OFFENDING)
  })

  it('FR2: the messages this ticket rewrote do not come back in their original form', () => {
    // Pinned verbatim, because a rewrite later "tidied" back toward the code's own vocabulary
    // would pass FR1 while undoing what §32 asked for. Comments are exempt: a note recording what
    // a message used to say is what this repository keeps, and nobody reads it in a clinic.
    const forbidden = [
      'Impossible de verifier la recuperation sure',
      'Impossible de preparer la recuperation sure',
      'Stockage local corrompu',
      'Nom affiché (obligatoire)',
    ]
    const offences: string[] = []
    for (const relative of FILES) {
      for (const text of copyStrings(pathOf(relative))) {
        for (const phrase of forbidden) {
          if (text.includes(phrase)) offences.push(`${relative}: ${phrase}`)
        }
      }
    }
    expect(offences).toEqual([])
  })
})
