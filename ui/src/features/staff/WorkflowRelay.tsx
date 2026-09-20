import styles from './WorkflowRelay.module.css'

/**
 * DH-MVP-POLISH-001 — the relay.
 *
 * One diagnostic order passes between organizations, and each organization
 * records facts about it. The relay shows those facts as what they are: distinct,
 * separately dated events, each attributed to the establishment that recorded it.
 *
 * ── The distinctions it exists to protect ──
 *
 * A progress bar with five even segments would imply things the data does not
 * say. These pairs are not the same fact and must never be collapsed:
 *
 *   assigned            is not  accepted
 *   sample taken        is not  sample recorded
 *   values entered      is not  entry completed
 *   entry completed     is not  released
 *   released            is not  reviewed
 *   released            is not  patient informed
 *
 * Only the right-hand side of each pair is knowable from this system. The
 * left-hand side happened in a room. So the labels a caller passes must name the
 * recorded fact, not the real-world one it implies.
 *
 * ── Two things this component structurally cannot do ──
 *
 * It cannot name a person. There is no field for one, deliberately: the backend
 * models release and review authority as organization roles and records an
 * identity only on completed facts, so a node claiming "Ahmed holds the release"
 * would be an invention. Obligations belong to establishments here.
 *
 * It cannot invent a date. `at` is nullable, and a node without one says so
 * rather than borrowing a neighbour's timestamp or the time of rendering. Several
 * of the statuses the provider-side API exposes genuinely carry no timestamp of
 * their own; that is a fact about the data and the interface shows it.
 *
 * ── Colour ──
 *
 * Every state is carried by a visible word as well as a colour and a marker
 * shape, so the relay survives a monochrome screen and never depends on hue
 * alone. The palette means workflow responsibility, never clinical
 * interpretation: `stopped` marks a record that went no further, not a bad
 * result.
 */

export type RelayNodeState =
  /** Recorded and behind us. */
  | 'done'
  /** Where the order is now. */
  | 'current'
  /** Owed by someone, not yet recorded. */
  | 'awaiting'
  /** Went no further: refused, cancelled, or superseded. */
  | 'stopped'

export interface RelayNode {
  id: string
  /**
   * The recorded fact, named precisely. "Prélèvement enregistré", never
   * "Prélèvement effectué" — this system knows only that someone recorded it.
   */
  label: string
  /** The establishment that recorded the fact, where the data identifies one. */
  organizationName?: string | null
  /** ISO 8601, or null when this fact carries no timestamp in the API. */
  at?: string | null
  state: RelayNodeState
  /** A precise technical detail, e.g. a release version number. */
  detail?: string | null
}

const STATE_WORD: Record<RelayNodeState, string> = {
  done: 'Effectué',
  current: 'En cours',
  awaiting: 'En attente',
  stopped: 'Arrêté',
}

/** fr-FR, date and time: a relay node is often distinguished from the next only by its hour. */
export function formatRelayMoment(value: string | null | undefined): string | null {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  return parsed.toLocaleString('fr-FR', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export interface WorkflowRelayProps {
  nodes: readonly RelayNode[]
  /** Names the list for screen readers, e.g. "Étapes de l'ordre". */
  label: string
}

export function WorkflowRelay({ nodes, label }: WorkflowRelayProps) {
  if (nodes.length === 0) return null

  return (
    <ol className={styles.relay} aria-label={label}>
      {nodes.map((node) => {
        const moment = formatRelayMoment(node.at)
        return (
          <li key={node.id} className={[styles.node, styles[node.state]].join(' ')}>
            <span className={styles.marker} aria-hidden="true" />
            <div className={styles.body}>
              <p className={styles.label}>
                {node.label}
                {/* The state as a word, so it is never carried by colour alone. */}
                <span className={styles.state}>{STATE_WORD[node.state]}</span>
              </p>
              {node.organizationName ? (
                <p className={styles.organization}>{node.organizationName}</p>
              ) : null}
              {moment ? (
                <p className={styles.moment}>
                  <time dateTime={node.at ?? undefined}>{moment}</time>
                </p>
              ) : node.state === 'done' ? (
                // Recorded, but this endpoint does not date it. Saying so is the
                // honest option; showing any other moment here would attribute a
                // time to a fact the system cannot time.
                <p className={styles.undated}>Date non enregistrée pour cette étape</p>
              ) : null}
              {node.detail ? (
                <p className={['technical-reference', styles.detail].join(' ')}>{node.detail}</p>
              ) : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
