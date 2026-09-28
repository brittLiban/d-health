/**
 * DHEALTH-BILLING-001 — Facturation: what a facility charges, what it invoiced, what it was paid.
 *
 * ── One screen for both kinds of facility, and why it is one ──
 *
 * A clinic and a laboratory bill differently at the start and identically afterwards. A clinic
 * clerk begins from a person — somebody is standing at the desk — and a laboratory clerk begins
 * from an order, because a laboratory holds no patient directory of its own (**D-6** is open) and
 * knows its work by the order in front of it. From the moment a patient is chosen the two are the
 * same act: pick what was performed, issue the invoice, take the money, print the receipt.
 *
 * So this is one component with two entry points rather than a clinic copy and a laboratory copy
 * that would drift apart — the same reasoning `TeamAccessPage` records for staff administration.
 *
 * ── The two authorities on this screen are not the same authority ──
 *
 * `canOperateBilling` runs the till. `canConfigurePrices` decides what things cost. ADR-066 §2
 * keeps them in different hands deliberately: somebody who can quietly change a price *and* take
 * the payment is the oldest problem in facility billing. A cashier therefore sees invoices and no
 * price editor; an administrator sees the price list and no till. Neither is shown a control they
 * would be refused.
 *
 * Hiding a panel is usability, never security. The server checks `BILLING_OPERATE` and
 * `BILLING_CONFIGURE_PRICES` on every request and would refuse a hand-typed one just the same.
 *
 * ── Money is never computed here ──
 *
 * Every total, paid figure and balance on this screen is a string the server computed and this
 * component printed. Nothing is added, parsed or rounded in the browser: a balance is derived from
 * the payment rows inside a transaction (ADR-066 §6), and a second, hopeful arithmetic in
 * JavaScript would be a different answer to the same question with a patient holding the receipt.
 *
 * ── What is deliberately absent ──
 *
 * There is no way to edit or delete a payment, because there is no such endpoint: financial
 * history is append-only and correction is an undecided policy question (§26) this ticket refuses
 * to invent. There is no discount field, no credit, no refund and no partial void. When a clerk
 * mis-keys a payment the interface says the correction is not yet possible rather than offering a
 * control that quietly rewrites what happened.
 *
 * **An unpaid balance stops nothing clinical.** Nothing on this screen can delay a sample, a
 * result, a release or a review, and nothing clinical can read it — that independence is
 * structural in the backend (ADR-066 §4), not a promise made here.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ExchangeApiError } from '../../api/exchangeClient'
import type {
  BillableOrder,
  BillableSource,
  BillingPatient,
  ClinicService,
  Invoice,
  InvoiceLineRequest,
  PaymentMethod,
} from '../../api/exchangeTypes'
import { Button, EmptyState, LoadingSkeleton, Select, StatusBadge, TextInput } from '../../design-system'
import { useWorkspace } from '../workspace/WorkspaceContext'
import {
  PAYMENT_METHODS,
  describeBillingPatient,
  formatMoney,
  invoiceStatusLabel,
  invoiceStatusTone,
  paymentMethodLabel,
} from './billingVocabulary'
import styles from './Billing.module.css'

/**
 * Refusals in the words of the thing that was refused.
 *
 * 409 and 403 both carry the server's own `detail` when it sent one, because on this surface the
 * server's reason is more specific than any generic sentence: "ce service appartient à un autre
 * établissement" and "ce paiement dépasse le solde" are different facts a clerk acts on
 * differently, and collapsing them into "l'opération a échoué" would hide which.
 */
function errorMessage(error: ExchangeApiError | undefined): string {
  if (error?.status === 403) {
    return (
      error.detail ??
      "Cette opération n'est pas autorisée ici. Un établissement ne peut facturer que les actes qu'il a lui-même réalisés."
    )
  }
  if (error?.status === 404) {
    return "Cet élément n'existe pas dans cet établissement, ou n'y est plus accessible."
  }
  if (error?.status === 409) {
    return error.detail ?? "L'état a changé entre-temps. Actualisez et réessayez."
  }
  if (error?.status === 422) {
    return error.detail ?? "Ces informations ne permettent pas d'établir la facture."
  }
  return "L'opération n'a pas abouti. Vous pouvez réessayer."
}

/** A selected source and how many of it. Quantity lives beside the selection rather than inside
 *  `BillableSource`, because the server's list is a fact and the quantity is the clerk's choice. */
interface Chosen {
  source: BillableSource
  quantity: number
}

export function BillingPage() {
  const { activeOrganization, capabilities, client } = useWorkspace()

  const organizationId = activeOrganization?.organization_id ?? null
  const organizationName = activeOrganization?.organization_name ?? ''
  const isLab = activeOrganization?.organization_type === 'LAB'

  const [invoices, setInvoices] = useState<Invoice[] | null>(null)
  const [services, setServices] = useState<ClinicService[] | null>(null)
  const [orders, setOrders] = useState<BillableOrder[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)

  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState<BillingPatient[] | null>(null)
  const [patient, setPatient] = useState<BillingPatient | null>(null)
  const [sources, setSources] = useState<BillableSource[] | null>(null)
  const [chosen, setChosen] = useState<Chosen[]>([])

  const [invoice, setInvoice] = useState<Invoice | null>(null)
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState<PaymentMethod>('CASH')
  const [reference, setReference] = useState('')

  const [newServiceName, setNewServiceName] = useState('')
  const [newServiceAmount, setNewServiceAmount] = useState('')

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<string | null>(null)

  const canOperate = capabilities.canOperateBilling
  const canConfigure = capabilities.canConfigurePrices

  const load = useCallback(async () => {
    if (!organizationId) return
    setLoadFailed(false)
    try {
      // Each surface loads only what its own authority can read, so an administrator without the
      // till does not fire an invoice request the server would refuse, and a cashier does not ask
      // for a list they cannot use. The `await`s are sequential rather than raced because a
      // refusal on one must not discard the other's result.
      if (canOperate) {
        setInvoices(await client.listInvoices(organizationId, { limit: 25 }))
        if (isLab) setOrders(await client.listBillableOrders(organizationId))
      }
      if (canOperate || canConfigure) {
        setServices(await client.listClinicServices(organizationId))
      }
    } catch {
      setLoadFailed(true)
    }
  }, [client, organizationId, canOperate, canConfigure, isLab])

  useEffect(() => {
    void load()
  }, [load])

  const currency = useMemo(
    () => invoice?.currency ?? services?.find((service) => service.currency)?.currency ?? 'DJF',
    [invoice, services],
  )

  if (!activeOrganization || !organizationId) return null

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(errorMessage(err as ExchangeApiError))
    } finally {
      setBusy(false)
    }
  }

  const searchPatients = () =>
    run(async () => {
      setConfirmation(null)
      const trimmed = query.trim()
      if (!trimmed) {
        // Refused here as well as by the server, so the clerk is told why rather than watching a
        // request fail. This finds a person who is present; it does not browse the directory.
        setMatches([])
        return
      }
      setMatches(await client.searchBillingPatients(organizationId, trimmed))
    })

  const choosePatient = (selected: BillingPatient) =>
    run(async () => {
      setPatient(selected)
      setChosen([])
      setInvoice(null)
      setSources(await client.listBillableSources(organizationId, selected.id))
    })

  const chooseOrderPatient = (order: BillableOrder) =>
    run(async () => {
      // A laboratory's entry point. The order already carries the identity the lab holds, so no
      // patient lookup happens on this path at all.
      const asPatient: BillingPatient = {
        id: order.patient_id,
        display_name: order.patient_display_name,
        date_of_birth: null,
      }
      setPatient(asPatient)
      setChosen([])
      setInvoice(null)
      setSources(await client.listBillableSources(organizationId, order.patient_id))
    })

  const toggleSource = (source: BillableSource) => {
    setChosen((current) => {
      const existing = current.find((entry) => entry.source.source_id === source.source_id)
      if (existing) {
        return current.filter((entry) => entry.source.source_id !== source.source_id)
      }
      return [...current, { source, quantity: 1 }]
    })
  }

  const setQuantity = (sourceId: string, value: number) => {
    setChosen((current) =>
      current.map((entry) =>
        entry.source.source_id === sourceId
          ? { ...entry, quantity: Number.isFinite(value) && value > 0 ? Math.floor(value) : 1 }
          : entry,
      ),
    )
  }

  const createInvoice = () =>
    run(async () => {
      if (!patient || chosen.length === 0) return
      const lines: InvoiceLineRequest[] = chosen.map((entry) => ({
        source_type: entry.source.source_type,
        source_id: entry.source.source_id,
        quantity: entry.quantity,
      }))
      // A fresh key per attempt, so a retry after a *failure* is a new invoice and a double-click
      // on this same attempt is not. `crypto.randomUUID` is the same source the rest of this client
      // uses for idempotency keys.
      const outcome = await client.createInvoiceWithOutcome(
        organizationId,
        crypto.randomUUID(),
        { patient_id: patient.id, lines },
      )
      setInvoice(outcome.invoice)
      setChosen([])
      setConfirmation(
        outcome.created
          ? `Facture ${outcome.invoice.number} établie pour ${formatMoney(outcome.invoice.total, outcome.invoice.currency)}.`
          : `Facture ${outcome.invoice.number} déjà établie — aucune seconde facture n'a été créée.`,
      )
      setInvoices(await client.listInvoices(organizationId, { limit: 25 }))
      setSources(await client.listBillableSources(organizationId, patient.id))
    })

  const openInvoice = (selected: Invoice) =>
    run(async () => {
      setConfirmation(null)
      setInvoice(await client.getInvoice(organizationId, selected.id))
      setAmount('')
      setReference('')
    })

  const recordPayment = () =>
    run(async () => {
      if (!invoice) return
      const outcome = await client.recordPaymentWithOutcome(
        organizationId,
        invoice.id,
        crypto.randomUUID(),
        { amount: amount.trim(), method, reference: reference.trim() || null },
      )
      setInvoice(outcome.invoice)
      setAmount('')
      setReference('')
      const receipt = outcome.invoice.payments[outcome.invoice.payments.length - 1]
      setConfirmation(
        outcome.created
          ? `Paiement enregistré. Reçu ${receipt?.receipt_reference ?? ''} — solde restant ${formatMoney(outcome.invoice.balance, outcome.invoice.currency)}.`
          : `Ce paiement était déjà enregistré — il n'a pas été compté deux fois.`,
      )
      setInvoices(await client.listInvoices(organizationId, { limit: 25 }))
    })

  const createService = () =>
    run(async () => {
      await client.createClinicService(organizationId, {
        name: newServiceName.trim(),
        amount: newServiceAmount.trim(),
      })
      setNewServiceName('')
      setNewServiceAmount('')
      setConfirmation(`${newServiceName.trim()} ajouté au tarif de ${organizationName}.`)
      setServices(await client.listClinicServices(organizationId))
    })

  const changePrice = (service: ClinicService, next: string) =>
    run(async () => {
      await client.changeClinicServicePrice(organizationId, service.id, { amount: next.trim() })
      setConfirmation(
        `${service.name} : nouveau tarif ${formatMoney(next.trim(), service.currency ?? currency)}. Les factures déjà établies ne changent pas.`,
      )
      setServices(await client.listClinicServices(organizationId))
    })

  if (loadFailed) {
    return (
      <div className={styles.page}>
        <EmptyState title="La facturation n'a pas pu être chargée">
          Réessayez dans un instant. Aucune facture et aucun paiement n'ont été modifiés.
        </EmptyState>
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <header>
        <h1 className={styles.pageTitle}>Facturation</h1>
        <p className={styles.lede}>
          {organizationName} facture les actes qu'il a lui-même réalisés. Une facture établie ne
          change plus : modifier un tarif aujourd'hui ne modifie aucune facture d'hier.
          {canOperate
            ? " Un solde impayé n'empêche jamais un prélèvement, un résultat ou une consultation."
            : ''}
        </p>
      </header>

      {confirmation ? <p className={styles.status}>{confirmation}</p> : null}
      {error ? (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      ) : null}

      {canOperate ? (
        <div className={styles.columns}>
          <section className={styles.panel} aria-labelledby="billing-new">
            <h2 className={styles.panelTitle} id="billing-new">
              Nouvelle facture
            </h2>

            {isLab ? (
              <>
                <p className={styles.panelNote}>
                  Choisissez l'ordre réalisé par ce laboratoire. Seuls les ordres pris en charge ici
                  apparaissent.
                </p>
                {orders === null ? (
                  <LoadingSkeleton />
                ) : orders.length === 0 ? (
                  <EmptyState title="Aucun ordre à facturer">
                    Les ordres apparaîtront ici une fois acceptés par ce laboratoire.
                  </EmptyState>
                ) : (
                  <ul className={styles.list}>
                    {orders.map((order) => (
                      <li
                        key={order.lab_order_id}
                        className={
                          patient?.id === order.patient_id
                            ? `${styles.row} ${styles.rowSelected}`
                            : styles.row
                        }
                      >
                        <span className={styles.identity}>
                          <span className={styles.name}>
                            {order.patient_display_name ?? 'Patient sans nom enregistré'}
                          </span>
                          <span className={styles.meta}>
                            {order.items.map((item) => item.description).join(' · ')}
                          </span>
                        </span>
                        <Button
                          variant="secondary"
                          disabled={busy}
                          onClick={() => void chooseOrderPatient(order)}
                        >
                          Facturer
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <>
                <p className={styles.panelNote}>
                  Recherchez la personne à facturer, puis vérifiez sa date de naissance avec elle
                  avant d'établir la facture.
                </p>
                <form
                  className={styles.searchRow}
                  aria-label="Rechercher le patient"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void searchPatients()
                  }}
                >
                  <span className={styles.grow}>
                    <TextInput
                      label="Nom du patient"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                    />
                  </span>
                  <Button type="submit" disabled={busy}>
                    Rechercher
                  </Button>
                </form>
                {matches === null ? null : matches.length === 0 ? (
                  <p className={styles.panelNote}>Aucun patient trouvé pour cette recherche.</p>
                ) : (
                  <ul className={styles.list}>
                    {matches.map((match) => (
                      <li
                        key={match.id}
                        className={
                          patient?.id === match.id
                            ? `${styles.row} ${styles.rowSelected}`
                            : styles.row
                        }
                      >
                        <span className={styles.name}>
                          {describeBillingPatient(match.display_name, match.date_of_birth)}
                        </span>
                        <Button
                          variant="secondary"
                          disabled={busy}
                          onClick={() => void choosePatient(match)}
                        >
                          Choisir
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}

            {patient && sources ? (
              <>
                <h3 className={styles.panelTitle}>
                  À facturer — {describeBillingPatient(patient.display_name, patient.date_of_birth)}
                </h3>
                {sources.length === 0 ? (
                  <EmptyState title="Rien à facturer pour cette personne">
                    Cet établissement n'a réalisé aucun acte facturable pour ce patient.
                  </EmptyState>
                ) : (
                  <ul className={styles.list}>
                    {sources.map((source) => {
                      const selected = chosen.find(
                        (entry) => entry.source.source_id === source.source_id,
                      )
                      const priced = source.amount !== null
                      return (
                        <li key={source.source_id} className={styles.row}>
                          <span className={styles.identity}>
                            <span className={styles.name}>{source.description}</span>
                            {priced ? (
                              <span className={styles.meta}>
                                {formatMoney(source.amount as string, source.currency)}
                              </span>
                            ) : (
                              <span className={styles.unpriced}>
                                Tarif non défini — un administrateur doit le renseigner
                              </span>
                            )}
                            {source.already_invoiced ? (
                              <span className={styles.alreadyInvoiced}>Déjà facturé une fois</span>
                            ) : null}
                          </span>
                          <span className={styles.actions}>
                            {selected ? (
                              <span className={styles.quantity}>
                                <TextInput
                                  label="Quantité"
                                  type="number"
                                  min={1}
                                  value={String(selected.quantity)}
                                  onChange={(event) =>
                                    setQuantity(source.source_id, Number(event.target.value))
                                  }
                                />
                              </span>
                            ) : null}
                            <Button
                              variant={selected ? 'primary' : 'secondary'}
                              disabled={busy || !priced}
                              onClick={() => toggleSource(source)}
                            >
                              {selected ? 'Retirer' : 'Ajouter'}
                            </Button>
                          </span>
                        </li>
                      )
                    })}
                  </ul>
                )}
                <div className={styles.actions}>
                  <Button
                    disabled={busy || chosen.length === 0}
                    onClick={() => void createInvoice()}
                  >
                    Créer la facture
                  </Button>
                </div>
              </>
            ) : null}
          </section>

          <section className={styles.panel} aria-labelledby="billing-invoice">
            <h2 className={styles.panelTitle} id="billing-invoice">
              {invoice ? 'Facture' : 'Factures récentes'}
            </h2>

            {invoice ? (
              <>
                <div className={styles.invoiceHead}>
                  <span className={styles.invoiceNumber}>{invoice.number}</span>
                  <StatusBadge
                    label={invoiceStatusLabel(invoice.status)}
                    tone={invoiceStatusTone(invoice.status)}
                  />
                </div>
                <p className={styles.meta}>
                  {invoice.patient_display_name ?? 'Patient sans nom enregistré'}
                </p>

                <table className={styles.lineTable}>
                  <caption className={styles.panelNote}>
                    Les montants ci-dessous ont été figés à l'établissement de la facture.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Acte</th>
                      <th scope="col" className={styles.numeric}>
                        Qté
                      </th>
                      <th scope="col" className={styles.numeric}>
                        Prix unitaire
                      </th>
                      <th scope="col" className={styles.numeric}>
                        Total
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {invoice.lines.map((line) => (
                      <tr key={line.id}>
                        <td>{line.description}</td>
                        <td className={styles.numeric}>{line.quantity}</td>
                        <td className={styles.numeric}>
                          {formatMoney(line.unit_amount, line.currency)}
                        </td>
                        <td className={styles.numeric}>
                          {formatMoney(line.line_total, line.currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <div className={styles.totals}>
                  <span className={styles.totalRow}>
                    <span>Total</span>
                    <span>{formatMoney(invoice.total, invoice.currency)}</span>
                  </span>
                  <span className={styles.totalRow}>
                    <span>Déjà réglé</span>
                    <span>{formatMoney(invoice.paid, invoice.currency)}</span>
                  </span>
                  <span className={styles.balanceRow}>
                    <span>Solde</span>
                    <span>{formatMoney(invoice.balance, invoice.currency)}</span>
                  </span>
                </div>

                {invoice.status === 'PAID' ? (
                  <p className={styles.panelNote}>Cette facture est soldée.</p>
                ) : (
                  <form
                    className={styles.paymentForm}
                    aria-label="Enregistrer un paiement"
                    onSubmit={(event) => {
                      event.preventDefault()
                      void recordPayment()
                    }}
                  >
                    <TextInput
                      label="Montant reçu"
                      inputMode="decimal"
                      value={amount}
                      onChange={(event) => setAmount(event.target.value)}
                    />
                    <Select
                      label="Moyen"
                      value={method}
                      onChange={(event) => setMethod(event.target.value as PaymentMethod)}
                    >
                      {PAYMENT_METHODS.map((entry) => (
                        <option key={entry.value} value={entry.value}>
                          {entry.label}
                        </option>
                      ))}
                    </Select>
                    <TextInput
                      label="Référence (facultatif)"
                      value={reference}
                      onChange={(event) => setReference(event.target.value)}
                    />
                    <Button type="submit" disabled={busy || amount.trim() === ''}>
                      Enregistrer le paiement
                    </Button>
                  </form>
                )}

                {invoice.payments.length > 0 ? (
                  <>
                    <h3 className={styles.panelTitle}>Reçus</h3>
                    <p className={styles.panelNote}>
                      Un paiement enregistré ne peut être ni modifié ni supprimé.
                    </p>
                    <ul className={styles.receiptList}>
                      {invoice.payments.map((payment) => (
                        <li key={payment.id} className={styles.receiptRow}>
                          <span className={styles.receiptReference}>
                            {payment.receipt_reference}
                          </span>
                          <span className={styles.meta}>
                            {formatMoney(payment.amount, invoice.currency)} ·{' '}
                            {paymentMethodLabel(payment.method)}
                            {payment.reference ? ` · ${payment.reference}` : ''}
                          </span>
                        </li>
                      ))}
                    </ul>
                    <div className={styles.actions}>
                      <Button variant="secondary" onClick={() => window.print()}>
                        Imprimer le reçu
                      </Button>
                    </div>
                  </>
                ) : null}

                <div className={styles.actions}>
                  <Button variant="secondary" onClick={() => setInvoice(null)}>
                    Retour aux factures
                  </Button>
                </div>
              </>
            ) : invoices === null ? (
              <LoadingSkeleton />
            ) : invoices.length === 0 ? (
              <EmptyState title="Aucune facture pour le moment">
                Les factures établies ici apparaîtront dans cette liste.
              </EmptyState>
            ) : (
              <ul className={styles.list}>
                {invoices.map((entry) => (
                  <li key={entry.id} className={styles.row}>
                    <span className={styles.identity}>
                      <span className={styles.invoiceNumber}>{entry.number}</span>
                      <span className={styles.meta}>
                        {entry.patient_display_name ?? 'Patient sans nom enregistré'}
                      </span>
                    </span>
                    <span className={styles.identity}>
                      <span className={styles.name}>
                        {formatMoney(entry.balance, entry.currency)}
                      </span>
                      <StatusBadge
                        label={invoiceStatusLabel(entry.status)}
                        tone={invoiceStatusTone(entry.status)}
                      />
                    </span>
                    <Button
                      variant="secondary"
                      disabled={busy}
                      onClick={() => void openInvoice(entry)}
                    >
                      Ouvrir
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      ) : null}

      {canConfigure ? (
        <section className={styles.panel} aria-labelledby="billing-prices">
          <h2 className={styles.panelTitle} id="billing-prices">
            Services et tarifs
          </h2>
          <p className={styles.panelNote}>
            Ce que {organizationName} facture. Changer un tarif ne modifie aucune facture déjà
            établie : l'ancien montant reste celui qui a été facturé.
          </p>

          <form
            className={styles.priceForm}
            aria-label="Ajouter un service"
            onSubmit={(event) => {
              event.preventDefault()
              void createService()
            }}
          >
            <TextInput
              label="Nom du service"
              value={newServiceName}
              onChange={(event) => setNewServiceName(event.target.value)}
            />
            <TextInput
              label="Tarif"
              inputMode="decimal"
              value={newServiceAmount}
              onChange={(event) => setNewServiceAmount(event.target.value)}
            />
            <Button
              type="submit"
              disabled={busy || newServiceName.trim() === '' || newServiceAmount.trim() === ''}
            >
              Ajouter
            </Button>
          </form>

          {services === null ? (
            <LoadingSkeleton />
          ) : services.length === 0 ? (
            <EmptyState title="Aucun service au tarif">
              Ajoutez les actes que cet établissement réalise et facture.
            </EmptyState>
          ) : (
            <ul className={styles.list}>
              {services.map((service) => (
                <li key={service.id} className={styles.priceRow}>
                  <span className={styles.name}>{service.name}</span>
                  <span className={styles.numeric}>
                    {service.amount === null ? (
                      <span className={styles.unpriced}>Tarif non défini</span>
                    ) : (
                      formatMoney(service.amount, service.currency)
                    )}
                  </span>
                  <PriceEditor service={service} busy={busy} onSubmit={changePrice} />
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      {/* The printable receipt. Present in the document and invisible on screen, so printing is a
          browser action on the page the clerk is already looking at rather than a second fetch
          that could render a different invoice than the one in front of them. */}
      {invoice ? (
        <section className={styles.receiptSheet} aria-hidden="true">
          <h2>Reçu — {organizationName}</h2>
          <p>
            Facture {invoice.number} · {invoice.patient_display_name ?? 'Patient'}
          </p>
          <table className={styles.lineTable}>
            <tbody>
              {invoice.lines.map((line) => (
                <tr key={line.id}>
                  <td>{line.description}</td>
                  <td className={styles.numeric}>{line.quantity}</td>
                  <td className={styles.numeric}>
                    {formatMoney(line.line_total, line.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>Total : {formatMoney(invoice.total, invoice.currency)}</p>
          <p>Réglé : {formatMoney(invoice.paid, invoice.currency)}</p>
          <p>Solde : {formatMoney(invoice.balance, invoice.currency)}</p>
          {invoice.payments.map((payment) => (
            <p key={payment.id}>
              {payment.receipt_reference} — {formatMoney(payment.amount, invoice.currency)} ·{' '}
              {paymentMethodLabel(payment.method)}
            </p>
          ))}
        </section>
      ) : null}
    </div>
  )
}

/**
 * One service's price, edited in place.
 *
 * Its own component so each row keeps its own draft: a single shared "new price" field would let a
 * clerk type an amount against one service and submit it against another, which on this screen
 * means charging the wrong price rather than a cosmetic bug.
 */
function PriceEditor({
  service,
  busy,
  onSubmit,
}: {
  service: ClinicService
  busy: boolean
  onSubmit: (service: ClinicService, amount: string) => Promise<void>
}) {
  const [draft, setDraft] = useState('')
  return (
    <form
      className={styles.priceForm}
      aria-label={`Changer le tarif — ${service.name}`}
      onSubmit={(event) => {
        event.preventDefault()
        void onSubmit(service, draft).then(() => setDraft(''))
      }}
    >
      <TextInput
        label="Nouveau tarif"
        inputMode="decimal"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <Button type="submit" variant="secondary" disabled={busy || draft.trim() === ''}>
        Changer
      </Button>
    </form>
  )
}
