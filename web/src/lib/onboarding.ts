import type { components } from "./openapi";

export type ReadinessReport = components["schemas"]["ReadinessReport"];
export type OnboardingState = components["schemas"]["OnboardingOut"];
export type InstallationOverview = components["schemas"]["InstallationOverview"];
export type InstallationPolicy = components["schemas"]["InstallationPolicy"];
export type NetworkSettings = components["schemas"]["NetworkOut"];

export const setupSteps = ["Lab", "Network", "AI", "KiCad", "Access & updates", "Readiness"] as const;

export function readinessLabel(report: ReadinessReport): string {
  return report.overall === "ready" ? "Your lab is ready" : report.overall === "ready_with_warnings"
    ? "Ready, with optional features skipped or unverified" : "A few things need attention";
}

export function initialSetupStep(state: OnboardingState): number {
  if (state.completed_at) return 5;
  return state.network.verified ? 2 : 0;
}

export function setupTokenFromFragment(fragment: string): string {
  const token = new URLSearchParams(fragment.replace(/^#/, "")).get("token") ?? "";
  return /^[A-Za-z0-9_-]{16,256}$/.test(token) ? token : "";
}
