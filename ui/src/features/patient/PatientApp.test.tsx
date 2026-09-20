/**
 * DH-PATIENT-001 §22/§23/§25/§50, PT1-PT7: credential ingestion, scrubbing, non-persistence, and
 * the generic invalid-link behaviour.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PatientOrder } from '../../api/exchangeTypes'
import { PatientApp } from './PatientApp'
import { PatientAccessClient, PatientAccessError } from './patientAccessClient'

const TOKEN = 'kR3n9Xq_Tb7LmAz2vWc4dEfGhIjKlMnOpQrStUvWxYz'

const ORDER: PatientOrder = {
  status: 'IN_PROGRESS',
  requested_tests: [
    { catalog_code_snapshot: 'DHEALTH-DEMO-A', catalog_display_name_snapshot: 'Analyse de démonstration A' },
  ],
  ordering_provider_name: 'Clinique Démo',
  selection: null,
  result_status: 'NOT_READY',
}

function stubClient(impl: Partial<PatientAccessClient>): PatientAccessClient {
  return impl as PatientAccessClient
}

describe('PatientApp — credential ingestion', () => {
  let replaceState: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    window.history.replaceState(null, '', '/?app=patient')
    replaceState = vi.spyOn(window.history, 'replaceState')
  })

  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
    sessionStorage.clear()
  })

  it('PT1/PT3: parses the fragment credential and sends it as a bearer header, holding it only in memory', async () => {
    const getOrder = vi.fn().mockResolvedValue(ORDER)
    render(<PatientApp client={stubClient({ getOrder })} initialHash={`#access=${TOKEN}`} />)

    await screen.findByText('Analyse en cours')
    expect(getOrder).toHaveBeenCalledTimes(1)
    expect(getOrder.mock.calls[0][0]).toBe(TOKEN)

    // PT3: the credential is nowhere in the rendered DOM -- not as text, not in an attribute.
    expect(document.body.innerHTML).not.toContain(TOKEN)
  })

  it('PT2: scrubs the fragment from the visible URL immediately after ingestion', async () => {
    render(
      <PatientApp
        client={stubClient({ getOrder: vi.fn().mockResolvedValue(ORDER) })}
        initialHash={`#access=${TOKEN}`}
      />,
    )

    await waitFor(() => expect(replaceState).toHaveBeenCalled())
    for (const call of replaceState.mock.calls) {
      expect(String(call[2])).not.toContain(TOKEN)
      expect(String(call[2])).not.toContain('access=')
    }
    expect(window.location.hash).toBe('')
    // The app identity survives the scrub, so a refresh lands back on the patient surface.
    expect(window.location.search).toBe('?app=patient')
  })

  it('PT4/PT5: never writes the credential to localStorage, sessionStorage, or a cookie', async () => {
    const localSet = vi.spyOn(Storage.prototype, 'setItem')
    render(
      <PatientApp
        client={stubClient({ getOrder: vi.fn().mockResolvedValue(ORDER) })}
        initialHash={`#access=${TOKEN}`}
      />,
    )
    await screen.findByText('Analyse en cours')

    // Nothing was written at all; and if some future change starts writing, it must not be this.
    for (const call of localSet.mock.calls) {
      expect(String(call[1])).not.toContain(TOKEN)
    }
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    expect(document.cookie).not.toContain(TOKEN)
  })

  it('PT6: with no fragment (e.g. after a refresh) shows the rescan message and makes no request', async () => {
    const getOrder = vi.fn()
    render(<PatientApp client={stubClient({ getOrder })} initialHash="" />)

    expect(
      await screen.findByText(/scannez de nouveau votre QR/i),
    ).toBeInTheDocument()
    expect(getOrder).not.toHaveBeenCalled()
  })

  it('PT7: an invalid/expired/revoked credential produces one generic message, never a reason', async () => {
    const getOrder = vi.fn().mockRejectedValue(new PatientAccessError('invalid_access'))
    render(<PatientApp client={stubClient({ getOrder })} initialHash={`#access=${TOKEN}`} />)

    const message = await screen.findByText(/Ce lien n’est plus valide/i)
    expect(message).toBeInTheDocument()
    // §25: nothing in the UI distinguishes expired from revoked from unknown.
    expect(document.body.textContent).not.toMatch(/expir|révoqu|inconnu|introuvable/i)
  })

  it('distinguishes a service outage from a credential refusal, and offers a retry', async () => {
    const getOrder = vi
      .fn()
      .mockRejectedValueOnce(new PatientAccessError('unavailable'))
      .mockResolvedValueOnce(ORDER)
    render(<PatientApp client={stubClient({ getOrder })} initialHash={`#access=${TOKEN}`} />)

    await screen.findByText(/Connexion impossible pour le moment/i)
    // The outage message must not accuse the patient's link of being invalid.
    expect(screen.queryByText(/Ce lien n’est plus valide/i)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Réessayer/i }))
    await screen.findByText('Analyse en cours')
    expect(getOrder).toHaveBeenCalledTimes(2)
  })

  it('PT3 (lifetime): the credential is gone from memory once the component unmounts', async () => {
    const getOrder = vi.fn().mockResolvedValue(ORDER)
    const { unmount } = render(
      <PatientApp client={stubClient({ getOrder })} initialHash={`#access=${TOKEN}`} />,
    )
    await screen.findByText('Analyse en cours')
    unmount()

    expect(document.body.innerHTML).not.toContain(TOKEN)
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('§50: a response that lands after unmount never writes state', async () => {
    let resolveOrder: ((order: PatientOrder) => void) | undefined
    const getOrder = vi.fn(
      () =>
        new Promise<PatientOrder>((resolve) => {
          resolveOrder = resolve
        }),
    )
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const { unmount } = render(
      <PatientApp client={stubClient({ getOrder })} initialHash={`#access=${TOKEN}`} />,
    )
    unmount()
    resolveOrder?.(ORDER)
    await Promise.resolve()

    // React logs a warning when a state update lands on an unmounted tree; none may occur.
    expect(errorSpy).not.toHaveBeenCalled()
  })
})

describe('PatientAccessClient', () => {
  it('sends the credential only in the Authorization header, never in the URL', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(ORDER), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    const client = new PatientAccessClient({ baseUrl: '/exchange/v1', fetchImpl })

    await client.getOrder(TOKEN)

    const [url, init] = fetchImpl.mock.calls[0]
    expect(String(url)).toBe('/exchange/v1/patient/order')
    expect(String(url)).not.toContain(TOKEN)
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`)
  })

  it('collapses 401/403/404 to one indistinguishable failure kind carrying no server detail', async () => {
    for (const status of [401, 403, 404]) {
      const fetchImpl = vi
        .fn()
        .mockResolvedValue(new Response(JSON.stringify({ detail: 'grant expired at 12:03' }), { status }))
      const client = new PatientAccessClient({ baseUrl: '/exchange/v1', fetchImpl })

      const error = await client.getOrder(TOKEN).catch((caught: unknown) => caught)
      expect(error).toBeInstanceOf(PatientAccessError)
      expect((error as PatientAccessError).kind).toBe('invalid_access')
      // The server's own detail string must not survive into the client-side error.
      expect((error as PatientAccessError).message).not.toContain('expired')
      expect((error as PatientAccessError).message).not.toContain(TOKEN)
    }
  })

  it('reports a 5xx/network failure as unavailable, not as a credential problem', async () => {
    const serverError = new PatientAccessClient({
      baseUrl: '/exchange/v1',
      fetchImpl: vi.fn().mockResolvedValue(new Response('boom', { status: 503 })),
    })
    await expect(serverError.getOrder(TOKEN)).rejects.toMatchObject({ kind: 'unavailable' })

    const networkError = new PatientAccessClient({
      baseUrl: '/exchange/v1',
      fetchImpl: vi.fn().mockRejectedValue(new TypeError('Failed to fetch')),
    })
    await expect(networkError.getOrder(TOKEN)).rejects.toMatchObject({ kind: 'unavailable' })
  })

  it('§24/§67: exposes no marketplace capability at all', () => {
    const client = new PatientAccessClient()
    const methods = new Set([
      ...Object.getOwnPropertyNames(Object.getPrototypeOf(client)),
      ...Object.keys(client),
    ])
    expect(methods).toContain('getOrder')
    for (const forbidden of ['getLabOptions', 'listLabOptions', 'selectLab', 'createSelection']) {
      expect(methods).not.toContain(forbidden)
    }
  })
})
