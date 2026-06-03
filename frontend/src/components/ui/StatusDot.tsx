import { cn } from "../../lib/cn";

/**
 * Status indicator. Color comes from the caller via a text-color class
 * (e.g. `text-success`) because the dot fills with `bg-current`. `pulse` is the
 * only motion in the trace — a quiet heartbeat on the step currently running.
 */
export function StatusDot({
  className,
  pulse,
}: {
  className?: string;
  pulse?: boolean;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block h-2 w-2 shrink-0 rounded-full bg-current",
        pulse && "animate-pulse-dot",
        className,
      )}
    />
  );
}
