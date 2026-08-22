import type { OpenMrsPatient } from '../api/types'

// Renders only fields UI-001A actually observed in the patient response
// (docs/UI-001-CLINICAL-UX.md §13a.1). Missing fields show an honest
// "non renseigné" rather than a fabricated value.
export function formatBirthInfo(patient: OpenMrsPatient): string {
  const { birthdate, age } = patient.person ?? {}
  if (birthdate && typeof age === 'number') {
    return `${new Date(birthdate).toLocaleDateString('fr-FR')} (${age} ans)`
  }
  if (typeof age === 'number') {
    return `${age} ans`
  }
  if (birthdate) {
    return new Date(birthdate).toLocaleDateString('fr-FR')
  }
  return 'Date de naissance non renseignée'
}

export function formatIdentifier(patient: OpenMrsPatient): string {
  const first = patient.identifiers?.[0]?.identifier
  return first ?? 'Identifiant non renseigné'
}
