const FRENCH_MONTHS = [
  'janvier',
  'février',
  'mars',
  'avril',
  'mai',
  'juin',
  'juillet',
  'août',
  'septembre',
  'octobre',
  'novembre',
  'décembre',
]

const DATE_ONLY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

/**
 * DH-CLINIC-005 §16/§40: `date_of_birth` is a calendar date ("YYYY-MM-DD"), never a timestamp --
 * formatted by splitting the string directly, never via `new Date(...)`. Parsing a date-only
 * string with `Date` reinterprets it as UTC midnight, which can then render as the *previous* or
 * *next* calendar day once formatted back out in a viewer whose local timezone sits on the other
 * side of UTC midnight (e.g. `new Date('1990-01-01')` renders as 31 December 1989 in any
 * timezone west of UTC). This function is a pure string transform -- zero `Date` objects, zero
 * timezone surface, so the same input always renders the same output regardless of the viewer's
 * browser timezone.
 *
 * Returns `null` for a missing or malformed value -- never invents a date, never defaults to
 * January 1 (§16's explicit instruction).
 */
export function formatDateOfBirth(dateOfBirth: string | null | undefined): string | null {
  if (!dateOfBirth) return null
  const match = DATE_ONLY_PATTERN.exec(dateOfBirth)
  if (!match) return null
  const [, year, monthStr, dayStr] = match
  const monthName = FRENCH_MONTHS[Number(monthStr) - 1]
  if (!monthName) return null
  const day = Number(dayStr)
  if (!Number.isFinite(day) || day < 1 || day > 31) return null
  return `${day} ${monthName} ${year}`
}

const CONTACT_CHANNEL_LABELS: Record<string, string> = {
  PHONE: 'Téléphone',
  WHATSAPP: 'WhatsApp',
  SMS: 'SMS',
  EMAIL: 'Email',
}

/**
 * DH-CLINIC-005 §16/§42: a stored contact channel type is displayed with an explicit, honest
 * label -- never assumed to be PHONE just because that is the only channel the native creation
 * form currently offers. An unrecognized channel type still gets a neutral, truthful label rather
 * than being silently mislabeled.
 */
export function formatContactLabel(channelType: string | null | undefined): string {
  if (!channelType) return 'Contact'
  return CONTACT_CHANNEL_LABELS[channelType] ?? channelType
}
