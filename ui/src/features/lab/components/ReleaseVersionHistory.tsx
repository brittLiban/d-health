import { useCallback, useEffect, useRef, useState } from 'react'
import type { ExchangeClient } from '../../../api/exchangeClient'
import type {
  LabReleaseVersionContent,
  LabReleaseVersionSummary,
  ReleasedReport,
} from '../../../api/exchangeTypes'
import { Button, LoadingSkeleton } from '../../../design-system'
import { ReleasedReportDocument } from '../../report/ReleasedReportDocument'

const HISTORY_LOAD_ERROR = "Impossible de charger l'historique des versions."
const CONTENT_LOAD_ERROR = 'Impossible de charger le contenu de cette version.'
const REPORT_LOAD_ERROR = 'Impossible de charger le compte rendu de cette version.'

/**
 * DH-USABILITY-001 §27: the same shape as every other date on this screen.
 *
 * `toLocaleString('fr-FR')` rendered "23/09/2026 14:00:00" directly above the release line's
 * "23 sept. 2026, 14:00" -- two formats, one of them carrying seconds nobody reads, in the same
 * card. Consistent wording is not decoration here: a reader comparing two timestamps should be
 * comparing the times, not first working out that the formats differ.
 */
function formatDateTime(value: string): string {
  return new Date(value).toLocaleDateString('fr-FR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * DH-USABILITY-001 §13/§21. The lab's record of what it sent needs the same fact the clinician's
 * screen needs: which of these versions is the one that counts now. "Version 1 — Version initiale"
 * says only which came first, and a technician checking what was reported reads real values with
 * nothing on screen saying an amendment has since replaced them.
 *
 * Derived from the highest version number, not from list position, so a history returned in any
 * order still names the right one. The wording matches the clinician's screen deliberately --
 * "résultat actuel" must mean the same thing in both rooms.
 */
function currentVersionIdOf(versions: LabReleaseVersionSummary[]): string | null {
  if (versions.length === 0) return null
  return versions.reduce((latest, candidate) =>
    candidate.version_number > latest.version_number ? candidate : latest,
  ).release_version_id
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
  /**
   * DHEALTH-LAB-REPORT-001: the open report, and which version it belongs to.
   *
   * `versionId` is carried alongside the payload rather than inferred, so a slow response for
   * Version 1 that arrives after the user asked for Version 2 can be discarded instead of being
   * rendered under the wrong heading. The print action lives inside the document and prints what is
   * on screen, so there is no path by which it could resolve "latest".
   */
  const [reportState, setReportState] = useState<{
    versionId: string | null
    loading: boolean
    error: string | null
    report: ReleasedReport | null
  }>({ versionId: null, loading: false, error: null, report: null })
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
    setReportState({ versionId: null, loading: false, error: null, report: null })
    load()
  }

  function handleOpenReport(versionId: string) {
    setReportState({ versionId, loading: true, error: null, report: null })
    client
      .getLabOrderReleaseVersionReport(labId, orderId, versionId)
      .then((report) => {
        if (!mountedRef.current) return
        // Guard against an out-of-order response: only the version still being requested wins.
        setReportState((prev) =>
          prev.versionId === versionId
            ? { versionId, loading: false, error: null, report }
            : prev,
        )
      })
      .catch(() => {
        if (!mountedRef.current) return
        setReportState((prev) =>
          prev.versionId === versionId
            ? { versionId, loading: false, error: REPORT_LOAD_ERROR, report: null }
            : prev,
        )
      })
  }

  function handleCloseReport() {
    setReportState({ versionId: null, loading: false, error: null, report: null })
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

  const currentVersionId = state.versions ? currentVersionIdOf(state.versions) : null

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
                      {version.release_version_id === currentVersionId ? 'Résultat actuel' : 'Remplacé'}
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
                    {/* DHEALTH-LAB-REPORT-001: one button per version, each carrying its own
                        immutable id. There is no single "Imprimer" that would have to work out
                        which version was meant. */}
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() => handleOpenReport(version.release_version_id)}
                    >
                      {reportState.versionId === version.release_version_id && reportState.loading
                        ? 'Chargement du compte rendu…'
                        : 'Voir le compte rendu'}
                    </Button>
                  </div>
                  {reportState.versionId === version.release_version_id && reportState.error ? (
                    <p
                      role="alert"
                      style={{
                        margin: '4px 0 0',
                        color: 'var(--color-danger, #b91c1c)',
                        fontSize: 'var(--font-size-sm)',
                      }}
                    >
                      {reportState.error}
                    </p>
                  ) : null}
                  {contentState.versionId === version.release_version_id ? (
                    contentState.loading ? (
                      <LoadingSkeleton />
                    ) : contentState.error ? (
                      <p role="alert" style={{ margin: 0, color: 'var(--color-danger, #b91c1c)', fontSize: 'var(--font-size-sm)' }}>
                        {contentState.error}
                      </p>
                    ) : contentState.content ? (
                      <>
                        {/*
                          DH-USABILITY-001 §13/§21: before the values, not after them. Opening an
                          older version's exact content is a legitimate act -- it is how the lab
                          answers "what did we actually report?" -- so this states a fact rather
                          than raising an error.
                        */}
                        {version.release_version_id !== currentVersionId ? (
                          <p
                            role="status"
                            style={{
                              margin: '4px 0 0',
                              fontSize: 'var(--font-size-sm)',
                              fontWeight: 600,
                            }}
                          >
                            Version remplacée — ce n’est plus le résultat de référence.
                          </p>
                        ) : null}
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
                      </>
                    ) : null
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {/* Rendered last, but it portals itself out of this subtree entirely — see
          `ReleasedReportDocument`. Nothing about this component's position in the laboratory page
          affects what reaches paper. */}
      {reportState.report ? (
        <ReleasedReportDocument report={reportState.report} onClose={handleCloseReport} />
      ) : null}
    </div>
  )
}
