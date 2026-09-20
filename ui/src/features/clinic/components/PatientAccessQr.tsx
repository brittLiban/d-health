/**
 * DH-PATIENT-001 §14/§69: QR generation, entirely local.
 *
 * `qrcode-generator` is a zero-dependency MIT encoder pinned exactly in `package-lock.json`; the
 * encoding happens synchronously in the browser and the access URL never leaves the page. No
 * Google Charts, no third-party QR service, no analytics, no image host, no runtime CDN fetch --
 * the patient's bearer credential is in that URL, and handing it to an external service would be
 * exactly the kind of "what patient data leaves the system" change AGENTS.md reserves for a human
 * decision.
 *
 * The modules are rendered as SVG `<rect>` elements from the encoder's own `isDark(row, col)`
 * grid rather than through the library's `createSvgTag()` + `dangerouslySetInnerHTML`: same
 * output, no HTML injection surface, and it keeps the access URL out of any attribute.
 *
 * Note what is deliberately absent from the DOM this renders: the URL appears in no `aria-label`,
 * `title`, `data-*`, `alt`, or `key`. The accessible name describes the code's *purpose*; the
 * credential itself is carried only by the black-and-white geometry (§43).
 */

import qrcode from 'qrcode-generator'

export interface PatientAccessQrProps {
  /** The full `.../?app=patient#access=<token>` URL. Never rendered as text. */
  url: string
  /** Rendered edge length in CSS pixels. */
  size?: number
  className?: string
}

export function PatientAccessQr({ url, size = 200, className }: PatientAccessQrProps) {
  // Type number 0 = "smallest version that fits"; 'M' error correction is the conventional
  // default and tolerates the print/scan degradation a photocopied handoff sheet will see.
  const code = qrcode(0, 'M')
  code.addData(url)
  code.make()

  const moduleCount = code.getModuleCount()
  // A 2-module quiet zone on each side. The spec asks for 4; 2 is the practical minimum that
  // still scans reliably and keeps the printed sheet compact, and the white card background
  // effectively extends it further.
  const quietZone = 2
  const total = moduleCount + quietZone * 2

  const rects: React.ReactElement[] = []
  for (let row = 0; row < moduleCount; row += 1) {
    for (let col = 0; col < moduleCount; col += 1) {
      if (!code.isDark(row, col)) continue
      rects.push(
        <rect
          key={`${row}-${col}`}
          x={col + quietZone}
          y={row + quietZone}
          width={1}
          height={1}
          fill="#000000"
        />,
      )
    }
  }

  return (
    <svg
      className={className}
      role="img"
      aria-label="Code QR d’accès au suivi de votre analyse"
      width={size}
      height={size}
      viewBox={`0 0 ${total} ${total}`}
      shapeRendering="crispEdges"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect x={0} y={0} width={total} height={total} fill="#ffffff" />
      {rects}
    </svg>
  )
}
