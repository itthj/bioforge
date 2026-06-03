/**
 * Minimal className joiner. Filters falsy values and joins with spaces.
 *
 * Deliberately not `clsx` + `tailwind-merge`: this slice ships zero new deps so it
 * stays offline-installable and reversible. The tradeoff is that `cn` does NOT
 * de-duplicate conflicting Tailwind classes (e.g. `px-2 px-4`), so callers pass a
 * single full class per concern via ternaries rather than overriding. If we later
 * need true class-merge semantics, swap the body for `twMerge(clsx(inputs))`.
 */
export type ClassValue = string | false | null | undefined;

export function cn(...classes: ClassValue[]): string {
  return classes.filter(Boolean).join(" ");
}
