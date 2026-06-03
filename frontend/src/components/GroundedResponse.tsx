import type { EntityClaimVerdict, NumericClaimVerdict } from "../types/agent";
import { cn } from "../lib/cn";

interface GroundedResponseProps {
  text: string;
  numericClaims?: NumericClaimVerdict[];
  entityClaims?: EntityClaimVerdict[];
}

// One normalized span to render: a character range in `text`, the claim's original surface
// form (for the offset-integrity check), its grounding status, a human tooltip, and — for a
// recognized identifier — a link to its authoritative database.
interface Span {
  start: number;
  end: number;
  surface: string;
  status: "grounded" | "unsupported";
  title: string;
  href?: string;
}

// Map each structured-identifier kind (from the deterministic entity grounder) to the public
// database that resolves it. Search-style URLs are used where a direct path isn't universal
// (e.g. RefSeq spans nucleotide + protein), so the link is correct for every member of the kind.
const ENTITY_DB: Record<string, { name: string; url: (id: string) => string }> = {
  rsid: { name: "dbSNP", url: (id) => `https://www.ncbi.nlm.nih.gov/snp/${encodeURIComponent(id)}` },
  refseq: {
    name: "NCBI",
    url: (id) => `https://www.ncbi.nlm.nih.gov/search/all/?term=${encodeURIComponent(id)}`,
  },
  ensembl: { name: "Ensembl", url: (id) => `https://www.ensembl.org/id/${encodeURIComponent(id)}` },
  clinvar: {
    name: "ClinVar",
    url: (id) => `https://www.ncbi.nlm.nih.gov/clinvar/?term=${encodeURIComponent(id)}`,
  },
  pdb: { name: "RCSB PDB", url: (id) => `https://www.rcsb.org/structure/${encodeURIComponent(id)}` },
};

function numericTitle(c: NumericClaimVerdict): string {
  if (c.status === "grounded") {
    const where = c.matched_path ?? "a tool result";
    return c.matched_value === null
      ? `Grounded — traced to ${where} this run.`
      : `Grounded — matches ${where} = ${c.matched_value}.`;
  }
  return "Not traceable to any tool result this run — treat this number with caution.";
}

function entityTitle(c: EntityClaimVerdict): string {
  const db = ENTITY_DB[c.kind];
  const opens = db ? ` Opens ${db.name}.` : "";
  if (c.status === "grounded") {
    return `Grounded — ${c.kind} found in ${c.matched_path ?? "a tool result / your request"}.${opens}`;
  }
  return `Identifier not found in any tool result or your request — treat with caution.${opens}`;
}

/**
 * Render a final response with grounded/flagged claims highlighted inline.
 *
 * Each numeric value and structured identifier the grounding validator checked carries
 * exact character offsets. We splice a subtle underline over each: solid accent-green for
 * "traced to a tool result this run" (hover → which field), dashed amber for "could not be
 * traced" (hover → caution). Recognized identifiers (rsID, RefSeq, Ensembl, ClinVar, PDB)
 * additionally become links to their source database — the trust signal is in the answer,
 * interrogable AND traversable, not buried in a trace step.
 *
 * Honest by construction: a span is only highlighted if `text.slice(start, end)` still
 * equals the claim's surface text. Any offset that doesn't line up is skipped rather than
 * mis-highlighted. Overlapping spans keep the first.
 */
export function GroundedResponse({ text, numericClaims, entityClaims }: GroundedResponseProps) {
  const spans: Span[] = [];
  for (const c of numericClaims ?? []) {
    spans.push({ start: c.start, end: c.end, surface: c.text, status: c.status, title: numericTitle(c) });
  }
  for (const c of entityClaims ?? []) {
    const db = ENTITY_DB[c.kind];
    spans.push({
      start: c.start,
      end: c.end,
      surface: c.text,
      status: c.status,
      title: entityTitle(c),
      href: db ? db.url(c.text) : undefined,
    });
  }
  spans.sort((a, b) => a.start - b.start);

  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const s of spans) {
    // Defensive: skip out-of-range, overlapping, or offset-mismatched spans so a stale
    // offset can never mis-highlight or corrupt the rendered answer.
    if (s.start < cursor || s.start < 0 || s.end > text.length || s.end <= s.start) continue;
    if (text.slice(s.start, s.end) !== s.surface) continue;
    if (s.start > cursor) nodes.push(text.slice(cursor, s.start));

    const underline =
      s.status === "grounded"
        ? "border-b border-success"
        : "border-b border-dashed border-warn text-warn";
    const body = text.slice(s.start, s.end);

    if (s.href) {
      nodes.push(
        <a
          key={`g${key++}`}
          href={s.href}
          target="_blank"
          rel="noopener noreferrer"
          title={s.title}
          data-grounding={s.status}
          className={cn("cursor-pointer bg-transparent hover:text-accent", underline)}
        >
          {body}
        </a>,
      );
    } else {
      nodes.push(
        <mark
          key={`g${key++}`}
          title={s.title}
          data-grounding={s.status}
          className={cn("cursor-help bg-transparent", underline)}
        >
          {body}
        </mark>,
      );
    }
    cursor = s.end;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));

  return <div className="whitespace-pre-wrap text-sm text-fg">{nodes}</div>;
}
