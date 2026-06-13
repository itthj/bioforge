export interface GpuStatus {
  backend: string;
  configured: boolean;
  endpoint_host: string;
}

export async function fetchGpuStatus(): Promise<GpuStatus> {
  const res = await fetch("/gpu/status");
  if (!res.ok) throw new Error("Failed to fetch GPU status");
  return res.json();
}
