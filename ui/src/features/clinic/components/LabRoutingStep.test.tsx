import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ExchangeClient } from '../../../api/exchangeClient'
import { ExchangeApiError } from '../../../api/exchangeClient'
import type {
  LabAssignmentResponse,
  LabOrder,
  LabOrderItem,
  PatientIdentitySummary,
  ProviderEligibleLab,
} from '../../../api/exchangeTypes'
import { LabRoutingStep } from './LabRoutingStep'

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function makePatient(overrides: Partial<PatientIdentitySummary> = {}): PatientIdentitySummary {
  return {
    patient_id: '11111111-1111-4111-8111-111111111111',
    display_name: 'Fatouma Robleh',
    date_of_birth: '1988-06-02',
    contact_channel_type: 'PHONE',
    contact_value: '+25377100001',
    ...overrides,
  }
}

function makeItems(): LabOrderItem[] {
  return [
    {
      catalog_entry_id: 'entry-1',
      catalog_code_snapshot: 'GLU',
      catalog_display_name_snapshot: 'Glycémie à jeun',
    },
  ]
}

function makeEligibleLab(overrides: Partial<ProviderEligibleLab> = {}): ProviderEligibleLab {
  return {
    lab_organization_id: 'lab-1',
    lab_organization_name: 'Laboratoire Central',
    public_address: 'Route de Venise, Djibouti',
    public_phone: '+25321350000',
    ...overrides,
  }
}

function makeOrder(overrides: Partial<LabOrder> = {}): LabOrder {
  return {
    id: 'order-1',
    provider_organization_id: 'provider-1',
    patient_reference_id: 'ref-1',
    practitioner_reference_id: 'prac-1',
    patient: makePatient(),
    status: 'SELECTED',
    created_at: '2026-09-18T08:00:00Z',
    items: makeItems(),
    lab_assignment: {
      lab_organization_id: 'lab-1',
      lab_organization_name: 'Laboratoire Central',
      selected_at: '2026-09-18T08:05:00Z',
    },
    ...overrides,
  }
}

function makeClient(overrides: Partial<ExchangeClient> = {}): ExchangeClient {
  return {
    getEligibleLabs: vi.fn().mockResolvedValue([makeEligibleLab()]),
    assignLab: vi.fn().mockResolvedValue({
      lab_order_id: 'order-1',
      lab_organization_id: 'lab-1',
      selected_at: '2026-09-18T08:05:00Z',
    } satisfies LabAssignmentResponse),
    getOrder: vi.fn().mockResolvedValue(makeOrder()),
    ...overrides,
  } as unknown as ExchangeClient
}

describe('LabRoutingStep', () => {
  it('shows patient identity and ordered tests alongside the lab choice (§38)', async () => {
    const client = makeClient()
    render(
      <LabRoutingStep
        client={client}
        providerId="provider-1"
        orderId="order-1"
        patient={makePatient()}
        items={makeItems()}
        onAssigned={vi.fn()}
      />,
    )

    expect(await screen.findByText('Fatouma Robleh')).toBeInTheDocument()
    expect(screen.getByText(/Glycémie à jeun/)).toBeInTheDocument()
  })

  it('renders the eligible lab list with name/address/phone only, no price/ranking of any kind (§32)', async () => {
    const client = makeClient()
    render(
      <LabRoutingStep
        client={client}
        providerId="provider-1"
        orderId="order-1"
        patient={makePatient()}
        items={makeItems()}
        onAssigned={vi.fn()}
      />,
    )

    expect(await screen.findByText('Laboratoire Central')).toBeInTheDocument()
    expect(screen.getByText('Route de Venise, Djibouti')).toBeInTheDocument()
    expect(screen.getByText('+25321350000')).toBeInTheDocument()
    expect(screen.queryByText(/DJF/)).not.toBeInTheDocument()
    expect(screen.queryByText(/prix|meilleur|moins cher|recommandé/i)).not.toBeInTheDocument()
  })

  it('§15: zero eligible labs shows the exact truthful message, never a fabricated fallback', async () => {
    const client = makeClient({ getEligibleLabs: vi.fn().mockResolvedValue([]) })
    render(
      <LabRoutingStep
        client={client}
        providerId="provider-1"
        orderId="order-1"
        patient={makePatient()}
        items={makeItems()}
        onAssigned={vi.fn()}
      />,
    )

    expect(
      await screen.findByText(
        'Aucun laboratoire configuré ne peut actuellement réaliser toutes les analyses de cet ordre.',
      ),
    ).toBeInTheDocument()
  })

  it('a load failure shows a retry action that re-fetches', async () => {
    const getEligibleLabs = vi
      .fn()
      .mockRejectedValueOnce(new ExchangeApiError(0, 'NETWORK_ERROR', 'down'))
      .mockResolvedValueOnce([makeEligibleLab()])
    const client = makeClient({ getEligibleLabs })
    const user = userEvent.setup()
    render(
      <LabRoutingStep
        client={client}
        providerId="provider-1"
        orderId="order-1"
        patient={makePatient()}
        items={makeItems()}
        onAssigned={vi.fn()}
      />,
    )

    expect(
      await screen.findByText('Impossible de charger les laboratoires disponibles pour cet ordre.'),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Réessayer' }))
    expect(await screen.findByText('Laboratoire Central')).toBeInTheDocument()
    expect(getEligibleLabs).toHaveBeenCalledTimes(2)
  })

  it('§33: confirmation is explicit -- the button stays disabled until a lab is selected, even with exactly one option', async () => {
    const client = makeClient()
    render(
      <LabRoutingStep
        client={client}
        providerId="provider-1"
        orderId="order-1"
        patient={makePatient()}
        items={makeItems()}
        onAssigned={vi.fn()}
      />,
    )

    const confirmButton = await screen.findByRole('button', {
      name: "Confirmer l'envoi au laboratoire",
    })
    expect(confirmButton).toBeDisabled()
  })

  it('assigns the selected lab and reports the authoritative, named assignment from a fresh order read, never from the assign response (§30)', async () => {
    const onAssigned = vi.fn()
    const assignLab = vi.fn().mockResolvedValue({
      lab_order_id: 'order-1',
      lab_organization_id: 'lab-1',
      selected_at: '2026-09-18T08:05:00Z',
    } satisfies LabAssignmentResponse)
    const getOrder = vi.fn().mockResolvedValue(makeOrder())
    const client = makeClient({ assignLab, getOrder })
    const user = userEvent.setup()
    render(
      <LabRoutingStep
        client={client}
        providerId="provider-1"
        orderId="order-1"
        patient={makePatient()}
        items={makeItems()}
        onAssigned={onAssigned}
      />,
    )

    await screen.findByText('Laboratoire Central')
    await user.click(screen.getByRole('radio'))
    await user.click(screen.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }))

    await waitFor(() => expect(onAssigned).toHaveBeenCalledTimes(1))
    expect(assignLab).toHaveBeenCalledWith(
      'provider-1',
      'order-1',
      { lab_organization_id: 'lab-1' },
      expect.anything(),
    )
    expect(getOrder).toHaveBeenCalledWith('provider-1', 'order-1')
    expect(onAssigned).toHaveBeenCalledWith(
      expect.objectContaining({ lab_organization_name: 'Laboratoire Central' }),
    )
  })

  it('a 409 conflict shows a truthful message and refreshes the eligible-labs list (§19)', async () => {
    const assignLab = vi
      .fn()
      .mockRejectedValue(new ExchangeApiError(409, 'CONFLICT', 'route inactive'))
    const getEligibleLabs = vi
      .fn()
      .mockResolvedValueOnce([makeEligibleLab()])
      .mockResolvedValueOnce([])
    const client = makeClient({ assignLab, getEligibleLabs })
    const user = userEvent.setup()
    render(
      <LabRoutingStep
        client={client}
        providerId="provider-1"
        orderId="order-1"
        patient={makePatient()}
        items={makeItems()}
        onAssigned={vi.fn()}
      />,
    )

    await screen.findByText('Laboratoire Central')
    await user.click(screen.getByRole('radio'))
    await user.click(screen.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }))

    expect(
      await screen.findByText(
        "Ce laboratoire n'est plus disponible pour cet ordre. La liste a été actualisée -- veuillez choisir à nouveau.",
      ),
    ).toBeInTheDocument()
    await waitFor(() => expect(getEligibleLabs).toHaveBeenCalledTimes(2))
    expect(
      await screen.findByText(
        'Aucun laboratoire configuré ne peut actuellement réaliser toutes les analyses de cet ordre.',
      ),
    ).toBeInTheDocument()
  })

  it('a non-conflict assign failure shows a generic truthful error without discarding the lab list', async () => {
    const assignLab = vi
      .fn()
      .mockRejectedValue(new ExchangeApiError(0, 'NETWORK_ERROR', 'down'))
    const client = makeClient({ assignLab })
    const user = userEvent.setup()
    render(
      <LabRoutingStep
        client={client}
        providerId="provider-1"
        orderId="order-1"
        patient={makePatient()}
        items={makeItems()}
        onAssigned={vi.fn()}
      />,
    )

    await screen.findByText('Laboratoire Central')
    await user.click(screen.getByRole('radio'))
    await user.click(screen.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }))

    expect(
      await screen.findByText("Impossible d'envoyer cet ordre au laboratoire sélectionné."),
    ).toBeInTheDocument()
    expect(screen.getByText('Laboratoire Central')).toBeInTheDocument()
  })

  it('SR: a late eligible-labs response for a stale order id/provider id never renders', async () => {
    const first = deferred<ProviderEligibleLab[]>()
    const getEligibleLabs = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValueOnce([
      makeEligibleLab({ lab_organization_id: 'lab-2', lab_organization_name: 'Autre Labo' }),
    ])
    const client = makeClient({ getEligibleLabs })

    const { rerender } = render(
      <LabRoutingStep
        client={client}
        providerId="provider-1"
        orderId="order-1"
        patient={makePatient()}
        items={makeItems()}
        onAssigned={vi.fn()}
      />,
    )

    rerender(
      <LabRoutingStep
        client={client}
        providerId="provider-1"
        orderId="order-2"
        patient={makePatient()}
        items={makeItems()}
        onAssigned={vi.fn()}
      />,
    )

    expect(await screen.findByText('Autre Labo')).toBeInTheDocument()

    first.resolve([makeEligibleLab({ lab_organization_id: 'lab-1' })])
    await new Promise((r) => setTimeout(r, 0))

    expect(screen.queryByText('Laboratoire Central')).not.toBeInTheDocument()
    expect(screen.getByText('Autre Labo')).toBeInTheDocument()
  })
})
