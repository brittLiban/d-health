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
  canReadPendingPatientStorage,
  PendingPatientStorageReadError,
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
  const [confirmedReadAttempt, setConfirmedReadAttempt] = useState<OpaquePendingPatientIdentity | null>(null)
  const [reconcilingStoredAttempt, setReconcilingStoredAttempt] = useState(false)
  const [storageUnreadable, setStorageUnreadable] = useState(false)

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
    setConfirmedReadAttempt(null)
    setReconcilingStoredAttempt(false)
    setStorageUnreadable(false)

    // 1. In-memory pending attempt from tab switching within the same React session
    const inMemoryPending = scopeKey ? pendingCreations[scopeKey] : undefined
    setDisplayName(String(inMemoryPending?.body.display_name ?? ''))
    setContactValue(String(inMemoryPending?.body.contact_value ?? ''))
    setShowCreate(Boolean(inMemoryPending && !inMemoryPending.confirmedPatientReferenceId))
    setCreateState(inMemoryPending && !inMemoryPending.confirmedPatientReferenceId ? 'uncertain' : 'idle')
    if (inMemoryPending?.confirmedPatientReferenceId) {
      setConfirmedReadAttempt({
        source_system: inMemoryPending.body.source_system,
        source_identity_system: inMemoryPending.body.source_identity_system,
        source_identity_value: inMemoryPending.body.source_identity_value,
        confirmed_patient_reference_id: inMemoryPending.confirmedPatientReferenceId,
        created_at: Date.now(),
      })
    }

    // 2. Cross-reload recovery: check browser storage for unresolved opaque identity
    if (user && providerId) {
      if (!canReadPendingPatientStorage()) {
        setStorageUnreadable(true)
        setDefinitiveError("Impossible de verifier la recuperation sure d'une creation precedente.")
      }
      let stored: OpaquePendingPatientIdentity | null = null
      try {
        stored = getStoredPendingPatient(user.identity_id, providerId)
      } catch (error) {
        if (error instanceof PendingPatientStorageReadError) {
          setStorageUnreadable(true)
          setDefinitiveError("Impossible de verifier la recuperation sure d'une creation precedente.")
          setShowCreate(false)
        } else throw error
      }
      if (stored) {
        // Block new creation immediately. A stored UUID may identify a POST which the server
        // committed before its response was lost, so it must be reconciled exactly, never replaced.
        setReconcilingStoredAttempt(true)
        if (!stored.confirmed_patient_reference_id) setUnresolvedStoredAttempt(stored)
        void (async () => {
          let found: ClinicPatientSummary | null = null
          try {
            if (stored.confirmed_patient_reference_id) {
              found = await client.getPatientReference(
                providerId,
                stored.confirmed_patient_reference_id,
                authToken ?? undefined,
              )
            }
          } catch {
            // A read failure cannot undo a confirmed bind POST.
          }

          if (mountedRef.current && currentProviderRef.current === providerId) {
            if (found) {
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
            } else if (stored.confirmed_patient_reference_id) setConfirmedReadAttempt(stored)
            else setUnresolvedStoredAttempt(stored)
            setReconcilingStoredAttempt(false)
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

  const handleResumeStoredAttempt = () => {
    setShowCreate(true)
    setCreateState('idle')
    setDefinitiveError(null)
  }

  const handleCancelCreate = () => {
    setShowCreate(false)
    // Cancellation is presentation-only if a create is unresolved. The durable
    // source identity remains available for exact reconciliation.
    if (!unresolvedStoredAttempt && !pendingCreations[scopeKey ?? '']) {
      setDisplayName('')
      setContactValue('')
      setDisplayNameError(null)
      setDefinitiveError(null)
      setCreateState('idle')
    }
  }

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault()
    if (!providerId || !scopeKey || !user || storageUnreadable) return

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
    const hadEarlierAmbiguousAttempt = Boolean(existingPending || unresolvedStoredAttempt)
    // Durability is a precondition of issuing a mutation whose response may be lost.
    if (!setStoredPendingPatient(user.identity_id, targetProviderId, {
      source_system: NATIVE_PATIENT_SOURCE_SYSTEM,
      source_identity_system: NATIVE_PATIENT_IDENTITY_SYSTEM,
      source_identity_value: identityValue,
    })) {
      setCreateErrorCode('SERVER_ERROR')
      setDefinitiveError("Impossible de preparer la recuperation sure de cette creation. Aucun envoi n'a ete effectue.")
      return
    }
    onPendingCreationChange(scopeKey, { body })
    setCreateState('submitting')
    let outcome
    try {
      outcome = await client.bindPatientReferenceWithOutcome(
        targetProviderId,
        body,
        authToken ?? undefined,
      )
    } catch (error) {
      if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return

      if (isDefinitiveExchangeError(error) && !hadEarlierAmbiguousAttempt) {
        // This specific first POST was authoritatively refused before any ambiguity existed.
        clearStoredPendingPatient(user.identity_id, targetProviderId)
        onPendingCreationChange(scopeKey, null)
        setUnresolvedStoredAttempt(null)
        setCreateErrorCode(safeErrorCode(error))
        setDefinitiveError("Impossible d'enregistrer le patient. Veuillez vérifier les informations et vos autorisations.")
        setCreateState('idle')
      } else {
        // A later retry refusal cannot prove that an earlier ambiguous POST did not commit.
        onPendingCreationChange(scopeKey, { body })
        setCreateErrorCode(safeErrorCode(error))
        setCreateState('uncertain')
      }
      return
    }

    // Phase A is now authoritatively confirmed. Persist the returned reference before
    // Phase B so a failed read, reload, or navigation cannot turn it into a fresh create.
    const confirmed = {
      source_system: NATIVE_PATIENT_SOURCE_SYSTEM,
      source_identity_system: NATIVE_PATIENT_IDENTITY_SYSTEM,
      source_identity_value: identityValue,
      confirmed_patient_reference_id: outcome.reference.id,
    }
    if (!setStoredPendingPatient(user.identity_id, targetProviderId, confirmed)) {
      // The mutation is confirmed; retain it in memory and avoid any second create.
      onPendingCreationChange(scopeKey, { body, confirmedPatientReferenceId: outcome.reference.id })
      if (mountedRef.current && currentProviderRef.current === targetProviderId) {
        setConfirmedReadAttempt({ ...confirmed, created_at: Date.now() })
        setCreateErrorCode('SERVER_ERROR')
        setCreateState('idle')
        setShowCreate(false)
      }
      return
    }
    onPendingCreationChange(scopeKey, null)
    // The authoritative record is durable for targetProviderId. Never attach it to
    // whatever provider the clinician may have selected while the POST was in flight.
    if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return
    // Phase B is a read only. Gate creation before awaiting it so cancel/navigation
    // cannot mint another source identity while this confirmed reference is loading.
    setConfirmedReadAttempt({ ...confirmed, created_at: Date.now() })
    setShowCreate(false)
    try {
      const authoritative = await client.getPatientReference(
        targetProviderId,
        outcome.reference.id,
        authToken ?? undefined,
      )
      if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return
      clearStoredPendingPatient(user.identity_id, targetProviderId)
      setUnresolvedStoredAttempt(null)
      setConfirmedReadAttempt(null)
      setResults((current) => ({
        providerId: targetProviderId,
        values: [authoritative, ...(current?.providerId === targetProviderId
          ? current.values.filter((patient) => patient.patient_reference_id !== authoritative.patient_reference_id)
          : [])],
      }))
      setSelected({ providerId: targetProviderId, value: authoritative })
      setLoadState('success')
      setCreateState('idle')
      setShowCreate(false)
    } catch (error) {
      if (!mountedRef.current || currentProviderRef.current !== targetProviderId) return
      setCreateErrorCode(safeErrorCode(error))
      setCreateState('idle')
      setShowCreate(false)
    }
  }

  const handleRetryConfirmedRead = async () => {
    if (!providerId || !user || !confirmedReadAttempt?.confirmed_patient_reference_id) return
    try {
      const authoritative = await client.getPatientReference(
        providerId,
        confirmedReadAttempt.confirmed_patient_reference_id,
        authToken ?? undefined,
      )
      if (!mountedRef.current || currentProviderRef.current !== providerId) return
      clearStoredPendingPatient(user.identity_id, providerId)
      setConfirmedReadAttempt(null)
      setUnresolvedStoredAttempt(null)
      if (scopeKey) onPendingCreationChange(scopeKey, null)
      setSelected({ providerId, value: authoritative })
    } catch (error) {
      if (mountedRef.current && currentProviderRef.current === providerId) {
        setCreateErrorCode(safeErrorCode(error))
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

      {storageUnreadable ? (
        <Card><div className={styles.createError}><ErrorState code="SERVER_ERROR" /><p className={styles.errorText}>Impossible de verifier la recuperation sure d'une creation precedente.</p></div></Card>
      ) : null}

      {confirmedReadAttempt ? (
        <Card>
          <div className={styles.alertBanner} role="alert">
            <p><strong>Patient créé :</strong> La création est confirmée, mais la fiche patient ne peut pas être ouverte pour le moment.</p>
            <Button type="button" onClick={() => void handleRetryConfirmedRead()}>Réessayer l’ouverture</Button>
          </div>
        </Card>
      ) : unresolvedStoredAttempt && !showCreate ? (
        <Card>
          <div className={styles.alertBanner} role="alert">
            <p>
              <strong>Tentative non confirmée :</strong> La création du patient n’a pas pu être confirmée. Vérifiez la tentative existante avant de créer un nouveau patient.
            </p>
            <div className={styles.actions}>
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
              <Button type="submit" disabled={createState === 'submitting' || storageUnreadable}>
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
          action={<Button type="button" disabled={Boolean(unresolvedStoredAttempt || confirmedReadAttempt || reconcilingStoredAttempt || storageUnreadable)} onClick={() => setShowCreate(true)}>Nouveau patient</Button>}
        />
      ) : (
        <section aria-label="Résultats de recherche" className={styles.results}>
          <div className={styles.resultsHeader}>
            <h2>Patients trouvés</h2>
            <Button type="button" variant="secondary" disabled={Boolean(unresolvedStoredAttempt || confirmedReadAttempt || reconcilingStoredAttempt || storageUnreadable)} onClick={() => setShowCreate(true)}>
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
