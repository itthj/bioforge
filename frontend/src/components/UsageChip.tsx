import { useEffect, useState } from "react";
import { getUsage, type UsageSnapshot } from "../api/usage";

/** Small header indicator of the signed-in user's spend this month (and budget, if one is set). */
export function UsageChip() {
  const [usage, setUsage] = useState<UsageSnapshot | null>(null);

  useEffect(() => {
    let cancelled = false;
    getUsage()
      .then((u) => {
        if (!cancelled) setUsage(u);
      })
      .catch(() => {
        /* usage is informational; stay silent if it can't be fetched */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!usage) return null;

  const spend = `$${usage.spend_this_month_usd.toFixed(2)}`;
  const hasBudget = usage.budget_enabled && usage.monthly_budget_usd > 0;
  const over = hasBudget && usage.spend_this_month_usd >= usage.monthly_budget_usd;
  const label = hasBudget ? `${spend} / $${usage.monthly_budget_usd.toFixed(2)}` : `${spend} this month`;

  return (
    <span
      className={`text-xs ${over ? "text-danger" : "text-fg-subtle"}`}
      title={over ? "Monthly budget reached — new runs are blocked until next month." : "Your spend this month"}
    >
      {label}
    </span>
  );
}
