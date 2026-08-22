import styles from './StatusBadge.module.css'

export type StatusTone = 'neutral' | 'success' | 'warning' | 'danger'

interface StatusBadgeProps {
  label: string
  tone?: StatusTone
}

// Status is never color-only (§19): the text label is the actual signal,
// color is a reinforcement layered on top of it, never a replacement for it.
export function StatusBadge({ label, tone = 'neutral' }: StatusBadgeProps) {
  return <span className={[styles.badge, styles[tone]].join(' ')}>{label}</span>
}
