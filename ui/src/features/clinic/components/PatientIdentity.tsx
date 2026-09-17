import type { PatientIdentitySummary } from '../../../api/exchangeTypes'
import { formatContactLabel, formatDateOfBirth } from './patientIdentityFormat'
import styles from './PatientIdentity.module.css'

interface PatientIdentityProps {
  patient: PatientIdentitySummary
  /**
   * `compact` -- a list/table row: name, then DOB/contact/limited-identity as small secondary
   * lines. No technical identifier (DH-CLINIC-005 §20: the directory list needs name/DOB/contact
   * at a glance, not a UUID).
   *
   * `full` -- a workspace header, order-review step, order confirmation, or result-detail banner:
   * the same fields at a more prominent size, plus the complete (never abbreviated) technical
   * DHealth reference as a de-emphasized fallback line (§17: a technical identifier, once shown,
   * must always be the full id -- never a truncated fragment presented as if it were unique).
   */
  variant?: 'compact' | 'full'
}

/**
 * DH-CLINIC-005 §15/§16: the one shared presentation of human patient identity across Patients,
 * Orders, and Results -- name is always the most prominent element; DOB and contact are shown only
 * when actually known, never fabricated or defaulted (§16/AC10); a Patient with only a name gets a
 * plain, non-alarming "limited identity" notice, never a blocked workflow (§18). Nothing here is
 * conveyed by color alone (§19) -- every signal is text.
 */
export function PatientIdentity({ patient, variant = 'compact' }: PatientIdentityProps) {
  const name = patient.display_name?.trim() || 'Nom non renseigné'
  const dob = formatDateOfBirth(patient.date_of_birth)
  const hasContact = Boolean(patient.contact_value)
  const isLimitedIdentity = !dob && !hasContact

  return (
    <div
      className={variant === 'full' ? styles.full : styles.compact}
      data-patient-id={patient.patient_id}
    >
      <p className={styles.name}>{name}</p>
      {dob ? <p className={styles.metaLine}>Né(e) le {dob}</p> : null}
      {hasContact ? (
        <p className={styles.metaLine}>
          {formatContactLabel(patient.contact_channel_type)} : {patient.contact_value}
        </p>
      ) : null}
      {isLimitedIdentity ? (
        <p className={styles.warning}>
          Identité limitée — vérifiez le patient avant de continuer.
        </p>
      ) : null}
      {variant === 'full' ? (
        <p className={styles.technicalId}>
          Réf. DHealth : <code>{patient.patient_id}</code>
        </p>
      ) : null}
    </div>
  )
}
