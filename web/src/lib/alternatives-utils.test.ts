import { describe, expect, it } from "vitest";
import type { Job } from "./api";
import {
  alternativeResult,
  alternativeSearchTitle,
  alternativeTierLabel,
  canCreateAlternativeBuild,
  type AlternativeSolution,
} from "./alternatives-utils";

const solution = (status: AlternativeSolution["status"]): AlternativeSolution => ({
  id: status,
  status,
  score: 0.8,
  line_items: [],
  covered_functions: [],
  evidence: [],
  missing_checks: [],
});

describe("alternative result helpers", () => {
  it("allows Build creation only for actionable tiers", () => {
    expect(canCreateAlternativeBuild(solution("documented_match"))).toBe(true);
    expect(canCreateAlternativeBuild(solution("needs_validation"))).toBe(true);
    expect(canCreateAlternativeBuild(solution("insufficient_evidence"))).toBe(false);
  });

  it("uses stable user-facing tier labels", () => {
    expect(alternativeTierLabel("documented_match")).toBe("Documented match");
    expect(alternativeTierLabel("needs_validation")).toBe("Needs validation");
    expect(alternativeTierLabel("insufficient_evidence")).toBe("Insufficient evidence");
  });

  it("reads completed results and falls back to queued payload titles", () => {
    const job = { id: "job", kind: "inventory.inverse_search", status: "queued", payload: { target_name: "ESP32" }, result: null, attempts: 0, last_error: null, expires_at: null } satisfies Job;
    expect(alternativeResult(job)).toBeNull();
    expect(alternativeSearchTitle(job)).toBe("ESP32");
    const completed = { ...job, status: "completed", result: { status: "ready", target: { canonical_name: "ESP32 DevKit V1" }, solutions: [] } } satisfies Job;
    expect(alternativeSearchTitle(completed)).toBe("ESP32 DevKit V1");
    expect(alternativeResult(completed)?.status).toBe("ready");
  });
});
