// Small, dependency-free helpers for getting figures and data OUT of BioForge — the
// thing scientists need for papers (CSV tables, SVG figures). Kept pure and isolated so
// each card can wire an export with one import and a one-line handler, and so the
// serialization logic is unit-testable without a real download.

/** A CSV cell: rendered as-is for strings/numbers, empty for null/undefined. */
export type CsvCell = string | number | boolean | null | undefined;

/** Quote a single CSV field per RFC 4180 (only when it contains a comma, quote, or newline). */
function csvField(value: CsvCell): string {
  if (value === null || value === undefined) return "";
  const s = String(value);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/**
 * Serialize a 2-D array of cells (header row first, by convention) to a CSV string with
 * CRLF line endings (Excel-friendly). Faithful: never reorders or drops columns.
 */
export function toCsv(rows: CsvCell[][]): string {
  return rows.map((row) => row.map(csvField).join(",")).join("\r\n");
}

/**
 * Serialize a live <svg> element to a standalone SVG document string. Inlines each
 * element's COMPUTED fill/stroke/stroke-width so SVGs styled via CSS classes (Tailwind
 * utilities) still render with the right colors as a saved file — inline-attribute SVGs
 * already round-trip untouched. Best-effort: getComputedStyle may be unavailable in some
 * environments (e.g. happy-dom), in which case the un-inlined clone is returned.
 */
export function svgToString(svg: SVGSVGElement): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  try {
    const originals = svg.querySelectorAll<SVGElement>("*");
    const clones = clone.querySelectorAll<SVGElement>("*");
    originals.forEach((orig, i) => {
      const target = clones[i];
      if (!target) return;
      const cs = getComputedStyle(orig);
      for (const prop of ["fill", "stroke", "stroke-width"] as const) {
        const v = cs.getPropertyValue(prop);
        if (v && v !== "none" && !target.getAttribute(prop)) {
          target.setAttribute(prop, v);
        }
      }
    });
  } catch {
    // No computed styles available — fall back to the structural clone.
  }
  return new XMLSerializer().serializeToString(clone);
}

/**
 * Trigger a browser download of `data` as a file named `filename`. Creates an object URL
 * from a Blob, clicks a transient anchor, then revokes the URL. A no-op-safe convenience:
 * the URL is always revoked, even if the click throws.
 */
export function downloadBlob(
  filename: string,
  mime: string,
  data: string | Blob,
): void {
  const blob = data instanceof Blob ? data : new Blob([data], { type: mime });
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
