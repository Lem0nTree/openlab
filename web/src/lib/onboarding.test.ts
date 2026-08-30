import { describe, expect, it } from "vitest";
import { initialSetupStep, readinessLabel, setupTokenFromFragment, type OnboardingState, type ReadinessReport } from "./onboarding";

const report: ReadinessReport = { overall: "blocked", version: "test", checked_at: "2026-08-30T00:00:00Z", checks: [] };

describe("installation presentation", () => {
  it("does not describe a blocked report as ready", () => {
    expect(readinessLabel(report)).toBe("A few things need attention");
    expect(readinessLabel({ ...report, overall: "ready_with_warnings" })).toContain("optional");
  });

  it("resumes completed setup at the actual checks rather than forcing account creation", () => {
    const state: OnboardingState = { completed_at: null, network: { public_url: null, source: "unset", verified: false }, readiness: report };
    expect(initialSetupStep(state)).toBe(0);
    expect(initialSetupStep({ ...state, network: { ...state.network, verified: true } })).toBe(2);
    expect(initialSetupStep({ ...state, completed_at: "2026-08-30T00:00:00Z" })).toBe(5);
  });

  it("accepts only bounded URL-safe bootstrap tokens from the fragment", () => {
    expect(setupTokenFromFragment("#token=abcdefghijklmnop_123")).toBe("abcdefghijklmnop_123");
    for (const fragment of ["", "#token=short", "#token=<script>", `#token=${"x".repeat(257)}`, "#token=abcdefghijklmnop%0a"]) {
      expect(setupTokenFromFragment(fragment)).toBe("");
    }
  });
});
