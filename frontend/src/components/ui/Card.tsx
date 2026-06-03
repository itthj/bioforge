import { cn } from "../../lib/cn";

/**
 * Surface shell. The single source of "what a panel looks like" so every result,
 * trace step, and approval prompt shares the same chrome on the dark canvas.
 */
export function Card({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-lg border border-border bg-surface", className)}
      {...props}
    />
  );
}
