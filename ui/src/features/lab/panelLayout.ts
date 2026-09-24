/**
 * DH-USABILITY-001 §29 — the queue-beside-detail layout, at a width that is not a desktop.
 *
 * Both laboratory screens put the work queue beside the open order. At 1280 that is right: the
 * technician keeps the queue in view while entering values. At 768 it was not — the fixed
 * `1fr 1fr` held two columns on a tablet, which squeezed the queue to roughly 200px (a patient
 * name broke mid-word, "Ismail Warsam / a") and pushed the detail panel past the right edge, so
 * "Enregistrer les valeurs" and "Terminer la saisie" sat outside the viewport with nothing
 * indicating it. Found by photographing the screens at three widths, not by reading the source.
 *
 * `auto-fit` + `minmax(min(100%, 22rem), 1fr)` states the rule instead of a breakpoint: a column
 * is never narrower than 22rem unless the container itself is, so the second column appears when
 * there is room for it and the panel stacks under the queue when there is not. `min(100%, …)` is
 * what keeps the track from exceeding its container at phone width — the usual `minmax(22rem,
 * 1fr)` overflows below 352px.
 */
export const PANEL_COLUMNS = 'repeat(auto-fit, minmax(min(100%, 22rem), 1fr))'
