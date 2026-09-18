import { formatDateOfBirth } from '../../clinic/components/patientIdentityFormat'
import type { LabWorkflowPatientIdentity } from '../../../api/exchangeTypes'
import styles from './LabPatientIdentity.module.css'

interface LabPatientIdentityProps {
  patient: LabWorkflowPatientIdentity
  /** See `PatientIdentity`'s own identical `variant` doc (DH-CLINIC-005 §15-17) -- `full` adds
   * the complete, never-truncated technical reference as a de-emphasized fallback line. */
  variant?: 'compact' | 'full'
}

/**
 * DH-LAB-001 §16-19: the lab-safe sibling of Clinic's own `PatientIdentity` -- name and exact
 * date of birth only, deliberately never contact data (this component's own prop type,
 * `LabWorkflowPatientIdentity`, structurally cannot carry a phone/email at all). Two patients
 * with byte-identical name and DOB (§18) remain visually distinguishable only via the technical
 * reference shown in the `full` variant -- never merged, never deduplicated.
 */
export function LabPatientIdentity({ patient, variant = 'compact' }: LabPatientIdentityProps) {
  const name = patient.display_name?.trim() || 'Nom non renseigné'
  const dob = formatDateOfBirth(patient.date_of_birth)
  const isLimitedIdentity = !dob

  return (
    <div
      className={variant === 'full' ? styles.full : styles.compact}
      data-patient-id={patient.patient_id}
    >
      <p className={styles.name}>{name}</p>
      {dob ? <p className={styles.metaLine}>Né(e) le {dob}</p> : null}
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
