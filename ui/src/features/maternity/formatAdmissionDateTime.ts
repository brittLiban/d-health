// Matches formatPatient.ts's fr-FR locale convention. Renders the exact
// `startDatetime` the server returned — never recomputed, never a relative
// "il y a X heures" that could drift from server truth on a slow render.
export function formatAdmissionDateTime(startDatetime: string): string {
  const date = new Date(startDatetime)
  if (Number.isNaN(date.getTime())) return 'Date d’admission non renseignée'
  return date.toLocaleString('fr-FR', { dateStyle: 'medium', timeStyle: 'short' })
}
