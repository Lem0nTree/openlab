import type { components } from "./openapi";
import { api, type ProviderConfig } from "./api";

export type ReadinessReport = components["schemas"]["ReadinessReport"];
export type OnboardingState = components["schemas"]["OnboardingOut"];
export type InstallationOverview = components["schemas"]["InstallationOverview"];
export type InstallationPolicy = components["schemas"]["InstallationPolicy"];
export type NetworkSettings = components["schemas"]["NetworkOut"];

export const setupSteps = ["Lab", "Network", "AI", "KiCad", "Access & updates", "Product MCP", "Readiness"] as const;
export const readinessStep = setupSteps.length - 1;
export const openRouterEndpoint = "https://openrouter.ai/api/v1";
export const openRouterFreeModel = "openrouter/free";

// Keep save, endpoint test, and readiness verification ordered. A failed request
// must leave the user on the AI step, including on their first provider setup.
export async function connectOnboardingProvider(
  draft: { endpoint: string; model: string; key: string },
  previous: ProviderConfig | null,
  onSaved: (provider: ProviderConfig) => void,
): Promise<string[]> {
  const endpoint = draft.endpoint.trim().replace(/\/$/, "");
  const endpointChanged = previous?.base_url.replace(/\/$/, "") !== endpoint;
  const saved = await api<ProviderConfig>("/ai/provider", {
    method: "PUT",
    body: JSON.stringify({
      base_url: endpoint, model: draft.model.trim(), enabled: true,
      embedding_model: previous?.embedding_model ?? null,
      embeddings_enabled: previous?.embeddings_enabled ?? false,
      // Never forward a stored key to a newly selected provider.
      ...(draft.key.trim() ? { api_key: draft.key.trim() } : endpointChanged ? { api_key: "" } : {}),
    }),
  });
  onSaved(saved);
  const result = await api<{ models: string[] }>("/ai/provider/models");
  const state = await api<OnboardingState>("/onboarding");
  if (state.readiness.checks.find((check) => check.id === "ai")?.status !== "pass") {
    throw new Error("AI settings were saved, but readiness has not verified this connection. Try connecting again.");
  }
  return result.models;
}

export function readinessLabel(report: ReadinessReport): string {
  return report.overall === "ready" ? "Your lab is ready" : report.overall === "ready_with_warnings"
    ? "Ready, with optional features skipped or unverified" : "A few things need attention";
}

export function initialSetupStep(state: OnboardingState): number {
  if (state.completed_at) return readinessStep;
  return state.network.verified ? 2 : 0;
}

export function setupTokenFromFragment(fragment: string): string {
  const token = new URLSearchParams(fragment.replace(/^#/, "")).get("token") ?? "";
  return /^[A-Za-z0-9_-]{16,256}$/.test(token) ? token : "";
}
