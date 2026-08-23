export type Thing = {
  id: string;
  name: string;
  category: string;
  manufacturer: string | null;
  mpn: string | null;
  metadata_json: Record<string, unknown>;
};

export type Location = { id: string; name: string; parent_id: string | null; public_code: string };
export type InboxItem = { id: string; input_type: string; status: string; text: string | null; error: string | null; processing_evidence: Record<string, unknown>; created_at: string };
export type InboxCandidate = { id: string; name: string; quantity: string; category: string; identity_confidence: string; status: string; thing_id: string | null; product_url: string | null; provenance: Record<string, unknown> };
export type ProviderConfig = { id: string; provider: string; base_url: string; model: string; embedding_model: string | null; enabled: boolean; embeddings_enabled: boolean; has_api_key: boolean; egress: "local" | "external" };
export type Project = { id: string; name: string; description: string | null; status: string; revision: number };
export type Requirement = { id: string; name: string; quantity: string; priority: string; constraints: Record<string, unknown>; source: string; role_key: string | null; selected_thing_id: string | null; match_status: string | null };
export type Allocation = { id: string; thing_id: string; location_id: string | null; quantity: string; state: string };
export type ProjectDetail = Project & { requirements: Requirement[]; allocations: Allocation[]; design_json: Record<string, unknown> };
export type Job = { id: string; kind: string; status: string; result: Record<string, unknown> | null; attempts: number; last_error: string | null; expires_at: string | null };

function csrfToken(): string | undefined {
  return document.cookie.split("; ").find((value) => value.startsWith("openlab_csrf="))?.split("=")[1];
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method && !["GET", "HEAD"].includes(init.method)) {
    headers.set("X-CSRF-Token", csrfToken() ?? "");
  }
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "same-origin" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function upload(path: string, body: FormData): Promise<void> {
  const response = await fetch(`/api/v1${path}`, { method: "POST", body, credentials: "same-origin", headers: { "X-CSRF-Token": csrfToken() ?? "" } });
  if (!response.ok) { const value = await response.json().catch(() => ({})); throw new Error(value.detail ?? `Upload failed (${response.status})`); }
}

export function idempotencyHeaders(): HeadersInit {
  return { "Idempotency-Key": crypto.randomUUID() };
}
