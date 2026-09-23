/**
 * DH-CONNECT-CONTRACT-001 §49/§76/§80 — the connector panel.
 *
 * Almost every test here is about a sentence *not* appearing. The failure mode
 * this screen guards against is not a crash; it is an operator reading four
 * separate facts as one reassuring green state and sending real orders to a
 * laboratory that cannot receive them. So: "qualifié" never appears unqualified,
 * "en service" never appears at all, no control marks anything qualified, and a
 * screen rendered without colour says exactly the same thing as one with it.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import { ExchangeApiError, type ExchangeClient } from '../../api/exchangeClient'
import type {
  ConnectorInstallation,
  ConnectorQualificationRecord,
  FacilityConnector,
} from '../../api/exchangeTypes'
import { ConnectorQualification } from './ConnectorQualification'

const FP_CONFIG = 'a'.repeat(64)
const FP_MAPPING = 'b'.repeat(64)

const INSTALLATION: ConnectorInstallation = {
  installation_id: 'inst-1',
  organization_id: 'org-lab',
  connector_key: 'openelis-fhir',
  connector_version: '0.1',
  contract_version: '1.0',
  source_system: 'OpenELIS',
  source_system_version: '2.7',
  interface_type: 'FHIR',
  configuration_fingerprint: FP_CONFIG,
  mapping_fingerprint: FP_MAPPING,
  status: 'REGISTERED',
  service_client_id: null,
  created_at: '2026-09-01T08:00:00Z',
  updated_at: '2026-09-01T08:00:00Z',
}

const LAB_BUNDLE = [
  'ORDER_RECEIPT',
  'BUSINESS_ACCEPTANCE',
  'COLLECTION_EVENT',
  'RELEASED_RESULT_PUBLICATION',
  'AMENDMENT_PUBLICATION',
]

const PASSED_RUN: ConnectorQualificationRecord = {
  qualification_id: 'qual-1',
  result: 'PASSED',
  suite_version: '2',
  started_at: '2026-09-10T08:00:00Z',
  completed_at: '2026-09-10T08:04:00Z',
  contract_version: '1.0',
  connector_version: '0.1',
  configuration_fingerprint: FP_CONFIG,
  mapping_fingerprint: FP_MAPPING,
  evidence_summary:
    '16 scénario(s) exécuté(s), tous satisfaits ; déclaration seule : LQ1-order-receipt-declared',
  capabilities: LAB_BUNDLE.map((capability) => ({
    capability,
    passed: true,
    // Hand-built EXERCISED across the board, including ORDER_RECEIPT. This fixture exists to
    // render the fully-qualified panel; no run in this build produces it, and
    // SUITE_PASSED_INCOMPLETE below is what the synthetic harness actually yields.
    evidence_level: 'EXERCISED' as const,
    detail: null,
  })),
}

const NO_CONNECTOR: FacilityConnector = {
  organization_id: 'org-lab',
  participation_mode: 'CONNECT',
  installation: null,
  installations: [],
  verdict: {
    qualified: false,
    gap: 'NO_INSTALLATION',
    detail: "aucun connecteur n'est enregistré pour cet établissement",
    qualification_id: null,
    completed_at: null,
    missing_capabilities: [],
    unexercised_capabilities: [],
    installed_contract_version: null,
    current_contract_version: '1.0',
    current_suite_version: '2',
  },
  health_state: 'UNKNOWN',
  operational: false,
  // True since DH-CONNECT-AGENT-001: DHealth has a delivery runtime. Still says nothing about
  // whether *this* facility may take clinical work.
  transport_available: true,
  runtime: {
    runtime_instance_id: null,
    agent_version: null,
    last_seen_at: null,
    adapter_reachable: null,
    pending_deliveries: 0,
    dead_letters: 0,
    oldest_pending_seconds: null,
    reasons: ["Aucun agent ne s'est jamais annoncé pour ce connecteur."],
  },
  qualification_history: [],
}

const REGISTERED_UNQUALIFIED: FacilityConnector = {
  ...NO_CONNECTOR,
  installation: INSTALLATION,
  installations: [INSTALLATION],
  verdict: {
    ...NO_CONNECTOR.verdict,
    gap: 'NEVER_QUALIFIED',
    detail: "le connecteur enregistré n'a pas encore été qualifié",
  },
}

const QUALIFIED: FacilityConnector = {
  ...REGISTERED_UNQUALIFIED,
  verdict: {
    qualified: true,
    gap: null,
    detail: 'connecteur qualifié pour cette configuration',
    qualification_id: 'qual-1',
    completed_at: '2026-09-10T08:04:00Z',
    missing_capabilities: [],
    unexercised_capabilities: [],
    installed_contract_version: '1.0',
    current_contract_version: '1.0',
    current_suite_version: '2',
  },
  qualification_history: [PASSED_RUN],
}

const STALE: FacilityConnector = {
  ...QUALIFIED,
  installation: { ...INSTALLATION, configuration_fingerprint: 'c'.repeat(64) },
  verdict: {
    ...QUALIFIED.verdict,
    qualified: false,
    gap: 'QUALIFICATION_STALE',
    detail: 'la configuration a changé depuis la dernière qualification',
  },
}

const FAILED: FacilityConnector = {
  ...REGISTERED_UNQUALIFIED,
  verdict: {
    ...REGISTERED_UNQUALIFIED.verdict,
    gap: 'LAST_ATTEMPT_FAILED',
    detail: 'la dernière qualification a échoué',
  },
  qualification_history: [
    {
      ...PASSED_RUN,
      qualification_id: 'qual-0',
      result: 'FAILED',
      evidence_summary: '12/14 scénario(s) satisfaits',
      capabilities: LAB_BUNDLE.map((capability) => ({
        capability,
        passed: capability !== 'AMENDMENT_PUBLICATION',
        evidence_level: 'EXERCISED' as const,
        detail:
          capability === 'AMENDMENT_PUBLICATION'
            ? 'LQ10-amendment: capacité non déclarée par le connecteur'
            : null,
      })),
    },
  ],
}

/**
 * What the synthetic laboratory harness actually produces, and therefore the most important
 * fixture in this file.
 *
 * The suite ran and every scenario it could execute behaved correctly, so the run is PASSED and
 * stays PASSED. `ORDER_RECEIPT` is DECLARED, because it is the one DHealth → connector capability
 * and there is no transport to drive it over. Production qualification is consequently incomplete.
 *
 * Both of those are true at once. A screen that cannot hold them apart will show a green
 * "Qualification réussie" over a connector nobody has proved can receive an order.
 */
const SUITE_PASSED_INCOMPLETE: FacilityConnector = {
  ...REGISTERED_UNQUALIFIED,
  verdict: {
    ...REGISTERED_UNQUALIFIED.verdict,
    qualified: false,
    gap: 'REQUIRED_CAPABILITY_UNEXERCISED',
    detail:
      '1 capacité requise déclarée par le connecteur mais non encore exercée ; la qualification' +
      ' de production est incomplète',
    qualification_id: 'qual-1',
    completed_at: '2026-09-10T08:04:00Z',
    unexercised_capabilities: ['ORDER_RECEIPT'],
  },
  qualification_history: [
    {
      ...PASSED_RUN,
      capabilities: LAB_BUNDLE.map((capability) => ({
        capability,
        passed: true,
        evidence_level:
          capability === 'ORDER_RECEIPT' ? ('DECLARED' as const) : ('EXERCISED' as const),
        detail:
          capability === 'ORDER_RECEIPT'
            ? "LQ1-order-receipt-declared: déclaration seule : la réception d'une commande n'est" +
              " pas exercée dans cette version"
            : null,
      })),
    },
  ],
}

const SUSPENDED: FacilityConnector = {
  ...QUALIFIED,
  installation: { ...INSTALLATION, status: 'SUSPENDED' },
  installations: [{ ...INSTALLATION, status: 'SUSPENDED' }],
  verdict: {
    ...QUALIFIED.verdict,
    qualified: false,
    gap: 'SUSPENDED',
    detail: 'le connecteur est suspendu',
  },
}

function client(
  connector: FacilityConnector | FacilityConnector[],
  overrides: Partial<Record<string, unknown>> = {},
) {
  const pages = Array.isArray(connector) ? connector : [connector]
  let call = 0
  return {
    getFacilityConnector: vi.fn(async () => {
      const page = pages[Math.min(call, pages.length - 1)]
      call += 1
      return page
    }),
    setFacilityConnectorStatus: vi.fn(async () => pages[pages.length - 1]),
    ...overrides,
  } as unknown as ExchangeClient
}

function mount(connector: FacilityConnector | FacilityConnector[], overrides = {}) {
  const api = client(connector, overrides)
  const onChanged = vi.fn()
  render(
    <ConnectorQualification organizationId="org-lab" client={api} onChanged={onChanged} />,
  )
  return { api, onChanged }
}

/**
 * A connector whose agent is reporting in and whose source system answers.
 *
 * Built on the *unqualified* fixture deliberately. Health and qualification are separate questions
 * (§16), and a fixture that could only be healthy while qualified would quietly make them one --
 * which is exactly the conflation these screens exist to prevent.
 */
const HEALTHY_RUNTIME: FacilityConnector = {
  ...REGISTERED_UNQUALIFIED,
  health_state: 'HEALTHY',
  operational: true,
  runtime: {
    runtime_instance_id: 'agent-7f31c0',
    agent_version: '0.1.0',
    last_seen_at: '2026-09-23T08:00:00Z',
    adapter_reachable: true,
    pending_deliveries: 0,
    dead_letters: 0,
    oldest_pending_seconds: null,
    reasons: [],
  },
}

/** Alive, answering, and unable to reach its LIS. Not operational -- §63. */
const DEGRADED_RUNTIME: FacilityConnector = {
  ...HEALTHY_RUNTIME,
  health_state: 'DEGRADED',
  operational: false,
  runtime: {
    ...HEALTHY_RUNTIME.runtime,
    adapter_reachable: false,
    pending_deliveries: 4,
    dead_letters: 1,
    oldest_pending_seconds: 900,
    reasons: [
      "L'agent ne joint pas le système source.",
      '4 message(s) en attente, le plus ancien depuis 15 min.',
      '1 message(s) non livrable(s) en attente de décision.',
    ],
  },
}

describe('ConnectorQualification', () => {
  it('CQ1 states the real limit first: no qualified vendor adapter', async () => {
    mount(NO_CONNECTOR)
    // This sentence is true for every state this component can render, so it must not be
    // conditional on any of them -- including the qualified one with a healthy agent.
    //
    // It replaces "le transport n'est pas construit", which DH-CONNECT-AGENT-001 made false: a
    // durable delivery runtime now exists. The limit moved rather than disappearing, and the
    // assertion moved with it. Pinning the *claim* rather than the old phrasing is the point --
    // a test asserting a sentence that is no longer true would have to be deleted, and deleting a
    // test is how a guarantee quietly stops being checked.
    expect(
      await screen.findByText(/aucun adaptateur éditeur qualifié n'existe/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/il ne se connecte à aucun système réel/i)).toBeInTheDocument()
  })

  it('CQ2 says plainly when no connector is registered', async () => {
    mount(NO_CONNECTOR)
    expect(
      await screen.findByText(/Rien n'est installé ici/i),
    ).toBeInTheDocument()
    expect(screen.getByText('Non qualifiée')).toBeInTheDocument()
  })

  it('CQ3 a registered connector is not described as qualified', async () => {
    mount(REGISTERED_UNQUALIFIED)
    await screen.findByText('openelis-fhir 0.1')
    expect(screen.getByText('Non qualifiée')).toBeInTheDocument()
    expect(screen.queryByText('Qualifiée')).not.toBeInTheDocument()
    expect(screen.getByText(/Jamais qualifié/)).toBeInTheDocument()
  })

  it('CQ4 a qualified connector is still reported as not in service', async () => {
    // The assertion the whole ticket turns on. Qualified and unroutable at once
    // is not a contradiction to be tidied away -- it is the honest state, and the
    // screen has to be able to hold both without one implying the other.
    mount(QUALIFIED)
    expect(await screen.findByText('Qualifiée')).toBeInTheDocument()
    // "En service — Non", read off the definition row rather than a sentence. This fixture has no
    // agent at all, which since DH-CONNECT-AGENT-001 is one of the reasons a connector is not in
    // service rather than the only one -- so the assertion pins the *answer*, not the wording.
    expect(screen.getByText('En service').nextElementSibling?.textContent).toBe('Non')
    expect(screen.getByText(/jamais annoncé pour ce connecteur/i)).toBeInTheDocument()
    expect(screen.getByText(/Routage de production — désactivé/)).toBeInTheDocument()
    expect(screen.queryByText(/^Connecté$/)).not.toBeInTheDocument()
  })

  it('CQ5 a stale qualification names the configuration change as the reason', async () => {
    mount(STALE)
    expect(await screen.findByText('Non qualifiée')).toBeInTheDocument()
    expect(screen.getByText(/Qualification périmée/)).toBeInTheDocument()
    // The evidence is still listed: the run really did pass, about a configuration
    // that is no longer installed, and deleting it would lose that fact.
    expect(screen.getByText(/Réussie le/)).toBeInTheDocument()
  })

  it('CQ6 a failed run names the capability that failed, in French', async () => {
    mount(FAILED)
    expect(await screen.findByText(/Échouée le/)).toBeInTheDocument()
    const row = screen.getByRole('row', { name: /Publication des rectificatifs/ })
    expect(within(row).getByText('Non satisfaite')).toBeInTheDocument()
    // No raw enum anywhere in the capability column.
    expect(screen.queryByText('AMENDMENT_PUBLICATION')).not.toBeInTheDocument()
    expect(screen.queryByText('RELEASED_RESULT_PUBLICATION')).not.toBeInTheDocument()
  })

  it('CQ7 offers no control that marks a connector qualified', async () => {
    // §42, asserted on the rendered DOM rather than trusted to code review. Any
    // button that could make an operator the author of a qualification fails this.
    mount(REGISTERED_UNQUALIFIED)
    await screen.findByText('openelis-fhir 0.1')
    for (const button of screen.getAllByRole('button')) {
      expect(button.textContent ?? '').not.toMatch(/qualifi/i)
    }
    expect(screen.queryByRole('button', { name: /marquer/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('CQ8 suspending a connector re-reads the state and tells the page', async () => {
    const user = userEvent.setup()
    const { api, onChanged } = mount([QUALIFIED, SUSPENDED])
    await screen.findByText('Qualifiée')

    await user.click(screen.getByRole('button', { name: /Suspendre le connecteur/ }))

    await waitFor(() => expect(screen.getByText(/Connecteur suspendu/)).toBeInTheDocument())
    expect(api.setFacilityConnectorStatus).toHaveBeenCalledWith('org-lab', 'inst-1', 'SUSPENDED')
    // Readiness above derives from the same rows; not refreshing it would leave
    // the page's own verdict contradicting this section.
    expect(onChanged).toHaveBeenCalled()
  })

  it('CQ9 a retired connector refuses further changes with a sentence, not a stack trace', async () => {
    const user = userEvent.setup()
    mount(REGISTERED_UNQUALIFIED, {
      setFacilityConnectorStatus: vi.fn(async () => {
        throw new ExchangeApiError(409, 'CONFLICT', 'conflict')
      }),
    })
    await screen.findByText('openelis-fhir 0.1')
    await user.click(screen.getByRole('button', { name: /Retirer définitivement…/ }))
    await user.click(screen.getByRole('button', { name: /^Retirer définitivement$/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/ne peut plus être modifié/)
  })

  it('CQ14 retirement takes two gestures and states what it costs', async () => {
    // The only irreversible action on this screen, sitting one word away from
    // "Réactiver". Route activation already takes two gestures with a
    // restatement; a permanent retirement taking one would be the weaker
    // safeguard on the more damaging action.
    const user = userEvent.setup()
    const { api } = mount(REGISTERED_UNQUALIFIED)
    await screen.findByText('openelis-fhir 0.1')

    await user.click(screen.getByRole('button', { name: /Retirer définitivement…/ }))
    expect(api.setFacilityConnectorStatus).not.toHaveBeenCalled()
    // The restatement names the connector by key, so the operator confirms against the thing in
    // front of them rather than against the word "connecteur".
    const restatement = screen.getByText(/ne pourra plus être réactivé ni requalifié/)
    expect(restatement).toHaveTextContent('openelis-fhir')
    expect(screen.getByText(/qualifications déjà enregistrées sont conservées/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^Annuler$/ }))
    expect(api.setFacilityConnectorStatus).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: /Retirer définitivement…/ }))
    await user.click(screen.getByRole('button', { name: /^Retirer définitivement$/ }))
    expect(api.setFacilityConnectorStatus).toHaveBeenCalledWith('org-lab', 'inst-1', 'RETIRED')
  })

  it('CQ15 does not tell a non-integrable facility to build a connector', async () => {
    // Directly under a red banner saying the partner's software exposes no
    // interface DHealth can safely exchange over. "Un connecteur doit être
    // construit pour ce système" there would be the screen arguing with itself.
    render(
      <ConnectorQualification
        organizationId="org-lab"
        client={client(NO_CONNECTOR)}
        integrationFeasibility="NOT_SAFELY_INTEGRABLE"
        onChanged={vi.fn()}
      />,
    )
    expect(await screen.findByText(/rien ne peut l'être tant que le logiciel actuel/)).toBeInTheDocument()
    expect(screen.queryByText(/doit être construit pour ce système/)).not.toBeInTheDocument()
  })

  it('CQ10 never renders a fingerprint as if it were a configuration', async () => {
    // §34: a digest is shown, shortened, inside <code>, and no field on this
    // screen is a URL, host, or anything a credential could hide in.
    const { container } = render(
      <ConnectorQualification
        organizationId="org-lab"
        client={client(QUALIFIED)}
        onChanged={vi.fn()}
      />,
    )
    await screen.findByText('Qualifiée')
    expect(screen.getAllByText(`${FP_CONFIG.slice(0, 12)}…`).length).toBeGreaterThan(0)
    expect(container.textContent).not.toContain(FP_CONFIG)
    expect(container.textContent).not.toMatch(/https?:\/\//)
  })

  it('CQ11 states qualification without relying on colour', async () => {
    // §80. Every state carries a word: "Qualifié", "Non qualifié", "Satisfaite",
    // "Non satisfaite". A reader who cannot distinguish the classes loses nothing.
    const { container } = render(
      <ConnectorQualification
        organizationId="org-lab"
        client={client(FAILED)}
        onChanged={vi.fn()}
      />,
    )
    await screen.findByText('Non qualifiée')
    for (const element of Array.from(container.querySelectorAll('[class]'))) {
      // No element conveys state purely by class with no text of its own.
      if ((element.className as string).match(/done|owed|stopped/)) {
        expect((element.textContent ?? '').trim().length).toBeGreaterThan(0)
      }
    }
  })

  it('CQ12 has no accessibility violations', async () => {
    const { container } = render(
      <ConnectorQualification
        organizationId="org-lab"
        client={client(QUALIFIED)}
        onChanged={vi.fn()}
      />,
    )
    await screen.findByText('Qualifiée')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('CQ13 a refused read says who may look, not "something went wrong"', async () => {
    mount(NO_CONNECTOR, {
      getFacilityConnector: vi.fn(async () => {
        throw new ExchangeApiError(403, 'FORBIDDEN', 'forbidden')
      }),
    })
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /Seule la plateforme DHealth peut consulter les connecteurs/,
    )
  })

  it('CQ16 separates the suite result from production qualification', async () => {
    // The correction's whole point on screen. Both statements are true at once, and a panel
    // that showed only the first would tell an operator a connector is ready when one required
    // capability rests on the connector's own say-so.
    mount(SUITE_PASSED_INCOMPLETE)
    await screen.findByText('openelis-fhir 0.1')

    expect(screen.getByText('Suite de qualification')).toBeInTheDocument()
    expect(screen.getByText('Réussie')).toBeInTheDocument()
    expect(screen.getByText('Qualification de production')).toBeInTheDocument()
    expect(screen.getByText('Incomplète')).toBeInTheDocument()

    // Never this, while production qualification is incomplete.
    expect(screen.queryByText('Qualification réussie')).not.toBeInTheDocument()
    expect(screen.queryByText('Qualifiée')).not.toBeInTheDocument()
  })

  it('CQ17 names the capability that remains to be demonstrated, in French', async () => {
    mount(SUITE_PASSED_INCOMPLETE)
    expect(await screen.findByText(/Capacité restant à démontrer/)).toBeInTheDocument()
    expect(
      screen.getByText(/Réception des ordres — déclarée, non encore exercée/),
    ).toBeInTheDocument()
    // Not worded as a failure, and not a raw enum: nothing failed, and the gap is DHealth's own
    // missing transport rather than anything the partner has to fix.
    expect(screen.queryByText(/ORDER_RECEIPT/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Réception des ordres — non satisfaite/)).not.toBeInTheDocument()
  })

  it('CQ18 does not give a declared capability the success treatment', async () => {
    // §14. Three outcomes need three words, and the declared one must not carry the class the
    // exercised ones do -- otherwise a glance down the column reads as five successes.
    mount(SUITE_PASSED_INCOMPLETE)
    await screen.findByText('openelis-fhir 0.1')

    const declared = screen.getByRole('row', { name: /Réception des ordres/ })
    const exercised = screen.getByRole('row', { name: /Publication des résultats libérés/ })

    expect(within(declared).getByText('Déclarée — non encore exercée')).toBeInTheDocument()
    expect(within(exercised).getByText('Exercée avec succès')).toBeInTheDocument()

    const declaredCell = within(declared).getByText('Déclarée — non encore exercée')
    const exercisedCell = within(exercised).getByText('Exercée avec succès')
    expect(declaredCell.className).not.toEqual(exercisedCell.className)
  })

  it('CQ19 has no accessibility violations in the incomplete state', async () => {
    const { container } = render(
      <ConnectorQualification
        organizationId="org-lab"
        client={client(SUITE_PASSED_INCOMPLETE)}
        onChanged={vi.fn()}
      />,
    )
    await screen.findByText('Incomplète')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('CQ20 shows the derived runtime state and the observations behind it', async () => {
    mount(HEALTHY_RUNTIME)
    expect(await screen.findByText('Sain')).toBeInTheDocument()
    expect(screen.getByText(/version 0\.1\.0/)).toBeInTheDocument()
    expect(screen.getByText(/heure de réception DHealth/i)).toBeInTheDocument()
  })

  it('CQ21 labels the heartbeat time as DHealth\u2019s own, not the agent\u2019s', async () => {
    // §14. An agent with a skewed clock must not be able to look recent, and an operator comparing
    // two facilities needs one clock rather than each partner's.
    mount(HEALTHY_RUNTIME)
    expect(await screen.findByText(/heure de réception DHealth/i)).toBeInTheDocument()
  })

  it('CQ22 reports a degraded runtime as not in service, with its reasons', async () => {
    mount(DEGRADED_RUNTIME)
    expect(
      await screen.findByText(/L'agent ne joint pas le système source\./),
    ).toBeInTheDocument()
    // The definition row, not a sentence: DEGRADED is deliberately not operational (§63).
    expect(screen.getByText('En service').nextElementSibling?.textContent).toBe('Non')
    expect(
      screen.getByText(/1 message\(s\) non livrable\(s\) en attente de décision\./),
    ).toBeInTheDocument()
  })

  it('CQ23 offers no control that sets health', async () => {
    // §12/§81. The whole reason health is derived. A button here would be pressed at 2 a.m. to make
    // an alarm stop, and the facility it unblocked would take clinical work through a connector
    // nobody has heard from.
    mount(DEGRADED_RUNTIME)
    await screen.findByText(/L'agent ne joint pas le système source\./)
    for (const name of [/marquer comme sain/i, /forcer/i, /réinitialiser l'état/i]) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
  })

  it('CQ24 a healthy agent does not make an unqualified connector qualified', async () => {
    // §16, on screen. The agent is reporting in perfectly and the connector has never passed the
    // suite; both sentences must be present and neither may soften the other.
    mount(HEALTHY_RUNTIME)
    expect(await screen.findByText('Sain')).toBeInTheDocument()
    // Several elements mention production qualification; what matters is that none of them says
    // this connector has it. getAllByText rather than getByText -- a test that fails because a
    // phrase appears twice is a test asserting layout, not meaning.
    expect(screen.getAllByText(/Qualification de production/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/connecteur qualifié pour cette configuration/i)).toBeNull()
  })
})
