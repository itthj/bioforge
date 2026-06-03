import { cn } from "../../lib/cn";

/**
 * Small monospace label — used for step type/index and tool names. Neutral by
 * design: color in the trace is carried by the StatusDot, keeping chips calm.
 */
export function Chip({
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-fg-muted",
        className,
      )}
      {...props}
    />
  );
}
