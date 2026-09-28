/**
 * DHEALTH-BILLING-001 — the words the till uses, and the rules for reading money on screen.
 *
 * Separated from the page for the same reason `visitVocabulary.ts` and `onboardingVocabulary.ts`
 * are: the wording is a product decision people review, and it should be readable without reading
 * a component. Nothing here decides anything — every amount, total and balance is computed by the
 * server, and this module only chooses how to say it.
 *
 * ── Money is never parsed ──
 *
 * `formatMoney` takes the server's string and adds spacing and a currency, and that is all it does.
 * It does not `parseFloat`, does not round, does not add and does not compare. The server holds
 * money as `Decimal`/`Numeric(12, 2)` precisely so no float touches it, and a formatter that parsed
 * would undo that at the last possible moment — with the resulting error visible to a patient
 * holding a receipt. If a figure needs computing, the server computes it.
 */

import type { InvoiceStatus, PaymentMethod } from '../../api/exchangeTypes'

/**
 * `3000.00 DJF` → `3 000,00 DJF`.
 *
 * French conventions: comma for the decimal mark, a narrow no-break space for thousands, currency
 * after the amount. The grouping space is U+202F, not a plain space, so a total never wraps in the
 * middle of a number — `3 000` split across two lines reads as two figures.
 *
 * Implemented by rewriting the string rather than by `Number(...).toLocaleString()`, which is the
 * parse this module exists to avoid. An unexpected shape is returned untouched: showing the raw
 * server value is honest, where showing `NaN` or a silently rounded figure is not.
 */
export function formatMoney(amount: string, currency: string | null): string {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(amount.trim())
  if (!match) return currency ? `${amount} ${currency}` : amount
  const [, sign, whole, fraction = '00'] = match
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  const rendered = `${sign}${grouped},${fraction.padEnd(2, '0')}`
  return currency ? `${rendered} ${currency}` : rendered
}

/**
 * `OPEN` / `PARTIALLY_PAID` / `PAID` in words a clerk and a patient both read the same way.
 *
 * "Soldée" rather than "Payée" for `PAID`: the invoice is settled, which is a fact about the
 * document. "Payée" invites the reading that the *patient* is paid, and in a facility where a third
 * party sometimes pays, that ambiguity is worth one extra syllable to avoid.
 */
const STATUS_LABELS: Record<InvoiceStatus, string> = {
  OPEN: 'À régler',
  PARTIALLY_PAID: 'Partiellement réglée',
  PAID: 'Soldée',
}

export function invoiceStatusLabel(status: InvoiceStatus | string): string {
  return STATUS_LABELS[status as InvoiceStatus] ?? status
}

/**
 * Tone follows the same rule the rest of this application uses: the label carries the meaning and
 * colour only reinforces it (§19). An outstanding balance is `warning` rather than `danger` —
 * money still owed is the ordinary state of a fresh invoice, not a fault, and a red screen for
 * every new bill would train people to ignore red.
 */
export function invoiceStatusTone(status: InvoiceStatus | string): 'neutral' | 'success' | 'warning' {
  if (status === 'PAID') return 'success'
  if (status === 'PARTIALLY_PAID') return 'warning'
  return 'neutral'
}

/**
 * The four ways a small facility in this pilot actually takes money.
 *
 * **None of these is an integration.** DHealth records that money was taken by one of these means;
 * it moves no money, contacts no bank, calls no mobile-money API and authorises no card. The
 * wording avoids any brand name for that reason — "Mobile money" is the category, not a provider.
 */
export const PAYMENT_METHODS: { value: PaymentMethod; label: string }[] = [
  { value: 'CASH', label: 'Espèces' },
  { value: 'MOBILE_MONEY', label: 'Mobile money' },
  { value: 'CARD', label: 'Carte' },
  { value: 'BANK_TRANSFER', label: 'Virement' },
]

export function paymentMethodLabel(method: string): string {
  return PAYMENT_METHODS.find((entry) => entry.value === method)?.label ?? method
}

/** `1990-04-17` → `17/04/1990`. Returned untouched if it is not a plain date, for the same reason
 *  `formatMoney` does: a wrong-looking date the reader can see beats a confident wrong one. */
export function formatDateOfBirth(value: string | null): string | null {
  if (!value) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return value
  const [, year, month, day] = match
  return `${day}/${month}/${year}`
}

/** A patient line in the till: name, and the date of birth that tells two same-named people apart.
 *  The date is the reason the lookup returns more than a name, so it is never dropped when present. */
export function describeBillingPatient(
  displayName: string | null,
  dateOfBirth: string | null,
): string {
  const name = displayName?.trim() || 'Patient sans nom enregistré'
  const born = formatDateOfBirth(dateOfBirth)
  return born ? `${name} — né(e) le ${born}` : name
}
