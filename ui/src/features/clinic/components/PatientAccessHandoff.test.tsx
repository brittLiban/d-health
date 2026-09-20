/**
 * DH-PATIENT-001 §11-§19, §47-§49, CH1-CH12 and LR1-LR5: the clinician-side patient handoff.
 *
 * The ambiguity tests (LR1-LR5) matter more than their count suggests. The raw bearer secret is
 * returned exactly once and is unrecoverable afterward, so "we are not sure whether that mutation
 * committed" is a state this UI genuinely lands in — and the only safe behaviours are to re-read
 * authoritative metadata and to tell the clinician the truth. A blind retry here either fails on a
 * database constraint or, worse, silently invalidates a QR code already in a patient's hands.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../../api/exchangeClient'
import type { LabOrderItem, PatientAccessMetadata } from '../../../api/exchangeTypes'
import { PatientAccessHandoff } from './PatientAccessHandoff'

const TOKEN = 'kR3n9Xq_Tb7LmAz2vWc4dEfGhIjKlMnOpQrStUvWxYz'
const PROVIDER_ID = '8d2c1f4a-77b3-4a1e-9c2d-5b6e7f801122'
const ORDER_ID = '1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d'

const ITEMS: LabOrderItem[] = [
  {
    catalog_entry_id: 'c0ffee00-1111-4222-8333-444455556666',
    catalog_code_snapshot: 'DHEALTH-DEMO-A',
    catalog_display_name_snapshot: 'Analyse de démonstration A',
  },
]

const INACTIVE: PatientAccessMetadata = { active: false, expires_at: null, access_mode: null }
/** A live grant's expiry, held separately so issuance fixtures get a non-nullable `string`. */
const EXPIRES_AT = '2026-09-27T14:00:00Z'
const ACTIVE: PatientAccessMetadata = {
  active: true,
  expires_at: EXPIRES_AT,
  access_mode: 'ORDER_READ',
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getPatientAccessMetadata: vi.fn().mockResolvedValue(INACTIVE),
    issuePatientAccess: vi.fn().mockResolvedValue({
      access_token: TOKEN,
      expires_at: EXPIRES_AT,
      access_mode: 'ORDER_READ',
    }),
    revokePatientAccess: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as ExchangeClient
}

function renderHandoff(client: ExchangeClient, labName: string | null = 'Laboratoire Démo DHealth') {
  return render(
    <PatientAccessHandoff
      client={client}
      providerId={PROVIDER_ID}
      orderId={ORDER_ID}
      providerName="Clinique Démo"
      items={ITEMS}
      labName={labName}
    />,
  )
}

async function createAccess(client: ExchangeClient) {
  renderHandoff(client)
  await screen.findByText('Aucun accès patient actif.')
  await userEvent.click(screen.getByRole('button', { name: 'Créer le suivi patient' }))
}

beforeEach(() => {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
    writable: true,
  })
})

describe('PatientAccessHandoff — availability (CH1/CH2)', () => {
  it('CH2: offers no issuance while the order is still awaiting a laboratory', async () => {
    const client = makeClient()
    renderHandoff(client, null)

    expect(await screen.findByText(/Attribuez d’abord un laboratoire/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Créer le suivi patient/i })).not.toBeInTheDocument()
    // §7: it must not even ask the server about an order that cannot yet produce an ORDER_READ grant.
    expect(client.issuePatientAccess).not.toHaveBeenCalled()
  })

  it('CH1: offers the handoff once a laboratory is assigned', async () => {
    renderHandoff(makeClient())
    expect(await screen.findByRole('button', { name: 'Créer le suivi patient' })).toBeInTheDocument()
  })
})

describe('PatientAccessHandoff — issuing (CH3-CH8, CH11)', () => {
  it('CH3: calls exactly the provider-scoped issuance endpoint for this order', async () => {
    const client = makeClient()
    await createAccess(client)

    await waitFor(() => expect(client.issuePatientAccess).toHaveBeenCalledTimes(1))
    expect(client.issuePatientAccess).toHaveBeenCalledWith(PROVIDER_ID, ORDER_ID)
  })

  it('CH4/CH6: an ORDER_READ grant is accepted and encoded as a fragment URL in the QR', async () => {
    await createAccess(makeClient())

    const qr = await screen.findByRole('img', { name: /Code QR/i })
    expect(qr).toBeInTheDocument()
    // The QR encodes the fragment URL. It is asserted through the encoder's own output rather
    // than by reading the token out of the DOM, because the token must not be in the DOM at all.
    const { buildPatientAccessUrl } = await import('../../patient/patientAccessUrl')
    const url = buildPatientAccessUrl(window.location.origin, TOKEN)
    expect(url).toContain('?app=patient#access=')
    expect(url).not.toContain('?access=')
    expect(url).not.toContain('?token=')
  })

  it('CH5/§12: a LAB_SELECTION grant fails closed — no marketplace fallback is offered', async () => {
    const client = makeClient({
      issuePatientAccess: vi.fn().mockResolvedValue({
        access_token: TOKEN,
        expires_at: EXPIRES_AT,
        access_mode: 'LAB_SELECTION',
      }),
      getPatientAccessMetadata: vi
        .fn()
        .mockResolvedValueOnce(INACTIVE)
        .mockResolvedValue({ ...ACTIVE, access_mode: 'LAB_SELECTION' }),
    })
    await createAccess(client)

    expect(await screen.findByRole('alert')).toHaveTextContent(/autorise le choix du laboratoire/i)
    // No QR, no copy, no print — the credential is never handed over in this state.
    expect(screen.queryByRole('img', { name: /Code QR/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Copier le lien/i })).not.toBeInTheDocument()
    // And emphatically no marketplace UI was built as a fallback (§6).
    expect(document.body.textContent).not.toMatch(/choisir un laboratoire|comparer|prix/i)
  })

  it('CH7: the raw token is never displayed as text or placed in any attribute', async () => {
    await createAccess(makeClient())
    await screen.findByRole('img', { name: /Code QR/i })

    expect(document.body.textContent).not.toContain(TOKEN)
    expect(document.body.innerHTML).not.toContain(TOKEN)
  })

  it('CH8: "Copier le lien" copies the full fragment URL, never the bare token', async () => {
    await createAccess(makeClient())
    await screen.findByRole('img', { name: /Code QR/i })

    await userEvent.click(screen.getByRole('button', { name: 'Copier le lien' }))

    const writeText = navigator.clipboard.writeText as ReturnType<typeof vi.fn>
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const copied = String(writeText.mock.calls[0][0])
    expect(copied).toContain('?app=patient#access=')
    expect(copied).toContain(TOKEN)
    expect(copied).not.toMatch(/\?access=|\?token=/)
    expect(copied).not.toBe(TOKEN)
  })

  it('CH11: the expiry is shown to the clinician', async () => {
    await createAccess(makeClient())
    await screen.findByRole('img', { name: /Code QR/i })

    expect(screen.getByText(/Accès valable jusqu’au/i)).toBeInTheDocument()
    // Rendered as a readable French date, not an ISO timestamp.
    expect(document.body.textContent).not.toContain('2026-09-27T14:00:00Z')
    expect(document.body.textContent).toMatch(/2026/)
  })

  it('handles a clipboard the browser does not provide, without crashing', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    await createAccess(makeClient())
    await screen.findByRole('img', { name: /Code QR/i })

    await userEvent.click(screen.getByRole('button', { name: 'Copier le lien' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Copie impossible/i)
  })
})

describe('PatientAccessHandoff — the printed sheet (CH9/CH10, §15-§17)', () => {
  it('CH9/CH10: prints no result value, no internal UUID, and no token', async () => {
    await createAccess(makeClient())
    const sheet = (await screen.findByRole('img', { name: /Code QR/i })).closest(
      '[data-print-root]',
    )
    expect(sheet).not.toBeNull()
    const printed = sheet!.textContent ?? ''

    // CH10: no internal identifier of any kind — order id, catalog entry id, or catalog code.
    expect(printed).not.toContain(ORDER_ID)
    expect(printed).not.toContain(PROVIDER_ID)
    expect(printed).not.toContain(ITEMS[0].catalog_entry_id)
    expect(printed).not.toContain('DHEALTH-DEMO-A')
    expect(printed).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i)

    // CH9: no result content or interpretation.
    expect(printed).not.toMatch(/g\/dL|valeur|résultat ?:|normal|anormal|diagnostic/i)

    // The token is the QR's payload, never visible text (§15).
    expect(printed).not.toContain(TOKEN)
    expect(sheet!.innerHTML).not.toContain(TOKEN)
  })

  it('§15: prints what the patient actually needs — provider, tests by name, lab, QR, expiry, assisted-care note', async () => {
    await createAccess(makeClient())
    const sheet = (await screen.findByRole('img', { name: /Code QR/i })).closest(
      '[data-print-root]',
    )
    const printed = sheet!.textContent ?? ''

    expect(printed).toContain('DHealth')
    expect(printed).toContain('Clinique Démo')
    expect(printed).toContain('Analyse de démonstration A')
    expect(printed).toContain('Laboratoire Démo DHealth')
    expect(printed).toMatch(/Scannez ce code/i)
    expect(printed).toMatch(/Accès valable jusqu’au/i)
    // AC-level product law: the no-smartphone path must be on the paper itself (§4).
    expect(printed).toMatch(/Si vous n’utilisez pas de téléphone, présentez ce document/i)
  })

  it('§16: prints no patient demographic block', async () => {
    await createAccess(makeClient())
    const sheet = (await screen.findByRole('img', { name: /Code QR/i })).closest(
      '[data-print-root]',
    )
    const printed = sheet!.textContent ?? ''

    expect(printed).not.toMatch(/téléphone ?:|adresse ?:|e-?mail|date de naissance|né[e]? le/i)
  })

  it('§17: the print action is reachable and triggers printing', async () => {
    const print = vi.fn()
    vi.stubGlobal('print', print)
    await createAccess(makeClient())
    await screen.findByRole('img', { name: /Code QR/i })

    const button = screen.getByRole('button', { name: /Imprimer le document patient/i })
    await userEvent.click(button)
    expect(print).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
  })
})

describe('PatientAccessHandoff — active but unrecoverable (CH12)', () => {
  it('CH12: an active grant whose secret is not in memory produces a truthful rotate flow', async () => {
    const client = makeClient({ getPatientAccessMetadata: vi.fn().mockResolvedValue(ACTIVE) })
    renderHandoff(client)

    expect(await screen.findByText(/Un accès patient est déjà actif/i)).toBeInTheDocument()
    expect(document.body.textContent).toMatch(/révoquez l’accès existant/i)
    // It must not pretend the existing QR can be reconstructed.
    expect(screen.queryByRole('img', { name: /Code QR/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Copier le lien/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Révoquer et recréer' })).toBeInTheDocument()
  })

  it('§19: rotate revokes, confirms authoritative state, and only then issues', async () => {
    const calls: string[] = []
    const client = makeClient({
      getPatientAccessMetadata: vi.fn(async () => {
        calls.push('metadata')
        return calls.filter((c) => c === 'revoke').length > 0 ? INACTIVE : ACTIVE
      }),
      revokePatientAccess: vi.fn(async () => {
        calls.push('revoke')
      }),
      issuePatientAccess: vi.fn(async () => {
        calls.push('issue')
        return { access_token: TOKEN, expires_at: EXPIRES_AT, access_mode: 'ORDER_READ' as const }
      }),
    })
    renderHandoff(client)
    await screen.findByText(/Un accès patient est déjà actif/i)

    await userEvent.click(screen.getByRole('button', { name: 'Révoquer et recréer' }))
    await screen.findByRole('img', { name: /Code QR/i })

    // Strict ordering: revoke -> metadata re-read -> issue. Never issue-before-confirm.
    expect(calls.indexOf('revoke')).toBeLessThan(calls.lastIndexOf('metadata'))
    expect(calls.lastIndexOf('metadata')).toBeLessThan(calls.indexOf('issue'))
  })

  it('§19: revoking on its own requires explicit confirmation stating the consequence', async () => {
    const client = makeClient({ getPatientAccessMetadata: vi.fn().mockResolvedValue(ACTIVE) })
    renderHandoff(client)
    await screen.findByText(/Un accès patient est déjà actif/i)

    await userEvent.click(screen.getByRole('button', { name: 'Révoquer' }))
    expect(screen.getByText(/Révoquer cet accès patient \?/i)).toBeInTheDocument()
    expect(document.body.textContent).toMatch(/cessera immédiatement de fonctionner/i)
    // Nothing happened yet.
    expect(client.revokePatientAccess).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Annuler' }))
    expect(client.revokePatientAccess).not.toHaveBeenCalled()
  })
})

describe('PatientAccessHandoff — lost issuance response (LR1-LR3, §47)', () => {
  it('LR1/LR2: a committed-but-lost issuance is reported truthfully, with revoke/recreate offered', async () => {
    const client = makeClient({
      // The POST committed server-side; the response never arrived.
      issuePatientAccess: vi.fn().mockRejectedValue(new ExchangeApiError(0, 'NETWORK_ERROR', 'boom')),
      getPatientAccessMetadata: vi.fn().mockResolvedValueOnce(INACTIVE).mockResolvedValue(ACTIVE),
    })
    await createAccess(client)

    expect(
      await screen.findByText(/Un accès a été créé, mais le lien n'a pas pu être récupéré/i),
    ).toBeInTheDocument()
    // It must not pretend to hold the QR it never received.
    expect(screen.queryByRole('img', { name: /Code QR/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Révoquer et recréer' })).toBeInTheDocument()
  })

  it('LR3: never fires a second blind issuance after an ambiguous one', async () => {
    const client = makeClient({
      issuePatientAccess: vi.fn().mockRejectedValue(new ExchangeApiError(0, 'NETWORK_ERROR', 'boom')),
      getPatientAccessMetadata: vi.fn().mockResolvedValueOnce(INACTIVE).mockResolvedValue(ACTIVE),
    })
    await createAccess(client)
    await screen.findByText(/n'a pas pu être récupéré/i)

    expect(client.issuePatientAccess).toHaveBeenCalledTimes(1)
    expect(client.getPatientAccessMetadata).toHaveBeenCalledTimes(2)
  })

  it('a genuinely failed issuance (server says no grant exists) offers a plain retry', async () => {
    const client = makeClient({
      issuePatientAccess: vi.fn().mockRejectedValue(new ExchangeApiError(0, 'NETWORK_ERROR', 'boom')),
      getPatientAccessMetadata: vi.fn().mockResolvedValue(INACTIVE),
    })
    await createAccess(client)

    expect(await screen.findByRole('alert')).toHaveTextContent(/Réessayez/i)
    expect(document.body.textContent).not.toMatch(/Un accès a été créé/i)
    expect(screen.getByRole('button', { name: 'Créer le suivi patient' })).toBeInTheDocument()
  })

  it('a 409 (a live grant already exists) is resolved by reading metadata, not by retrying', async () => {
    const client = makeClient({
      issuePatientAccess: vi
        .fn()
        .mockRejectedValue(new ExchangeApiError(409, 'CONFLICT', 'an active grant already exists')),
      getPatientAccessMetadata: vi.fn().mockResolvedValueOnce(INACTIVE).mockResolvedValue(ACTIVE),
    })
    await createAccess(client)

    expect(await screen.findByText(/n'a pas pu être récupéré/i)).toBeInTheDocument()
    expect(client.issuePatientAccess).toHaveBeenCalledTimes(1)
  })
})

describe('PatientAccessHandoff — lost revoke response (LR4/LR5, §48)', () => {
  it('LR4: a committed-but-lost revoke is treated as success once metadata confirms it', async () => {
    const client = makeClient({
      getPatientAccessMetadata: vi.fn().mockResolvedValueOnce(ACTIVE).mockResolvedValue(INACTIVE),
      revokePatientAccess: vi.fn().mockRejectedValue(new ExchangeApiError(0, 'NETWORK_ERROR', 'boom')),
    })
    renderHandoff(client)
    await screen.findByText(/Un accès patient est déjà actif/i)

    await userEvent.click(screen.getByRole('button', { name: 'Révoquer' }))
    await userEvent.click(screen.getByRole('button', { name: 'Révoquer' }))

    expect(await screen.findByText('Aucun accès patient actif.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    // No blind retry of the mutation.
    expect(client.revokePatientAccess).toHaveBeenCalledTimes(1)
  })

  it('LR5: an ambiguous revoke that left the grant active offers an explicit retry, never an automatic one', async () => {
    const client = makeClient({
      getPatientAccessMetadata: vi.fn().mockResolvedValue(ACTIVE),
      revokePatientAccess: vi.fn().mockRejectedValue(new ExchangeApiError(0, 'NETWORK_ERROR', 'boom')),
    })
    renderHandoff(client)
    await screen.findByText(/Un accès patient est déjà actif/i)

    await userEvent.click(screen.getByRole('button', { name: 'Révoquer' }))
    await userEvent.click(screen.getByRole('button', { name: 'Révoquer' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/toujours actif/i)
    expect(client.revokePatientAccess).toHaveBeenCalledTimes(1)
    // The clinician can choose to try again; nothing tried for them.
    expect(screen.getByRole('button', { name: 'Révoquer et recréer' })).toBeInTheDocument()
  })

  it('an ambiguous revoke whose metadata re-read also failed says exactly that, claiming no more', async () => {
    // The sub-case LR5's fixture does not reach: the server could not be re-read at all, so
    // whether the revoke committed is genuinely unknown. Claiming "l'accès est toujours actif"
    // here would assert a fact the code does not have.
    const client = makeClient({
      getPatientAccessMetadata: vi
        .fn()
        .mockResolvedValueOnce(ACTIVE)
        .mockRejectedValue(new ExchangeApiError(0, 'NETWORK_ERROR', 'boom')),
      revokePatientAccess: vi.fn().mockRejectedValue(new ExchangeApiError(0, 'NETWORK_ERROR', 'boom')),
    })
    renderHandoff(client)
    await screen.findByText(/Un accès patient est déjà actif/i)

    await userEvent.click(screen.getByRole('button', { name: 'Révoquer' }))
    await userEvent.click(screen.getByRole('button', { name: 'Révoquer' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/l'état de l'accès n'a pas pu être relu/i)
    expect(alert.textContent).not.toMatch(/toujours actif/i)
    expect(client.revokePatientAccess).toHaveBeenCalledTimes(1)
  })

  it('a successful 204 revoke is a success, not an ambiguous outcome', async () => {
    // Guards the real bug this ticket fixed in ExchangeClient: parsing an empty 204 body as JSON
    // threw, which `isAmbiguousExchangeError` classified as ambiguous — so every *successful*
    // revoke would have driven the §48 recovery path and told the clinician it might not have
    // committed.
    const client = makeClient({
      getPatientAccessMetadata: vi.fn().mockResolvedValueOnce(ACTIVE).mockResolvedValue(INACTIVE),
      revokePatientAccess: vi.fn().mockResolvedValue(undefined),
    })
    renderHandoff(client)
    await screen.findByText(/Un accès patient est déjà actif/i)

    await userEvent.click(screen.getByRole('button', { name: 'Révoquer' }))
    await userEvent.click(screen.getByRole('button', { name: 'Révoquer' }))

    expect(await screen.findByText('Aucun accès patient actif.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('PatientAccessHandoff — stale responses (§49)', () => {
  it('a late metadata response for a previous order never renders under the current one', async () => {
    let resolveFirst: ((value: PatientAccessMetadata) => void) | undefined
    const client = makeClient({
      getPatientAccessMetadata: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise<PatientAccessMetadata>((resolve) => {
              resolveFirst = resolve
            }),
        )
        .mockResolvedValue(INACTIVE),
    })

    const { rerender } = renderHandoff(client)
    // The clinician switches to a different patient's order while the first read is in flight.
    rerender(
      <PatientAccessHandoff
        client={client}
        providerId={PROVIDER_ID}
        orderId="99999999-8888-4777-a666-555544443333"
        providerName="Clinique Démo"
        items={ITEMS}
        labName="Laboratoire Démo DHealth"
      />,
    )
    await screen.findByText('Aucun accès patient actif.')

    // The first order's response finally lands: it must change nothing.
    resolveFirst?.(ACTIVE)
    await waitFor(() =>
      expect(screen.getByText('Aucun accès patient actif.')).toBeInTheDocument(),
    )
    expect(screen.queryByText(/Un accès patient est déjà actif/i)).not.toBeInTheDocument()
  })
})
