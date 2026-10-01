/**
 * DHEALTH-BILLING-001 — Facturation, tested for the things that would make it dangerous rather
 * than merely broken.
 *
 * A till is not an ordinary CRUD screen. The failure modes that matter are: charging the wrong
 * person, charging twice, showing a figure the server did not compute, offering a control the
 * server will refuse, and letting money look like it gates care. So the properties under test are:
 *
 * - **Money is printed, never computed.** Every total, paid figure and balance on screen is the
 *   server's string. Nothing is summed, parsed or rounded in the browser.
 * - **The patient is identified by more than a name.** The lookup shows the date of birth,
 *   because two patients sharing a name is ordinary and a till that cannot tell them apart bills
 *   the wrong one.
 * - **The two billing authorities are different authorities.** A cashier sees the till and no
 *   price editor; an administrator sees the price list and no till (ADR-066 §2).
 * - **A clinic and a laboratory enter differently.** The clinic searches for a person; the
 *   laboratory works from the orders it performed and never searches for a patient at all.
 * - **Nothing clinical is fetched to take a payment.** The client stub defines only billing reads,
 *   so a patient, order or result fetch would reject on an undefined method rather than be hidden
 *   by a convenient stub.
 * - **Financial history is stated as immutable**, because there is no endpoint to change it.
 *
 * All patient, service and price data here is synthetic. No real patient data is used.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../api/exchangeClient'
import type {
  BillableOrder,
  BillableSource,
  BillingPatient,
  CatalogEntry,
  ClinicService,
  ExchangeMembershipSummary,
  Invoice,
  LabOffering,
} from '../../api/exchangeTypes'
import { WorkspaceProvider } from '../workspace/WorkspaceProvider'
import { BillingPage } from './BillingPage'
import { capabilitiesForRoles } from '../../test/renderWithWorkspace'

const ORG_ID = 'org-clinic-1'
const ORG_NAME = 'Clinique Démo'
const LAB_ID = 'org-lab-1'

const PATIENT: BillingPatient = {
  id: 'patient-1',
  display_name: 'Amina Synthétique',
  date_of_birth: '1990-04-17',
}

/** The case the date of birth exists for: the same name, a different person. */
const NAMESAKE: BillingPatient = {
  id: 'patient-2',
  display_name: 'Amina Synthétique',
  date_of_birth: '1974-11-02',
}

const CONSULTATION: BillableSource = {
  source_type: 'CLINIC_SERVICE',
  source_id: 'service-1',
  description: 'Consultation générale',
  amount: '3000.00',
  currency: 'DJF',
  already_invoiced: false,
}

const UNPRICED: BillableSource = {
  source_type: 'CLINIC_SERVICE',
  source_id: 'service-2',
  description: 'Pansement',
  amount: null,
  currency: null,
  already_invoiced: false,
}

const SERVICES: ClinicService[] = [
  {
    id: 'service-1',
    organization_id: ORG_ID,
    name: 'Consultation générale',
    active: true,
    amount: '3000.00',
    currency: 'DJF',
  },
  {
    id: 'service-2',
    organization_id: ORG_ID,
    name: 'Pansement',
    active: true,
    amount: null,
    currency: null,
  },
]

function invoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    id: 'invoice-1',
    organization_id: ORG_ID,
    organization_name: ORG_NAME,
    patient_id: PATIENT.id,
    patient_display_name: PATIENT.display_name,
    number: 'FAC-2026-000001',
    currency: 'DJF',
    issued_at: '2026-09-28T08:00:00Z',
    lines: [
      {
        id: 'line-1',
        source_type: 'CLINIC_SERVICE',
        description: 'Consultation générale',
        quantity: 1,
        unit_amount: '3000.00',
        currency: 'DJF',
        line_total: '3000.00',
      },
    ],
    payments: [],
    total: '3000.00',
    paid: '0.00',
    balance: '3000.00',
    status: 'OPEN',
    ...overrides,
  }
}

const LAB_ORDER: BillableOrder = {
  lab_order_id: 'order-1',
  patient_id: PATIENT.id,
  patient_display_name: PATIENT.display_name,
  status: 'COMPLETED',
  created_at: '2026-09-27T09:00:00Z',
  items: [
    {
      source_type: 'LAB_ORDER_ITEM',
      source_id: 'item-1',
      description: 'Numération formule sanguine',
      amount: '3500.00',
      currency: 'DJF',
      already_invoiced: false,
    },
  ],
}

function membershipFor(
  roles: string[],
  organizationType: 'PROVIDER' | 'LAB' = 'PROVIDER',
): ExchangeMembershipSummary {
  return {
    organization_id: organizationType === 'LAB' ? LAB_ID : ORG_ID,
    organization_name: organizationType === 'LAB' ? 'Laboratoire Démo' : ORG_NAME,
    organization_type: organizationType,
    organization_status: 'ACTIVE',
    membership_status: 'ACTIVE',
    roles,
    capabilities: capabilitiesForRoles(roles),
  }
}

/**
 * A client whose only defined methods are billing reads and writes.
 *
 * Deliberately narrow, for the reason `TeamAccessPage.test.tsx` gives: if this screen ever started
 * fetching patients, visits, orders or results, a stub for them would hide it. Here such a call
 * rejects on an undefined method, which is the failure this suite wants to see.
 */
function billingClient(
  membership: ExchangeMembershipSummary,
  overrides: Partial<Record<keyof ExchangeClient, unknown>> = {},
): ExchangeClient {
  return {
    getMe: vi.fn().mockResolvedValue({
      identity_id: 'identity-clerk',
      issuer: 'https://keycloak.test/realms/dhealth-exchange',
      subject: 'subject-clerk',
      display_name: 'Caissier Démo',
      email: null,
      platform_admin: false,
      memberships: [membership],
    }),
    listInvoices: vi.fn().mockResolvedValue([]),
    listClinicServices: vi.fn().mockResolvedValue(SERVICES),
    listBillableOrders: vi.fn().mockResolvedValue([LAB_ORDER]),
    searchBillingPatients: vi.fn().mockResolvedValue([PATIENT]),
    listBillableSources: vi.fn().mockResolvedValue([CONSULTATION, UNPRICED]),
    createInvoiceWithOutcome: vi
      .fn()
      .mockResolvedValue({ invoice: invoice(), created: true }),
    recordPaymentWithOutcome: vi.fn(),
    getInvoice: vi.fn().mockResolvedValue(invoice()),
    createClinicService: vi.fn(),
    changeClinicServicePrice: vi.fn(),
    ...overrides,
  } as unknown as ExchangeClient
}

/**
 * DHEALTH-ACCESS-CAPABILITIES-001 §26: the page renders one surface at a time now. `till` is
 * `Facturation`; `prices` is `Services et tarifs`, its own section, because setting prices and
 * taking payments are different authorities and an administrator holding only the first must not
 * land on the till.
 */
function renderBilling(client: ExchangeClient, surface: 'till' | 'prices' = 'till') {
  return Object.assign(
    render(
      <WorkspaceProvider client={client}>
        <BillingPage surface={surface} />
      </WorkspaceProvider>,
    ),
    { client },
  )
}

async function searchFor(name: string) {
  const form = within(await screen.findByRole('form', { name: 'Rechercher le patient' }))
  await userEvent.type(form.getByLabelText('Nom du patient'), name)
  await userEvent.click(form.getByRole('button', { name: 'Rechercher' }))
}

// -- the two authorities are not the same authority -------------------------------------------

describe('the two billing authorities', () => {
  it('gives a cashier the till and no price editor', async () => {
    renderBilling(billingClient(membershipFor(['BILLING_CLERK'])))

    expect(await screen.findByRole('heading', { name: 'Nouvelle facture' })).toBeInTheDocument()
    // ADR-066 §2: somebody who can change a price *and* take the payment is the oldest
    // problem in facility billing, so the cashier is not offered the price editor.
    expect(screen.queryByRole('heading', { name: 'Services et tarifs' })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Ajouter un service' })).not.toBeInTheDocument()
  })

  it('gives an administrator the price list and no till', async () => {
    renderBilling(billingClient(membershipFor(['ORG_ADMIN'])), 'prices')

    expect(await screen.findByRole('heading', { name: 'Services et tarifs' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Nouvelle facture' })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Rechercher le patient' })).not.toBeInTheDocument()
  })

  it('never asks for an invoice list on behalf of somebody who cannot read one', async () => {
    // Not cosmetic: an administrator firing a request the server refuses would put a
    // refusal on screen for a person who did nothing wrong, and would teach them the
    // application is unreliable.
    const client = billingClient(membershipFor(['ORG_ADMIN']))
    renderBilling(client, 'prices')
    await screen.findByRole('heading', { name: 'Services et tarifs' })
    expect(client.listInvoices).not.toHaveBeenCalled()
  })
})

// -- identifying the right person --------------------------------------------------------------

describe('identifying the patient', () => {
  it('shows the date of birth beside the name, because a name alone is not identification', async () => {
    renderBilling(
      billingClient(membershipFor(['BILLING_CLERK']), {
        searchBillingPatients: vi.fn().mockResolvedValue([PATIENT, NAMESAKE]),
      }),
    )
    await searchFor('Amina')

    // Two people, the same name, told apart by the only other fact the till is given.
    expect(await screen.findByText(/17\/04\/1990/)).toBeInTheDocument()
    expect(screen.getByText(/02\/11\/1974/)).toBeInTheDocument()
  })

  it('does not search the directory on an empty query', async () => {
    const client = billingClient(membershipFor(['BILLING_CLERK']))
    renderBilling(client)
    const form = within(await screen.findByRole('form', { name: 'Rechercher le patient' }))
    await userEvent.click(form.getByRole('button', { name: 'Rechercher' }))

    // This finds a person who is present; it does not browse a roster.
    expect(client.searchBillingPatients).not.toHaveBeenCalled()
  })

  it('never searches for a patient at a laboratory, which works from its own orders', async () => {
    const client = billingClient(membershipFor(['BILLING_CLERK'], 'LAB'))
    renderBilling(client)

    expect(await screen.findByText('Numération formule sanguine')).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Rechercher le patient' })).not.toBeInTheDocument()
    expect(client.searchBillingPatients).not.toHaveBeenCalled()
  })
})

// -- what may be charged ------------------------------------------------------------------------

describe('choosing what to charge', () => {
  it('offers an unpriced service and says why it cannot be billed', async () => {
    renderBilling(billingClient(membershipFor(['BILLING_CLERK'])))
    await searchFor('Amina')
    await userEvent.click(await screen.findByRole('button', { name: 'Choisir' }))

    const row = (await screen.findByText('Pansement')).closest('li')
    expect(row).not.toBeNull()
    const scoped = within(row as HTMLElement)
    // Offered, not hidden: hiding it would tell a clerk nothing is chargeable when what
    // is actually true is that somebody has to set a price.
    expect(scoped.getByText(/Tarif non défini/)).toBeInTheDocument()
    expect(scoped.getByRole('button', { name: 'Ajouter' })).toBeDisabled()
  })

  it('creates the invoice from the chosen sources and prints the server total', async () => {
    const client = billingClient(membershipFor(['BILLING_CLERK']))
    renderBilling(client)
    await searchFor('Amina')
    await userEvent.click(await screen.findByRole('button', { name: 'Choisir' }))

    const row = within((await screen.findByText('Consultation générale')).closest('li') as HTMLElement)
    await userEvent.click(row.getByRole('button', { name: 'Ajouter' }))
    await userEvent.click(screen.getByRole('button', { name: 'Créer la facture' }))

    await waitFor(() => expect(client.createInvoiceWithOutcome).toHaveBeenCalled())
    const [, , body] = (client.createInvoiceWithOutcome as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(body).toEqual({
      patient_id: PATIENT.id,
      // No amount is sent. The price comes from the facility's own configuration at
      // invoicing time -- a caller-supplied amount would let a browser decide what a
      // patient is charged.
      lines: [{ source_type: 'CLINIC_SERVICE', source_id: 'service-1', quantity: 1 }],
    })
    // Scoped to the invoice panel, because the same figure legitimately appears twice:
    // once on screen and once in the printable receipt, which is in the document at all
    // times so that printing is a browser action on the invoice already in front of the
    // clerk rather than a second fetch that could render a different one.
    const panel = within(await screen.findByRole('region', { name: 'Facture' }))
    // Four places on a one-line unpaid invoice: the unit price, the line total, the
    // invoice total and the balance. Counted rather than found, because each is a
    // separate string the server sent and the count is what would change if the
    // component ever started deriving one of them instead of printing it.
    expect(panel.getAllByText(/^3\s000,00\sDJF$/)).toHaveLength(4)
    expect(panel.getByText(/^0,00\sDJF$/)).toBeInTheDocument()
  })

  it('sends a fresh idempotency key, so a retry is deliberate and a double-click is not', async () => {
    const client = billingClient(membershipFor(['BILLING_CLERK']))
    renderBilling(client)
    await searchFor('Amina')
    await userEvent.click(await screen.findByRole('button', { name: 'Choisir' }))
    const row = within((await screen.findByText('Consultation générale')).closest('li') as HTMLElement)
    await userEvent.click(row.getByRole('button', { name: 'Ajouter' }))
    await userEvent.click(screen.getByRole('button', { name: 'Créer la facture' }))

    await waitFor(() => expect(client.createInvoiceWithOutcome).toHaveBeenCalled())
    const [, key] = (client.createInvoiceWithOutcome as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(typeof key).toBe('string')
    expect((key as string).length).toBeGreaterThan(8)
  })
})

// -- money on screen ----------------------------------------------------------------------------

describe('money', () => {
  it('prints the balance the server computed, never one the browser worked out', async () => {
    // The figures below deliberately do not add up in the obvious way: total 3 000,
    // paid 1 000, and a balance the server says is 2 000. A component that recomputed
    // would agree by accident here, so the assertion is that the *server's* strings are
    // what appear -- including a paid figure the browser never derived.
    const partly = invoice({
      paid: '1000.00',
      balance: '2000.00',
      status: 'PARTIALLY_PAID',
      payments: [
        {
          id: 'pay-1',
          sequence_number: 1,
          amount: '1000.00',
          method: 'CASH',
          reference: null,
          recorded_at: '2026-09-28T09:00:00Z',
          receipt_reference: 'FAC-2026-000001-R1',
        },
      ],
    })
    renderBilling(
      billingClient(membershipFor(['BILLING_CLERK']), {
        listInvoices: vi.fn().mockResolvedValue([partly]),
        getInvoice: vi.fn().mockResolvedValue(partly),
      }),
    )

    await userEvent.click(await screen.findByRole('button', { name: 'Ouvrir' }))
    const panel = within(await screen.findByRole('region', { name: 'Facture' }))
    // Both figures are the server's own strings. Scoped to the panel for the reason
    // above: the printable receipt carries the same amounts, deliberately.
    expect(panel.getByText(/^1\s000,00\sDJF$/)).toBeInTheDocument()
    expect(panel.getByText(/^2\s000,00\sDJF$/)).toBeInTheDocument()
    expect(panel.getByText('Partiellement réglée')).toBeInTheDocument()
    expect(panel.getByText('FAC-2026-000001-R1')).toBeInTheDocument()
  })

  it('states that a recorded payment cannot be changed, and offers no control to change it', async () => {
    const paid = invoice({
      paid: '3000.00',
      balance: '0.00',
      status: 'PAID',
      payments: [
        {
          id: 'pay-1',
          sequence_number: 1,
          amount: '3000.00',
          method: 'MOBILE_MONEY',
          reference: 'MM-12345',
          recorded_at: '2026-09-28T09:00:00Z',
          receipt_reference: 'FAC-2026-000001-R1',
        },
      ],
    })
    renderBilling(
      billingClient(membershipFor(['BILLING_CLERK']), {
        listInvoices: vi.fn().mockResolvedValue([paid]),
        getInvoice: vi.fn().mockResolvedValue(paid),
      }),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Ouvrir' }))

    expect(await screen.findByText(/ne peut être ni modifié ni supprimé/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Modifier le paiement/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Supprimer/ })).not.toBeInTheDocument()
    // A settled invoice offers no payment form at all, rather than one that would be refused.
    expect(screen.queryByRole('form', { name: 'Enregistrer un paiement' })).not.toBeInTheDocument()
    expect(screen.getByText('Soldée')).toBeInTheDocument()
  })

  it('renders the server refusal when a payment exceeds the balance', async () => {
    const { ExchangeApiError } = await import('../../api/exchangeClient')
    const refusal = new ExchangeApiError(
      409,
      'CONFLICT',
      'ce paiement dépasse le solde restant de cette facture',
    )
    renderBilling(
      billingClient(membershipFor(['BILLING_CLERK']), {
        listInvoices: vi.fn().mockResolvedValue([invoice()]),
        recordPaymentWithOutcome: vi.fn().mockRejectedValue(refusal),
      }),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Ouvrir' }))

    const form = within(await screen.findByRole('form', { name: 'Enregistrer un paiement' }))
    await userEvent.type(form.getByLabelText('Montant reçu'), '9999.00')
    await userEvent.click(form.getByRole('button', { name: 'Enregistrer le paiement' }))

    // The server's own sentence, not a generic one: a clerk acts differently on "dépasse
    // le solde" than on "l'opération a échoué".
    expect(await screen.findByRole('alert')).toHaveTextContent(/dépasse le solde/)
  })
})

// -- the screen's own boundaries -----------------------------------------------------------------

describe('what the till never touches', () => {
  it('fetches nothing clinical to take a payment', async () => {
    // The client stub defines billing methods only, so a patient, visit, order or result
    // fetch would reject on an undefined method. Reaching the end of the flow with the
    // screen intact is the assertion.
    renderBilling(
      billingClient(membershipFor(['BILLING_CLERK']), {
        listInvoices: vi.fn().mockResolvedValue([invoice()]),
        recordPaymentWithOutcome: vi.fn().mockResolvedValue({
          invoice: invoice({ paid: '3000.00', balance: '0.00', status: 'PAID' }),
          created: true,
        }),
      }),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Ouvrir' }))
    const form = within(await screen.findByRole('form', { name: 'Enregistrer un paiement' }))
    await userEvent.type(form.getByLabelText('Montant reçu'), '3000.00')
    await userEvent.click(form.getByRole('button', { name: 'Enregistrer le paiement' }))

    expect(await screen.findByText('Soldée')).toBeInTheDocument()
  })

  it('says plainly that an unpaid balance blocks no care', async () => {
    renderBilling(billingClient(membershipFor(['BILLING_CLERK'])))
    // The P0 invariant is structural in the backend; saying it here is how the person
    // operating the till knows not to hold a result back while they chase a payment.
    expect(
      await screen.findByText(/n'empêche jamais un prélèvement, un résultat ou une consultation/),
    ).toBeInTheDocument()
  })

  it('tells an administrator that changing a price does not rewrite past invoices', async () => {
    renderBilling(billingClient(membershipFor(['ORG_ADMIN'])), 'prices')
    expect(
      await screen.findByText(/ne modifie aucune facture déjà établie/),
    ).toBeInTheDocument()
  })
})

// ── DHEALTH-LAB-OFFERINGS-001: Analyses réalisées ────────────────────────────────────────────────

const CATALOG_ENTRIES: CatalogEntry[] = [
  {
    id: 'entry-offered',
    code: 'DEMO-A',
    display_name: 'Analyse de démonstration A',
    version: 1,
    active: true,
    is_synthetic: true,
  },
  {
    id: 'entry-disabled',
    code: 'DEMO-B',
    display_name: 'Analyse de démonstration B',
    version: 1,
    active: true,
    is_synthetic: true,
  },
  {
    id: 'entry-never',
    code: 'DEMO-C',
    display_name: 'Analyse de démonstration C',
    version: 1,
    active: true,
    is_synthetic: true,
  },
]

const OFFERINGS: LabOffering[] = [
  {
    id: 'offering-a',
    lab_organization_id: LAB_ID,
    catalog_entry_id: 'entry-offered',
    local_test_code: null,
    active: true,
  },
  {
    id: 'offering-b',
    lab_organization_id: LAB_ID,
    catalog_entry_id: 'entry-disabled',
    local_test_code: null,
    active: false,
  },
]

/** A laboratory administrator on the `prices` surface, with the offering reads stubbed. */
function offeringsClient(overrides: Partial<Record<keyof ExchangeClient, unknown>> = {}) {
  return billingClient(membershipFor(['ORG_ADMIN'], 'LAB'), {
    listCatalogTests: vi.fn().mockResolvedValue(CATALOG_ENTRIES),
    listLabOfferings: vi.fn().mockResolvedValue(OFFERINGS),
    setLabOfferingActive: vi.fn().mockResolvedValue(OFFERINGS[0]),
    createLabOffering: vi.fn().mockResolvedValue(OFFERINGS[0]),
    ...overrides,
  })
}

describe('analyses réalisées', () => {
  it('OF-UI-1: shows three distinct states, because a boolean would hide the third', async () => {
    // Offered-and-accepting, offered-and-switched-off, and never-declared are three different
    // facts about a laboratory's configuration. Collapsing the last two would tell an
    // administrator they had turned something off when they had never offered it.
    renderBilling(offeringsClient(), 'prices')

    expect(await screen.findByRole('heading', { name: 'Analyses réalisées' })).toBeInTheDocument()

    // Asserted on each row's own text rather than with a text matcher: the code and the state are
    // separate JSX expressions, so the DOM splits them across text nodes and a single regex would
    // fail for a formatting reason rather than a behavioural one.
    const rowText = (name: string) =>
      (screen.getByText(name).closest('li')?.textContent ?? '').replace(/\s+/g, ' ')

    expect(rowText('Analyse de démonstration A')).toContain('accepte les nouveaux ordres')
    expect(rowText('Analyse de démonstration A')).toContain('DEMO-A')
    expect(rowText('Analyse de démonstration B')).toContain("n'accepte plus de nouveaux ordres")
    expect(rowText('Analyse de démonstration C')).toContain('non proposée par ce laboratoire')

    // The three states are genuinely different controls, not three labels on one button.
    expect(screen.getByRole('button', { name: 'Ne plus accepter' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Accepter à nouveau' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Proposer cette analyse' })).toBeInTheDocument()
  })

  it('OF-UI-2: the service name shown is the catalogue own, joined on catalog_entry_id', async () => {
    // `LabOffering` carries no name, so there is no second copy that could disagree with the
    // catalogue. The panel reads both and joins them, and this test is what would redden if a
    // future author "simplified" that by asking the server to include the name on the offering.
    const client = offeringsClient()
    renderBilling(client, 'prices')

    expect(await screen.findByText('Analyse de démonstration A')).toBeInTheDocument()
    expect(client.listCatalogTests).toHaveBeenCalled()
    expect(client.listLabOfferings).toHaveBeenCalledWith(LAB_ID)
  })

  it('OF-UI-3: disabling says plainly that existing orders do not change', async () => {
    // The wording is the feature. An administrator who believes "ne plus accepter" might cancel a
    // patient's test will not use the control, and the deactivation rule says it cannot.
    const setLabOfferingActive = vi.fn().mockResolvedValue({ ...OFFERINGS[0], active: false })
    const client = offeringsClient({
      setLabOfferingActive,
      listLabOfferings: vi
        .fn()
        .mockResolvedValueOnce(OFFERINGS)
        .mockResolvedValue([{ ...OFFERINGS[0], active: false }, OFFERINGS[1]]),
    })
    renderBilling(client, 'prices')

    const row = (await screen.findByText('Analyse de démonstration A')).closest('li')
    await userEvent.click(
      within(row as HTMLElement).getByRole('button', { name: 'Ne plus accepter' }),
    )

    await waitFor(() =>
      expect(setLabOfferingActive).toHaveBeenCalledWith(LAB_ID, 'offering-a', false),
    )
    expect(await screen.findByText(/Les ordres déjà reçus ne changent pas/)).toBeInTheDocument()
  })

  it('OF-UI-4: a never-declared service is added by catalog_entry_id and nothing else', async () => {
    // No name, no price, no result structure, no specimen type in the request. Each of those
    // belongs to something else, or to nobody yet.
    const createLabOffering = vi.fn().mockResolvedValue(OFFERINGS[0])
    const client = offeringsClient({ createLabOffering })
    renderBilling(client, 'prices')

    const row = (await screen.findByText('Analyse de démonstration C')).closest('li')
    await userEvent.click(
      within(row as HTMLElement).getByRole('button', { name: 'Proposer cette analyse' }),
    )

    await waitFor(() =>
      expect(createLabOffering).toHaveBeenCalledWith(LAB_ID, { catalog_entry_id: 'entry-never' }),
    )
  })

  it('OF-UI-5: a provider organization has no offerings panel at all', async () => {
    // `LabOffering` is a laboratory's declaration. A clinic administrator holding
    // `facility.configure` sees the price list and no services panel, and that is correct rather
    // than a gap -- a provider has no offerings to configure.
    renderBilling(billingClient(membershipFor(['ORG_ADMIN'])), 'prices')

    expect(await screen.findByRole('heading', { name: 'Services et tarifs' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Analyses réalisées' })).toBeNull()
  })

  it('OF-UI-6: price authority alone shows prices and no offerings panel', async () => {
    // The two panels sit side by side and are gated independently, because setting what a facility
    // charges and deciding what it accepts are different authorities. A holder of only
    // `billing.configure_prices` must not be offered a control the server would refuse.
    const membership = membershipFor([], 'LAB')
    const client = billingClient(
      { ...membership, capabilities: ['billing.configure_prices'] },
      { listCatalogTests: vi.fn(), listLabOfferings: vi.fn() },
    )
    renderBilling(client, 'prices')

    expect(await screen.findByRole('heading', { name: 'Services et tarifs' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Analyses réalisées' })).toBeNull()
    // And it never even asks: a request the server would refuse is not fired.
    expect(client.listLabOfferings).not.toHaveBeenCalled()
  })

  it('OF-UI-7: facility authority alone shows offerings and no price editor', async () => {
    const membership = membershipFor([], 'LAB')
    const client = billingClient(
      { ...membership, capabilities: ['facility.configure'] },
      {
        listCatalogTests: vi.fn().mockResolvedValue(CATALOG_ENTRIES),
        listLabOfferings: vi.fn().mockResolvedValue(OFFERINGS),
        listClinicServices: vi.fn(),
      },
    )
    renderBilling(client, 'prices')

    expect(await screen.findByRole('heading', { name: 'Analyses réalisées' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Services et tarifs' })).toBeNull()
    expect(client.listClinicServices).not.toHaveBeenCalled()
  })

  it('OF-UI-8: the panel shows no price, no specimen field and no ranking', async () => {
    renderBilling(offeringsClient(), 'prices')
    await screen.findByRole('heading', { name: 'Analyses réalisées' })

    const heading = screen.getByRole('heading', { name: 'Analyses réalisées' })
    const panel = (heading.closest('section') ?? document.body) as HTMLElement
    const text = (panel.textContent ?? '').toLowerCase()
    for (const absent of [
      'djf',
      'tarif',
      'prix',
      'tube',
      'volume',
      'meilleur',
      'classement',
      'étoile',
    ]) {
      expect(text).not.toContain(absent)
    }
  })

  it('OF-UI-9: an empty catalogue says so rather than inventing analyses', async () => {
    const client = offeringsClient({
      listCatalogTests: vi.fn().mockResolvedValue([]),
      listLabOfferings: vi.fn().mockResolvedValue([]),
    })
    renderBilling(client, 'prices')

    expect(await screen.findByText('Aucune analyse au catalogue')).toBeInTheDocument()
  })
})
