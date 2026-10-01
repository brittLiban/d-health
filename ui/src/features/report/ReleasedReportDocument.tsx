import { useCallback, useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { ReleasedReport } from '../../api/exchangeTypes'
import { Button } from '../../design-system'
import styles from './ReleasedReportDocument.module.css'

/**
 * DHEALTH-LAB-REPORT-001/ADR-072 — the printable report for one exact released version.
 *
 * ── What this component is, and what it refuses to be ──
 *
 * It renders `report`, which the server built from one immutable release manifest, and nothing else.
 * It holds no fetch, no "latest" fallback and no version picker: the caller has already decided
 * which version the user is looking at, and `report.release_version_id` is that version. A print
 * action that resolved "latest" on its own is precisely the defect this shape makes impossible.
 *
 * It derives no clinical meaning. Reference ranges are printed when the laboratory released them,
 * and nothing anywhere compares a value to one. There is no normal/abnormal flag, no H/L marker, no
 * urgency, no summary and no interpretation — and nothing on this document claims a signature, a
 * seal, a certification or an accreditation, because none of those exists in this product.
 *
 * ── Why a portal ──
 *
 * The document is rendered into `document.body`, outside `#root`, and sets `dh-report-open` on
 * `body` while mounted. Printing therefore removes the entire application (see `index.css`'s print
 * block) rather than enumerating the pieces of chrome to hide — a list that would go stale the first
 * time the shell grew a part nobody remembered to add to it.
 */

const DOCUMENT_KIND = 'Compte rendu d’analyses'

/** Matches `ReleaseVersionHistory` and `ClinicResultsPage`: one date shape across the product. */
function formatDateTime(value: string): string {
  return new Date(value).toLocaleDateString('fr-FR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleDateString('fr-FR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/**
 * One wording for both rooms.
 *
 * The clinician's screen says "Rectificatif (version N)" and the laboratory's history says
 * "Amendement" for the same thing. A document that a laboratory prints and a clinic files cannot
 * say two things, and inventing a third term would be worse than either. So the report leads with
 * the version number the ticket requires to be unmissable, and qualifies it with the clinician-side
 * word, which was chosen precisely because it does not make a reader do arithmetic.
 */
function versionIdentity(report: ReleasedReport): string {
  return report.is_amendment
    ? `Version ${report.release_version_number} — rectificatif`
    : `Version ${report.release_version_number}`
}

/**
 * Derived metadata, and labelled as such.
 *
 * "Remplacée" is the word the lab and clinic screens already use. The domain has no cancelled,
 * invalid or revoked release, so this never says one — an older version is a true record of what
 * was reported, not a mistake.
 */
function currencyStatement(report: ReleasedReport): string {
  if (report.is_current_version) return 'Résultat actuel'
  return `Version remplacée — résultat actuel : version ${report.current_release_version_number}`
}

function observationValue(
  observation: ReleasedReport['results'][number]['observations'][number],
): string {
  if (observation.numeric_value !== null) return observation.numeric_value
  if (observation.text_value !== null) return observation.text_value
  return '—'
}

function referenceRange(
  observation: ReleasedReport['results'][number]['observations'][number],
): string {
  const { reference_range_low: low, reference_range_high: high } = observation
  if (low !== null && high !== null) return `${low} – ${high}`
  if (low !== null) return `≥ ${low}`
  if (high !== null) return `≤ ${high}`
  return '—'
}

export interface ReleasedReportDocumentProps {
  report: ReleasedReport
  /** Closes the report and returns the user to the screen they opened it from. */
  onClose: () => void
  /**
   * Injected only by tests. Production passes nothing and the component calls `window.print`,
   * which jsdom does not implement.
   */
  onPrint?: () => void
}

export function ReleasedReportDocument({
  report,
  onClose,
  onPrint,
}: ReleasedReportDocumentProps) {
  const headingId = `released-report-${report.release_version_id}`

  // The body class the print rules key on. Removed on unmount, including on an unmount caused by
  // navigating away rather than by the back action, so the application can never be left in a state
  // where an ordinary print emits nothing.
  useEffect(() => {
    document.body.classList.add('dh-report-open')
    return () => {
      document.body.classList.remove('dh-report-open')
    }
  }, [])

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === 'Escape') onClose()
    },
    [onClose],
  )

  function handlePrint() {
    if (onPrint) {
      onPrint()
      return
    }
    window.print()
  }

  return createPortal(
    <div
      className={styles.overlay}
      role="region"
      aria-labelledby={headingId}
      onKeyDown={handleKeyDown}
    >
      <div className={styles.actions} data-print-hide>
        {/* Focus lands on the way out, not on the document, so a keyboard user opening a
            full-surface view is never stranded without an announced exit. `autoFocus` rather than a
            ref because the design-system `Button` is a plain function component that does not
            forward one. Escape does the same thing this button does. */}
        <Button type="button" variant="secondary" onClick={onClose} autoFocus>
          ← Retour
        </Button>
        <span className={styles.actionsSpacer} />
        <Button type="button" variant="primary" onClick={handlePrint}>
          Imprimer le rapport
        </Button>
      </div>

      <article className={styles.document} data-released-report>
        <header className={styles.header}>
          <div>
            {/* The laboratory that performed and released the analyses. No address, telephone or
                registration number: the organization record holds none, and a report must not
                print a placeholder where a governed fact belongs. */}
            <h1 className={styles.laboratory} id={headingId}>
              {report.laboratory.name}
            </h1>
            <p className={styles.documentKind}>{DOCUMENT_KIND}</p>
          </div>
          {/* Version identity, stated before anything else a reader might act on. */}
          <div className={styles.versionBlock}>
            <p className={styles.versionIdentity}>{versionIdentity(report)}</p>
            <p
              className={`${styles.versionCurrency} ${
                report.is_current_version ? '' : styles.versionSuperseded
              }`}
            >
              {currencyStatement(report)}
            </p>
          </div>
        </header>

        <div className={styles.panels}>
          <section className={styles.panel} aria-labelledby={`${headingId}-patient`}>
            <h2 className={styles.panelHeading} id={`${headingId}-patient`}>
              Patient
            </h2>
            <dl className={styles.fieldList}>
              <dt>Nom</dt>
              <dd>{report.patient.display_name ?? '—'}</dd>
              <dt>Date de naissance</dt>
              <dd>{formatDate(report.patient.date_of_birth)}</dd>
            </dl>
          </section>

          <section className={styles.panel} aria-labelledby={`${headingId}-order`}>
            <h2 className={styles.panelHeading} id={`${headingId}-order`}>
              Demande
            </h2>
            <dl className={styles.fieldList}>
              <dt>Établissement demandeur</dt>
              <dd>{report.ordering_organization.name}</dd>
              <dt>Référence</dt>
              {/* The order's own identifier. DHealth has no human-facing accession or report
                  numbering scheme, and this ticket does not invent one. */}
              <dd>{report.order_id}</dd>
            </dl>
          </section>
        </div>

        <h2 className={styles.resultsHeading}>Résultats publiés</h2>
        {report.results.map((item) => (
          <section
            key={item.item_id}
            className={styles.serviceBlock}
            aria-labelledby={`${headingId}-${item.item_id}`}
          >
            <h3 className={styles.serviceHeading} id={`${headingId}-${item.item_id}`}>
              {item.catalog_display_name_snapshot}{' '}
              <span className={styles.serviceCode}>({item.catalog_code_snapshot})</span>
            </h3>
            <table className={styles.table}>
              <caption>
                Résultats de {item.catalog_display_name_snapshot}, version{' '}
                {report.release_version_number}
              </caption>
              <thead>
                <tr>
                  <th scope="col" className={styles.colLabel}>
                    Analyse
                  </th>
                  <th scope="col" className={styles.colValue}>
                    Résultat
                  </th>
                  <th scope="col" className={styles.colUnit}>
                    Unité
                  </th>
                  <th scope="col" className={styles.colRange}>
                    Intervalle de référence
                  </th>
                </tr>
              </thead>
              <tbody>
                {item.observations.map((observation) => (
                  <tr key={observation.observation_id}>
                    <th scope="row" className={styles.colLabel}>
                      {observation.display_label_snapshot}
                    </th>
                    <td className={styles.value} data-label="Résultat">
                      {observationValue(observation)}
                    </td>
                    <td data-label="Unité">{observation.numeric_unit ?? '—'}</td>
                    {/* Printed when the laboratory released one. Nothing compares the value to it:
                        no flag, no verdict, no "normal". */}
                    <td data-label="Intervalle de référence">{referenceRange(observation)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ))}

        <footer className={styles.release}>
          <dl className={styles.fieldList}>
            <dt>Statut</dt>
            <dd>Résultat publié — {versionIdentity(report)}</dd>
            <dt>Publié le</dt>
            <dd>{formatDateTime(report.released_at)}</dd>
            <dt>Publié par</dt>
            {/* The releasing laboratory, which is a governed fact. Not a person: an
                `ExchangeIdentity` carries no display name by design, so there is no staff name to
                print and none is invented. Naming the laboratory is not a signature claim. */}
            <dd>{report.laboratory.name}</dd>
          </dl>
          <p className={styles.releaseNote}>
            Ce compte rendu reproduit exactement la version publiée indiquée ci-dessus. DHealth
            enregistre et transmet les résultats ; aucune interprétation clinique n’est produite.
          </p>
        </footer>
      </article>
    </div>,
    document.body,
  )
}
