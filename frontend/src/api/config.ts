export interface AppConfig {
  version: string;
  auth_enabled: boolean;
  auth_allow_registration: boolean;
}

// Public client config — drives whether the login screen is shown.
export async function getConfig(): Promise<AppConfig> {
  const res = await fetch("/config");
  if (!res.ok) throw new Error(`Failed to load config (HTTP ${res.status})`);
  return res.json() as Promise<AppConfig>;
}
