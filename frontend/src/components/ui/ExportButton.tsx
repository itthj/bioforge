interface ExportButtonProps {
  /** Visible label, e.g. "CSV" or "SVG". */
  label: string;
  onClick: () => void;
  title?: string;
}

/**
 * A small accent text-link button for exporting a card's figure or data. Styled to match
 * the provenance footer links (on-token, understated) so exports feel native to the UI.
 */
export function ExportButton({ label, onClick, title }: ExportButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="rounded text-[11px] font-medium text-accent hover:underline"
    >
      {label}
    </button>
  );
}
