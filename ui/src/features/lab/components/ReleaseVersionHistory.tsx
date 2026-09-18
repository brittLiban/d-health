import { useCallback, useEffect, useRef, useState } from 'react'
import type { ExchangeClient } from '../../../api/exchangeClient'
import type { LabReleaseVersionContent, LabReleaseVersionSummary } from '../../../api/exchangeTypes'
import { Button, LoadingSkeleton } from '../../../design-system'

const HISTORY_LOAD_ERROR = "Impossible de charger l'historique des versions."
const CONTENT_LOAD_ERROR = 'Impossible de charger le contenu de cette version.'

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString('fr-FR')
}

export interface ReleaseVersionHistoryProps {
  client: ExchangeClient
  labId: string
  orderId: string
}

/**
 * DH-LAB-005 §52-54: lab-side version history -- Version 1 is never "the release," it is one
 * immutable entry in a list that may grow. Status is always shown textually ("Amendement" vs.
 * "Version initiale"), never color-only. Each version's exact content is fetched by its own
 * immutable `release_version_id`, reconstructed from the manifest only -- opening Version 1's
 * content after Version 2/3 exist must show byte-for-byte what Version 1 actually released.
 */
export function ReleaseVersionHistory({ client, labId, orderId }: ReleaseVersionHistoryProps) {
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<{
    loading: boolean
    error: string | null
    versions: LabReleaseVersionSummary[] | null
  }>({ loading: false, error: null, versions: null })
  const [contentState, setContentState] = useState<{
    versionId: string | null
    loading: boolean
    error: string | null
    content: LabReleaseVersionContent | null
  }>({ versionId: null, loading: false, error: null, content: null })
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const load = useCallback(() => {
    setState({ loading: true, error: null, versions: null })
    client
      .getLabOrderReleaseVersions(labId, orderId)
      .then((history) => {
        if (!mountedRef.current) return
        setState({ loading: false, error: null, versions: history.versions })
      })
      .catch(() => {
        if (!mountedRef.current) return
        setState({ loading: false, error: HISTORY_LOAD_ERROR, versions: null })
      })
  }, [client, labId, orderId])

  function handleToggle() {
    if (open) {
      setOpen(false)
      return
    }
    setOpen(true)
    setContentState({ versionId: null, loading: false, error: null, content: null })
    load()
  }

  function handleOpenVersion(versionId: string) {
    if (contentState.versionId === versionId && contentState.content) {
      setContentState({ versionId: null, loading: false, error: null, content: null })
      return
    }
    setContentState({ versionId, loading: true, error: null, content: null })
    client
      .getLabOrderReleaseVersionContent(labId, orderId, versionId)
      .then((content) => {
        if (!mountedRef.current) return
        setContentState({ versionId, loading: false, error: null, content })
      })
      .catch(() => {
        if (!mountedRef.current) return
        setContentState({ versionId, loading: false, error: CONTENT_LOAD_ERROR, content: null })
      })
  }

  return (
    <div>
      <Button type="button" variant="secondary" onClick={handleToggle}>
        {open ? "Masquer l'historique des versions" : "Voir l'historique des versions"}
      </Button>
      {open ? (
        <div style={{ marginTop: 'var(--space-2)', display: 'grid', gap: 'var(--space-2)' }}>
          {state.loading ? (
            <LoadingSkeleton />
          ) : state.error ? (
            <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)', fontSize: 'var(--font-size-sm)' }}>
              {state.error}
            </p>
          ) : state.versions ? (
            <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: 'var(--space-2)' }}>
              {state.versions.map((version) => (
                <li key={version.release_version_id}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                      <strong>Version {version.version_number}</strong>
                      {' — '}
                      {version.is_amendment ? 'Amendement' : 'Version initiale'}
                      {' — '}
                      {formatDateTime(version.created_at)}
                    </p>
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() => handleOpenVersion(version.release_version_id)}
                    >
                      {contentState.versionId === version.release_version_id && contentState.content
                        ? 'Masquer le contenu'
                        : 'Voir le contenu exact'}
                    </Button>
                  </div>
                  {contentState.versionId === version.release_version_id ? (
                    contentState.loading ? (
                      <LoadingSkeleton />
                    ) : contentState.error ? (
                      <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)', fontSize: 'var(--font-size-sm)' }}>
                        {contentState.error}
                      </p>
                    ) : contentState.content ? (
                      <ul style={{ margin: '4px 0 0', paddingLeft: 'var(--space-4)', display: 'grid', gap: 'var(--space-1)' }}>
                        {contentState.content.results.flatMap((item) =>
                          item.observations.map((observation) => (
                            <li key={observation.observation_id} style={{ fontSize: 'var(--font-size-sm)' }}>
                              {item.catalog_display_name_snapshot} — {observation.display_label_snapshot} :{' '}
                              {observation.numeric_value ?? observation.text_value}
                              {observation.numeric_value && observation.numeric_unit
                                ? ` ${observation.numeric_unit}`
                                : ''}
                            </li>
                          )),
                        )}
                      </ul>
                    ) : null
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
