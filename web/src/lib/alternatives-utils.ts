import type { Job } from "./api";

export type AlternativeTier = "documented_match" | "needs_validation" | "insufficient_evidence";
export type AlternativeLineItem = {
  thing_id: string;
  thing_name: string;
  category: string;
  quantity: string;
  available_quantity: string;
  locations: string[];
  covered_roles: string[];
  evidence: string[];
};
export type AlternativeSolution = {
  id: string;
  status: AlternativeTier;
  score: number;
  line_items: AlternativeLineItem[];
  covered_functions: string[];
  evidence: string[];
  missing_checks: string[];
};
export type AlternativeTarget = {
  input_name: string;
  canonical_name: string;
  category: string | null;
  summary: string;
  assumptions: string[];
  critical_interfaces: string[];
  intended_use: string | null;
  knowledge_source: "inventory" | "local_catalog" | "model";
  provider_egress: "local" | "external" | null;
  confidence: "reviewed" | "model_inferred";
};
export type AlternativeResult = {
  status: "ready" | "insufficient_target_knowledge";
  target_name?: string;
  intended_use?: string | null;
  message?: string;
  target?: AlternativeTarget;
  direct_stock?: { thing_id: string; thing_name: string; available_quantity: string; locations: string[] } | null;
  solutions: AlternativeSolution[];
  gaps?: Array<{ role_key: string; name: string; reason: string }>;
};

export function alternativeResult(job: Job | null): AlternativeResult | null {
  if (!job?.result) return null;
  return job.result as AlternativeResult;
}

export function canCreateAlternativeBuild(solution: AlternativeSolution): boolean {
  return solution.status === "documented_match" || solution.status === "needs_validation";
}

export function alternativeTierLabel(tier: AlternativeTier): string {
  if (tier === "documented_match") return "Documented match";
  if (tier === "needs_validation") return "Needs validation";
  return "Insufficient evidence";
}

export function alternativeSearchTitle(job: Job): string {
  const target = job.result?.target;
  if (target && typeof target === "object" && "canonical_name" in target) {
    return String(target.canonical_name);
  }
  return String(job.payload.target_name ?? "Alternative search");
}
