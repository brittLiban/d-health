import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { ExchangeApiError, mapExchangeErrorToCode } from '../../../api/exchangeClient'
import type { ClinicPatientSummary, PatientReferenceBindRequest } from '../../../api/exchangeTypes'
import type { ErrorCode } from '../../../api/types'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  SearchInput,
  TextInput,
} from '../../../design-system'
import { useClinic } from '../ClinicContext'
import type { PendingPatientCreation } from '../ClinicApp'
import styles from './ClinicPatientsPage.module.css'

const NATIVE_PATIENT_SOURCE_SYSTEM = 'dhealth-clinic'
const NATIVE_PATIENT_IDENTITY_SYSTEM = 'urn:dhealth:clinic:patient-reference-id'

interface ClinicPatientsPageProps {
  pendingCreations: Record<string, PendingPatientCreation>
  onPendingCreationChange: (scopeKey: string, pending: PendingPatientCreation | null) => void
}

function patientLabel(patient: ClinicPatientSummary): string {
  return patient.display_name?.trim() || patient.local_reference
}

function safeErrorCode(error: unknown): ErrorCode {
  return error instanceof ExchangeApiError ? mapExchangeErrorToCode(error.code) : 'SERVER_ERROR'
}

export function ClinicPatientsPage({
  pendingCreations,
  onPendingCreationChange,
}: ClinicPatientsPageProps) {
  const { activeProvider, user, client, authToken } = useClinic()
  const providerId = activeProvider?.organization_id ?? null
  const scopeKey = providerId && user ? `${user.identity_id}:${providerId}` : null
  const currentProviderRef = useRef(providerId)
  const mountedRef = useRef(true)
  const requestGenerationRef = useRef(0)
  currentProviderRef.current = providerId

  const [searchInput, setSearchInput] = useState('')
  const [results, setResults] = useState<{ providerId: string; values: ClinicPatientSummary[] } | null>(null)
  const [loadState, setLoadState] = useState<'loading' | 'success' | 'error'>('loading')
  const [loadErrorCode, setLoadErrorCode] = useState<ErrorCode>('SERVER_ERROR')
  const [selected, setSelected] = useState<{ providerId: string; value: ClinicPatientSummary } | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [displayName, setDisplayName] = useState('')
  const [contactValue, setContactValue] = useState('')
  const [createState, setCreateState] = useState<'idle' | 'submitting' | 'uncertain'>('idle')
  const [createErrorCode, setCreateErrorCode] = useState<ErrorCode>('SERVER_ERROR')

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const loadPatients = useCallback(async (targetProviderId: string, query = '') => {
    const generation = ++requestGenerationRef.current
    setLoadState('loading')
    try {
      const values = await client.listPatientReferences(targetProviderId, {
        query,
        limit: 50,
        token: authToken ?? undefined,
      })
      if (
        mountedRef.current &&
        currentProviderRef.current === targetProviderId &&
        requestGenerationRef.current === generation
      ) {
        setResults({ providerId: targetProviderId, values })
        setLoadState('success')
      }
    } catch (error) {
      if (
        mountedRef.current &&
        currentProviderRef.current === targetProviderId &&
        requestGenerationRef.current === generation
      ) {
        setResults({ providerId: targetProviderId, values: [] })
        setLoadErrorCode(safeErrorCode(error))
        setLoadState('error')
      }
    }
  }, [authToken, client])

  useEffect(() => {
    requestGenerationRef.current += 1
    setSearchInput('')
    setResults(null)
    setSelected(null)
    setLoadState('loading')

    const pending = scopeKey ? pendingCreations[scopeKey] : undefined
    setDisplayName(String(pending?.body.display_name ?? ''))
    setContactValue(String(pending?.body.contact_value ?? ''))
    setShowCreate(Boolean(pending))
    setCreateState(pending ? 'uncertain' : 'idle')

    if (providerId) {
      void loadPatients(providerId)
    }
    // Pending attempts are intentionally read only when the authenticated provider scope changes.
    // Updating the attempt itself must not restart the page or replace an in-flight request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId, scopeKey, loadPatients])

  const visibleResults = results?.providerId === providerId ? results.values : []
  const selectedPatient = selected?.providerId === providerId ? selected.value : null

  const handleSearch = (event: FormEvent) => {
    event.preventDefault()
    if (providerId) void loadPatients(providerId, searchInput.trim())
  }

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault()
    if (!providerId || !scopeKey || !user) return

    const existingPending = pendingCreations[scopeKey]
    const body: PatientReferenceBindRequest = existingPending?.body ?? {
      source_system: NATIVE_PATIENT_SOURCE_SYSTEM,
      source_identity_system: NATIVE_PATIENT_IDENTITY_SYSTEM,
      source_identity_value: globalThis.crypto.randomUUID(),
      display_name: displayName.trim() || null,
      contact_channel_type: contactValue.trim() ? 'PHONE' : null,
      contact_value: contactValue.trim() || null,
    }
    if (!existingPending) onPendingCreationChange(scopeKey, { body })

    const targetProviderId = providerId
    setCreateState('submitting')
    try {
      const outcome = await client.bindPatientReferenceWithOutcome(
        targetProviderId,
        body,
        authToken ?? undefined,
      )
      const authoritative = await client.getPatientReference(
        targetProviderId,
        outcome.reference.id,
        authToken ?? undefined,
      )
      if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return

      setResults((current) => {
        const values = current?.providerId === targetProviderId ? current.values : []
        return {
          providerId: targetProviderId,
          values: [
            authoritative,
            ...values.filter(
              (patient) => patient.patient_reference_id !== authoritative.patient_reference_id,
            ),
          ],
        }
      })
      setSelected({ providerId: targetProviderId, value: authoritative })
      setLoadState('success')
      setCreateState('idle')
      setShowCreate(false)
      onPendingCreationChange(scopeKey, null)
    } catch (error) {
      if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return
      setCreateErrorCode(safeErrorCode(error))
      setCreateState('uncertain')
    }
  }

  if (!providerId || !scopeKey) {
    return <div className={styles.page}><LoadingSkeleton /></div>
  }

  if (selectedPatient) {
    return (
      <div className={styles.page}>
        <Button type="button" variant="secondary" onClick={() => setSelected(null)}>
          Retour aux patients
        </Button>
        <header className={styles.header}>
          <h1>Patients</h1>
          <p>{activeProvider?.organization_name}</p>
        </header>
        <Card>
          <section className={styles.workspace} aria-labelledby="patient-workspace-title">
            <h2 id="patient-workspace-title">{patientLabel(selectedPatient)}</h2>
            {selectedPatient.contact_value ? (
              <p><strong>Contact :</strong> {selectedPatient.contact_value}</p>
            ) : null}
            <p><strong>Référence patient :</strong> <code>{selectedPatient.patient_reference_id}</code></p>
            {!selectedPatient.display_name ? (
              <p><strong>Référence locale :</strong> <code>{selectedPatient.local_reference}</code></p>
            ) : null}
            <Button type="button" disabled title="Disponible dans DH-CLINIC-003">
              Nouvel ordre de laboratoire
            </Button>
            <p className={styles.hint}>La création d'ordres sera disponible dans une étape ultérieure.</p>
          </section>
        </Card>
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1>Patients</h1>
        <p>Établissement prescripteur : <strong>{activeProvider?.organization_name}</strong></p>
      </header>

      <Card>
        <form className={styles.searchForm} onSubmit={(event) => handleSearch(event)}>
          <SearchInput
            label="Rechercher un patient"
            value={searchInput}
            maxLength={100}
            onChange={(event) => setSearchInput(event.target.value)}
          />
          <Button type="submit" disabled={loadState === 'loading'}>Rechercher</Button>
        </form>
      </Card>

      {showCreate ? (
        <Card>
          <form className={styles.createForm} onSubmit={(event) => void handleCreate(event)}>
            <h2>Nouveau patient</h2>
            <TextInput
              label="Nom affiché (facultatif)"
              value={displayName}
              maxLength={256}
              disabled={createState !== 'idle'}
              onChange={(event) => setDisplayName(event.target.value)}
            />
            <TextInput
              label="Téléphone (facultatif)"
              type="tel"
              value={contactValue}
              maxLength={256}
              disabled={createState !== 'idle'}
              onChange={(event) => setContactValue(event.target.value)}
            />
            {createState === 'uncertain' ? (
              <div className={styles.createError}>
                <ErrorState code={createErrorCode} />
                <p>Réessayez pour vérifier la même référence sans créer un doublon.</p>
              </div>
            ) : null}
            <div className={styles.actions}>
              <Button type="submit" disabled={createState === 'submitting'}>
                {createState === 'submitting' ? 'Création…' : 'Créer et ouvrir'}
              </Button>
              {createState === 'idle' ? (
                <Button type="button" variant="secondary" onClick={() => setShowCreate(false)}>
                  Annuler
                </Button>
              ) : null}
            </div>
          </form>
        </Card>
      ) : null}

      {loadState === 'loading' ? (
        <LoadingSkeleton label="Chargement des patients…" />
      ) : loadState === 'error' ? (
        <ErrorState code={loadErrorCode} onRetry={() => void loadPatients(providerId, searchInput.trim())} />
      ) : visibleResults.length === 0 ? (
        <EmptyState
          title="Aucun patient trouvé"
          action={<Button type="button" onClick={() => setShowCreate(true)}>Nouveau patient</Button>}
        />
      ) : (
        <section aria-label="Résultats de recherche" className={styles.results}>
          <div className={styles.resultsHeader}>
            <h2>Patients trouvés</h2>
            <Button type="button" variant="secondary" onClick={() => setShowCreate(true)}>
              Nouveau patient
            </Button>
          </div>
          <ul className={styles.patientList}>
            {visibleResults.map((patient) => (
              <li key={patient.patient_reference_id}>
                <Card>
                  <div className={styles.patientRow}>
                    <div>
                      <h3>{patientLabel(patient)}</h3>
                      {patient.contact_value ? <p>Contact : {patient.contact_value}</p> : null}
                      <p>Référence locale : <code>{patient.local_reference}</code></p>
                    </div>
                    <Button type="button" onClick={() => setSelected({ providerId, value: patient })}>Ouvrir</Button>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
