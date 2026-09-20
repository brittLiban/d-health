/**
 * DH-PATIENT-001 §32-§37: the whole patient experience -- one screen, one column, one obvious
 * status.
 *
 * What this component deliberately does **not** render, each traceable to a rule rather than an
 * oversight:
 *
 * - no result values, even after review (§36, AC16) -- it has no access to any;
 * - no interpretation of any kind: no normal/abnormal, no urgency, no advice (§37);
 * - no prices, no lab comparison, no lab choice (§6/§34, AC19/AC20) -- `toPatientLab` below is
 *   the only path from the response to the screen, and it carries three fields;
 * - no clinician identity, no internal id, no catalog code (§31/§35);
 * - no navigation, no dashboard, no tables (§32).
 */

import type { PatientOrder, PatientOrderLab, PatientOrderSelection } from '../../api/exchangeTypes'
import styles from './Patient.module.css'
import { patientStatusWording, toneMarker } from './patientStatusWording'

/**
 * §34: the single narrowing point between the Exchange's full selection receipt and what a
 * patient sees. Price fields, coordinates, the selection timestamp, and the lab's internal id are
 * dropped here, by construction, so no downstream JSX can render one.
 */
export function toPatientLab(selection: PatientOrderSelection | null): PatientOrderLab | null {
  if (!selection) return null
  return {
    lab_organization_name: selection.lab_organization_name,
    public_address: selection.public_address,
    public_phone: selection.public_phone,
  }
}

const TONE_MARKER_CLASS = {
  waiting: styles.markerWaiting,
  active: styles.markerActive,
  done: styles.markerDone,
  attention: styles.markerAttention,
  stopped: styles.markerStopped,
} as const

export interface PatientStatusPageProps {
  order: PatientOrder
}

export function PatientStatusPage({ order }: PatientStatusPageProps) {
  const wording = patientStatusWording(order.status, order.result_status)
  const lab = toPatientLab(order.selection)

  return (
    <main className={styles.screen}>
      <div className={styles.card}>
        <p className={styles.brand}>DHealth</p>
        <h1 className={styles.title}>Votre analyse</h1>

        {/*
          `role="status"` + `aria-live="polite"` so a screen reader announces the state when it
          arrives or changes (§52). The marker is `aria-hidden` because its meaning is already
          carried by the headline text -- it exists for sighted users who cannot rely on colour.
        */}
        <section className={styles.statusBlock} role="status" aria-live="polite">
          <span className={`${styles.marker} ${TONE_MARKER_CLASS[wording.tone]}`} aria-hidden="true">
            {toneMarker(wording.tone)}
          </span>
          <div>
            <h2 className={styles.headline}>{wording.headline}</h2>
            {wording.detail ? <p className={styles.detail}>{wording.detail}</p> : null}
          </div>
        </section>

        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>Analyses demandées</h3>
          {/* §35: the human display name is the only identifier shown -- never the catalog code. */}
          <ul className={styles.list}>
            {order.requested_tests.map((test, index) => (
              <li key={`${test.catalog_code_snapshot}-${index}`}>
                {test.catalog_display_name_snapshot}
              </li>
            ))}
          </ul>
        </section>

        {lab ? (
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>Laboratoire</h3>
            <p className={styles.labName}>{lab.lab_organization_name}</p>
            {lab.public_address ? <p className={styles.labLine}>{lab.public_address}</p> : null}
            {lab.public_phone ? <p className={styles.labLine}>{lab.public_phone}</p> : null}
          </section>
        ) : null}

        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>Votre clinique</h3>
          <p className={styles.labLine}>{order.ordering_provider_name}</p>
        </section>

        <p className={styles.footNote}>
          Pour toute question sur cette analyse, contactez votre clinique.
        </p>
      </div>
    </main>
  )
}
