export interface UsageSnapshot {
  spend_this_month_usd: number;
  monthly_budget_usd: number; // 0 = unlimited
  budget_enabled: boolean;
  runs_last_hour: number;
  rate_limit_runs_per_hour: number;
  rate_limit_enabled: boolean;
}

export async function getUsage(): Promise<UsageSnapshot> {
  const res = await fetch("/usage");
  if (!res.ok) throw new Error(`Failed to load usage (HTTP ${res.status})`);
  return res.json() as Promise<UsageSnapshot>;
}
