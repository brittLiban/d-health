import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ExchangeApiError,
  isDefinitiveExchangeError,
  mapExchangeErrorToCode,
} from '../../../api/exchangeClient'
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
import {
  clearStoredPendingPatient,
  getStoredPendingPatient,
  setStoredPendingPatient,
  type OpaquePendingPatientIdentity,
} from '../pendingPatientStorage'
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
  const [displayNameError, setDisplayNameError] = useState<string | null>(null)
  const [createState, setCreateState] = useState<'idle' | 'submitting' | 'uncertain'>('idle')
  const [createErrorCode, setCreateErrorCode] = useState<ErrorCode>('SERVER_ERROR')
  const [definitiveError, setDefinitiveError] = useState<string | null>(null)
  const [unresolvedStoredAttempt, setUnresolvedStoredAttempt] = useState<OpaquePendingPatientIdentity | null>(null)

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
    setDisplayNameError(null)
    setDefinitiveError(null)
    setUnresolvedStoredAttempt(null)

    // 1. In-memory pending attempt from tab switching within the same React session
    const inMemoryPending = scopeKey ? pendingCreations[scopeKey] : undefined
    setDisplayName(String(inMemoryPending?.body.display_name ?? ''))
    setContactValue(String(inMemoryPending?.body.contact_value ?? ''))
    setShowCreate(Boolean(inMemoryPending))
    setCreateState(inMemoryPending ? 'uncertain' : 'idle')

    // 2. Cross-reload recovery: check browser storage for unresolved opaque identity
    if (user && providerId) {
      const stored = getStoredPendingPatient(user.identity_id, providerId)
      if (stored) {
        // Attempt server reconciliation in case the server committed before response drop
        void (async () => {
          try {
            const matches = await client.listPatientReferences(providerId, {
              query: stored.source_identity_value,
              limit: 10,
              token: authToken ?? undefined,
            })
            const found = matches.find(
              (p) => p.local_reference === stored.source_identity_value,
            )
            if (
              mountedRef.current &&
              currentProviderRef.current === providerId &&
              found
            ) {
              clearStoredPendingPatient(user.identity_id, providerId)
              if (scopeKey) onPendingCreationChange(scopeKey, null)
              setSelected({ providerId, value: found })
              setResults((curr) => ({
                providerId,
                values:
                  curr?.providerId === providerId
                    ? [found, ...curr.values.filter((x) => x.patient_reference_id !== found.patient_reference_id)]
                    : [found],
              }))
              setLoadState('success')
              return
            }
          } catch {
            // Ignore reconciliation failure and treat as unresolved attempt
          }

          if (mountedRef.current && currentProviderRef.current === providerId) {
            setUnresolvedStoredAttempt(stored)
          }
        })()
      }
    }

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

  const handleDiscardStoredAttempt = () => {
    if (user && providerId) {
      clearStoredPendingPatient(user.identity_id, providerId)
    }
    if (scopeKey) {
      onPendingCreationChange(scopeKey, null)
    }
    setUnresolvedStoredAttempt(null)
    setCreateState('idle')
    setShowCreate(false)
    setDisplayName('')
    setContactValue('')
    setDisplayNameError(null)
    setDefinitiveError(null)
  }

  const handleResumeStoredAttempt = () => {
    setShowCreate(true)
    setCreateState('idle')
    setDefinitiveError(null)
  }

  const handleCancelCreate = () => {
    setShowCreate(false)
    setDisplayName('')
    setContactValue('')
    setDisplayNameError(null)
    setDefinitiveError(null)
    setCreateState('idle')
    handleDiscardStoredAttempt()
  }

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault()
    if (!providerId || !scopeKey || !user) return

    const trimmedName = displayName.trim()
    if (!trimmedName) {
      setDisplayNameError('Le nom affiché est obligatoire.')
      return
    }
    setDisplayNameError(null)
    setDefinitiveError(null)

    const existingPending = pendingCreations[scopeKey]
    const identityValue =
      existingPending?.body.source_identity_value ??
      unresolvedStoredAttempt?.source_identity_value ??
      globalThis.crypto.randomUUID()

    const body: PatientReferenceBindRequest = {
      source_system: NATIVE_PATIENT_SOURCE_SYSTEM,
      source_identity_system: NATIVE_PATIENT_IDENTITY_SYSTEM,
      source_identity_value: identityValue,
      display_name: trimmedName,
      contact_channel_type: contactValue.trim() ? 'PHONE' : null,
      contact_value: contactValue.trim() || null,
    }

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

      // Clear pending state on success
      clearStoredPendingPatient(user.identity_id, targetProviderId)
      onPendingCreationChange(scopeKey, null)
      setUnresolvedStoredAttempt(null)

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
    } catch (error) {
      if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return

      if (isDefinitiveExchangeError(error)) {
        // Definitive failure (400, 401, 403, 404, 409, 422):
        // Server definitively refused request. Clear pending token and do NOT prompt for duplicate-safe reconciliation.
        clearStoredPendingPatient(user.identity_id, targetProviderId)
        onPendingCreationChange(scopeKey, null)
        setUnresolvedStoredAttempt(null)
        setCreateErrorCode(safeErrorCode(error))
        setDefinitiveError("Impossible d'enregistrer le patient. Veuillez vérifier les informations et vos autorisations.")
        setCreateState('idle')
      } else {
        // Ambiguous failure (network drop, 5xx):
        // Retain opaque identity tuple across reload and in memory for safe retry.
        setStoredPendingPatient(user.identity_id, targetProviderId, {
          source_system: NATIVE_PATIENT_SOURCE_SYSTEM,
          source_identity_system: NATIVE_PATIENT_IDENTITY_SYSTEM,
          source_identity_value: identityValue,
        })
        onPendingCreationChange(scopeKey, { body })
        setCreateErrorCode(safeErrorCode(error))
        setCreateState('uncertain')
      }
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

      {unresolvedStoredAttempt && !showCreate ? (
        <Card>
          <div className={styles.alertBanner} role="alert">
            <p>
              <strong>Tentative non confirmée :</strong> Une tentative précédente de création n'a pas pu être confirmée sur le serveur. Aucun patient n'a été créé.
            </p>
            <div className={styles.actions}>
              <Button type="button" variant="secondary" onClick={handleDiscardStoredAttempt}>
                Abandonner la tentative
              </Button>
              <Button type="button" onClick={handleResumeStoredAttempt}>
                Reprendre
              </Button>
            </div>
          </div>
        </Card>
      ) : null}

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
            <div>
              <TextInput
                label="Nom affiché (obligatoire)"
                value={displayName}
                maxLength={256}
                disabled={createState !== 'idle'}
                onChange={(event) => {
                  setDisplayName(event.target.value)
                  if (displayNameError) setDisplayNameError(null)
                }}
              />
              {displayNameError ? (
                <p role="alert" className={styles.errorText}>
                  {displayNameError}
                </p>
              ) : null}
            </div>
            <TextInput
              label="Téléphone (facultatif)"
              type="tel"
              value={contactValue}
              maxLength={256}
              disabled={createState !== 'idle'}
              onChange={(event) => setContactValue(event.target.value)}
            />
            {definitiveError ? (
              <div className={styles.createError}>
                <ErrorState code={createErrorCode} />
                <p className={styles.errorText}>{definitiveError}</p>
              </div>
            ) : null}
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
              <Button type="button" variant="secondary" onClick={handleCancelCreate}>
                Annuler
              </Button>
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
