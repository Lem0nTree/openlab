import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type ProviderConfig } from "./api";
import { connectOnboardingProvider, initialSetupStep, openRouterEndpoint, openRouterFreeModel, readinessLabel, readinessStep, setupSteps, setupTokenFromFragment, type OnboardingState, type ReadinessReport } from "./onboarding";

vi.mock("./api", () => ({ api: vi.fn() }));

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
    expect(initialSetupStep({ ...state, completed_at: "2026-08-30T00:00:00Z" })).toBe(readinessStep);
    expect(setupSteps[readinessStep]).toBe("Readiness");
    expect(setupSteps[readinessStep - 1]).toBe("Product MCP");
  });

  it("accepts only bounded URL-safe bootstrap tokens from the fragment", () => {
    expect(setupTokenFromFragment("#token=abcdefghijklmnop_123")).toBe("abcdefghijklmnop_123");
    for (const fragment of ["", "#token=short", "#token=<script>", `#token=${"x".repeat(257)}`, "#token=abcdefghijklmnop%0a"]) {
      expect(setupTokenFromFragment(fragment)).toBe("");
    }
  });
});

describe("first-time AI connection", () => {
  const saved: ProviderConfig = { id: "provider", provider: "openai-compatible", base_url: openRouterEndpoint,
    model: openRouterFreeModel, embedding_model: null, embeddings_enabled: false, enabled: true, has_api_key: true, egress: "external" };
  const draft = { endpoint: openRouterEndpoint, model: openRouterFreeModel, key: "test-key" };
  const ready: OnboardingState = { completed_at: null, network: { public_url: null, source: "unset", verified: false },
    readiness: { ...report, checks: [{ id: "ai", label: "AI", required: false, status: "pass", code: "OK", summary: "Model listing passed" }] } };

  beforeEach(() => vi.resetAllMocks());

  it("saves and enables before testing, and verifies readiness before resolving", async () => {
    vi.mocked(api).mockResolvedValueOnce(saved).mockResolvedValueOnce({ models: [openRouterFreeModel] }).mockResolvedValueOnce(ready);
    const onSaved = vi.fn();
    expect(await connectOnboardingProvider(draft, null, onSaved)).toEqual([openRouterFreeModel]);
    expect(vi.mocked(api).mock.calls.map(([path]) => path)).toEqual(["/ai/provider", "/ai/provider/models", "/onboarding"]);
    const payload = JSON.parse(vi.mocked(api).mock.calls[0][1]!.body as string);
    expect(payload).toMatchObject({ enabled: true, api_key: "test-key", model: "openrouter/free" });
    expect(onSaved).toHaveBeenCalledWith(saved);
    expect(onSaved.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api).mock.invocationCallOrder[1]);
  });

  it("does not continue or test when saving fails", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Save failed"));
    const onSaved = vi.fn();
    await expect(connectOnboardingProvider(draft, null, onSaved)).rejects.toThrow("Save failed");
    expect(api).toHaveBeenCalledTimes(1);
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("keeps the saved provider for retry but does not continue after a test failure", async () => {
    vi.mocked(api).mockResolvedValueOnce(saved).mockRejectedValueOnce(new Error("Endpoint unavailable"));
    const onSaved = vi.fn();
    await expect(connectOnboardingProvider(draft, null, onSaved)).rejects.toThrow("Endpoint unavailable");
    expect(onSaved).toHaveBeenCalledWith(saved);
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("does not continue if recap still considers AI unverified", async () => {
    vi.mocked(api).mockResolvedValueOnce(saved).mockResolvedValueOnce({ models: [] }).mockResolvedValueOnce({ ...ready, readiness: report });
    await expect(connectOnboardingProvider(draft, null, vi.fn())).rejects.toThrow("readiness has not verified");
  });

  it("keeps a stored key on retry without resubmitting it", async () => {
    vi.mocked(api).mockResolvedValueOnce(saved).mockResolvedValueOnce({ models: [] }).mockResolvedValueOnce(ready);
    await connectOnboardingProvider({ ...draft, key: "" }, saved, vi.fn());
    expect(JSON.parse(vi.mocked(api).mock.calls[0][1]!.body as string)).not.toHaveProperty("api_key");
  });

  it("clears the old credential when switching endpoints without a new key", async () => {
    vi.mocked(api).mockResolvedValueOnce(saved).mockResolvedValueOnce({ models: [] }).mockResolvedValueOnce(ready);
    await connectOnboardingProvider({ ...draft, endpoint: "http://host.docker.internal:11434/v1", key: "" }, saved, vi.fn());
    expect(JSON.parse(vi.mocked(api).mock.calls[0][1]!.body as string).api_key).toBe("");
  });
});
