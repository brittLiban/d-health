/**
 * DH-PATIENT-001 §20/§21, AC1/AC2/AC25: top-level application routing for DHealth Patient.
 *
 * The central claim here is a **negative** one and cannot be shown by rendering the patient
 * screen and being satisfied that it looked right: no Keycloak adapter may be constructed, and no
 * `init()` may run, on the patient path. A patient has no account and must never be bounced to a
 * login page — that is ADR-043's fourth product law (no digital-literacy requirement for care), not
 * a preference. So `keycloak-js` is mocked here and the constructor itself is asserted never to
 * have been called.
 *
 * The same file also pins the other half of §21: Clinic and Lab keep their authentication exactly
 * as it was. Adding a third, unauthenticated surface must not weaken the two authenticated ones.
 */

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const keycloakInit = vi.fn().mockResolvedValue(false)
const keycloakConstructor = vi.fn()

vi.mock('keycloak-js', () => ({
  default: class MockKeycloak {
    authenticated = false
    token: string | undefined
    init = keycloakInit
    login = vi.fn()
    logout = vi.fn()
    updateToken = vi.fn()

    constructor(config: unknown) {
      keycloakConstructor(config)
    }
  },
}))

// The patient surface's own network call is irrelevant to routing; keep it inert and silent.
vi.mock('./features/patient/patientAccessClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./features/patient/patientAccessClient')>()
  return {
    ...actual,
    PatientAccessClient: class {
      getOrder = vi.fn().mockRejectedValue(new actual.PatientAccessError('invalid_access'))
    },
  }
})

const { default: App } = await import('./App')

describe('App routing — DHealth Patient (§20/§21)', () => {
  beforeEach(() => {
    keycloakConstructor.mockClear()
    keycloakInit.mockClear()
    window.history.replaceState(null, '', '/?app=patient')
  })

  afterEach(() => {
    window.history.replaceState(null, '', '/')
  })

  it('AC1: ?app=patient renders DHealth Patient', async () => {
    render(<App />)
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Votre analyse')
  })

  it('AC2/AR#1: no Keycloak adapter is ever constructed or initialised on the patient route', async () => {
    render(<App />)
    await screen.findByRole('heading', { level: 1 })

    expect(keycloakConstructor).not.toHaveBeenCalled()
    expect(keycloakInit).not.toHaveBeenCalled()
  })

  it('AC2: the patient is never shown an authentication gate or a login control', async () => {
    render(<App />)
    await screen.findByRole('heading', { level: 1 })

    expect(document.body.textContent).not.toMatch(/connexion|se connecter|authentifi|session/i)
    expect(screen.queryByRole('button', { name: /connexion|se connecter/i })).not.toBeInTheDocument()
  })

  it('renders the patient app from the initialApp prop too, without touching Keycloak', async () => {
    window.history.replaceState(null, '', '/')
    render(<App initialApp="patient" />)

    await screen.findByRole('heading', { level: 1 })
    expect(keycloakConstructor).not.toHaveBeenCalled()
  })

  /*
   * AC25/§71: the other half of the routing claim -- Clinic and Lab remain gated.
   *
   * These assert the gate, not the Keycloak constructor, and that is deliberate: under jsdom the
   * hostname is bare `localhost`, so `resolveExchangeAuthConfig` fails closed *before* an adapter
   * is ever constructed (DH-CLINIC-004A's own "never send authentication to a guessed host"
   * rule). The observable, environment-independent property is the one that matters here anyway:
   * neither surface renders its application content without an established session, and neither
   * falls through to the unauthenticated patient surface.
   */
  // DH-MVP-POLISH-001: `?app=clinic` and `?app=lab` are now aliases of the one
  // staff application rather than two surfaces, and the gate names the product
  // instead of a sub-application — it cannot honestly claim to be "DHealth
  // Clinique" while refusing someone whose memberships it has not yet read. The
  // invariant is unchanged and is asserted for every route that reaches the staff
  // application: gated, and never falling through to the patient surface.
  for (const route of ['/?app=staff', '/?app=clinic', '/?app=lab', '/']) {
    it('AC25/§71: the staff application stays gated at ' + route, async () => {
      window.history.replaceState(null, '', route)
      render(<App />)

      await screen.findByRole('alert')
      expect(document.body.textContent).toMatch(/DHealth/i)
      expect(screen.queryByText('Votre analyse')).not.toBeInTheDocument()
    })
  }

  it('an unknown ?app value does not fall through to the unauthenticated patient surface', async () => {
    window.history.replaceState(null, '', '/?app=nonsense')
    render(<App />)

    await waitFor(() => expect(screen.queryByText('Votre analyse')).not.toBeInTheDocument())
  })
})
