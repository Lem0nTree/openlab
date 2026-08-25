"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type Job, type ProjectDetail, type ProviderConfig } from "@/lib/api";
import {
  alternativeResult,
  alternativeSearchTitle,
  alternativeTierLabel,
  canCreateAlternativeBuild,
  type AlternativeSolution,
} from "@/lib/alternatives-utils";
import { LabIcon } from "./lab-icon";
import { Shell } from "./shell";

const ACTIVE_JOB_STATES = new Set(["queued", "running"]);

function SolutionCard({ solution, busy, onCreate }: { solution: AlternativeSolution; busy: boolean; onCreate: (solutionId: string) => void }) {
  const actionable = canCreateAlternativeBuild(solution);
  return <article className={`alternative-solution is-${solution.status}`}>
    <div className="alternative-solution-head">
      <div><span className="alternative-tier"><i />{alternativeTierLabel(solution.status)}</span><h3>{solution.line_items.length === 1 ? "Single-piece alternative" : `${solution.line_items.length}-piece alternative`}</h3></div>
      <small>{Math.round(solution.score * 100)}% retrieval score</small>
    </div>
    <div className="alternative-line-items">{solution.line_items.map((item) => <div className="alternative-line" key={`${item.thing_id}-${item.covered_roles.join("-")}`}>
      <span>{item.quantity}×</span>
      <div><strong>{item.thing_name}</strong><small>{item.category} · {item.available_quantity} available</small><p>{item.locations.length ? item.locations.join(" · ") : "Location not recorded"}</p></div>
      <Link href={`/inventory/${item.thing_id}`} aria-label={`Open ${item.thing_name}`}><LabIcon name="arrow" /></Link>
    </div>)}</div>
    <div className="alternative-coverage"><strong>Functions covered</strong><p>{solution.covered_functions.join(" · ")}</p></div>
    {solution.evidence.length ? <details><summary>Match evidence</summary><ul>{solution.evidence.map((entry, index) => <li key={`${entry}-${index}`}>{entry}</li>)}</ul></details> : null}
    {solution.missing_checks.length ? <div className="alternative-warning"><LabIcon name="bolt"/><div><strong>Validate before use</strong><ul>{solution.missing_checks.map((entry, index) => <li key={`${entry}-${index}`}>{entry}</li>)}</ul></div></div> : null}
    {actionable ? <button disabled={busy} onClick={() => onCreate(solution.id)}>Create Build for validation <LabIcon name="arrow"/></button> : <p className="alternative-blocked">This result is informational only because the recorded evidence is insufficient.</p>}
  </article>;
}

export function Alternatives() {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<Job | null>(null);
  const [provider, setProvider] = useState<ProviderConfig | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [nextJobs, nextProvider] = await Promise.all([
        api<Job[]>("/alternatives/searches"),
        api<ProviderConfig | null>("/ai/provider"),
      ]);
      setJobs(nextJobs);
      setSelected((current) => current ? nextJobs.find((job) => job.id === current.id) ?? current : nextJobs[0] ?? null);
      setProvider(nextProvider);
      setError("");
    } catch (nextError) {
      setError((nextError as Error).message);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    if (!selected || !ACTIVE_JOB_STATES.has(selected.status)) return;
    const timer = window.setInterval(() => {
      void api<Job>(`/jobs/${selected.id}`).then((job) => {
        setSelected(job);
        setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      }).catch((nextError: Error) => setError(nextError.message));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [selected]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const job = await api<Job>("/alternatives/search", {
        method: "POST",
        body: JSON.stringify({ target_name: data.get("target_name"), intended_use: data.get("intended_use") || null }),
      });
      setSelected(job);
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    } catch (nextError) {
      setError((nextError as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function createBuild(solutionId: string) {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const project = await api<ProjectDetail>(`/alternatives/${selected.id}/solutions/${solutionId}/build`, { method: "POST" });
      router.push(`/projects/${project.id}`);
    } catch (nextError) {
      setError((nextError as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const result = alternativeResult(selected);
  const providerMessage = !provider?.enabled
    ? "Unknown parts need an enabled model in Settings; reviewed local records still work offline."
    : provider.egress === "local"
      ? "Unknown-part analysis stays on your configured local model."
      : "Unknown-part analysis may leave this server through your configured external model.";
  const analyzing = selected ? ACTIVE_JOB_STATES.has(selected.status) : false;

  return <Shell title="Alternatives" signal={{ label: analyzing ? "ANALYZING" : "READY", tone: analyzing ? "checking" : "ready" }}>
    <section className="alternatives-intro"><div><p className="eyebrow">INVERSE SEARCH</p><h2>Replace a part with what you already own.</h2><p>Describe one component or board. OpenLab separates its functions, checks available stock, and explains what still needs validation.</p></div><span><LabIcon name="repeat"/></span></section>
    <form className="alternative-search-form" onSubmit={submit}>
      <label><span>Component or board</span><input name="target_name" placeholder="For example: ESP32 DevKit V1" maxLength={300} required /></label>
      <label><span>Intended use <small>optional</small></span><textarea name="intended_use" placeholder="What must it do in your circuit?" maxLength={2000} rows={3}/></label>
      <div><p><LabIcon name="command"/>{providerMessage} <Link href="/settings">Review settings</Link></p><button disabled={busy}>{busy ? "Working…" : "Find stock alternatives"}<LabIcon name="search"/></button></div>
    </form>
    {error ? <p className="error build-error">{error}</p> : null}
    <div className="alternatives-layout">
      <aside className="alternative-history"><div><p className="eyebrow">RECENT SEARCHES</p><span>{jobs.length}</span></div>{jobs.length ? jobs.map((job) => <button className={selected?.id === job.id ? "is-active" : ""} key={job.id} onClick={() => setSelected(job)}><span><LabIcon name="repeat"/></span><div><strong>{alternativeSearchTitle(job)}</strong><small>{job.status.replaceAll("_", " ")}</small></div><LabIcon name="arrow"/></button>) : <p>No inverse searches yet.</p>}</aside>
      <section className="alternative-results">
        {!selected ? <div className="alternative-empty"><span><LabIcon name="repeat"/></span><h2>Start with the part you need.</h2><p>OpenLab will look for a single stocked substitute first, then bounded combinations of up to four pieces.</p></div> : null}
        {analyzing ? <div className="alternative-empty is-loading"><span><LabIcon name="spark"/></span><h2>Checking functions and stock…</h2><p>The worker is resolving the target, checking reservations, and comparing documented capabilities.</p></div> : null}
        {selected?.status === "dead_letter" ? <div className="alternative-empty"><span><LabIcon name="bolt"/></span><h2>Analysis could not finish.</h2><p>{selected.last_error || "The configured model or worker returned an error."}</p></div> : null}
        {result?.status === "insufficient_target_knowledge" ? <div className="alternative-empty"><span><LabIcon name="search"/></span><h2>Not enough target knowledge.</h2><p>{result.message}</p></div> : null}
        {result?.status === "ready" && result.target ? <>
          <article className="alternative-target"><div><p className="eyebrow">TARGET ANALYSIS</p><h2>{result.target.canonical_name}</h2><p>{result.target.summary}</p></div><div className={`alternative-source is-${result.target.confidence}`}><span>{result.target.confidence === "reviewed" ? "Reviewed local knowledge" : "Model-inferred function"}</span><small>{result.target.provider_egress === "external" ? "External provider" : "Local data path"}</small></div></article>
          {result.target.assumptions.length ? <div className="alternative-assumptions"><strong>Assumptions</strong><ul>{result.target.assumptions.map((entry, index) => <li key={`${entry}-${index}`}>{entry}</li>)}</ul></div> : null}
          {result.direct_stock ? <div className="alternative-direct"><LabIcon name="check"/><div><strong>The requested part is already available.</strong><p>{result.direct_stock.available_quantity}× {result.direct_stock.thing_name} in {result.direct_stock.locations.join(" · ") || "recorded stock"}.</p></div><Link href={`/inventory/${result.direct_stock.thing_id}`}>Open Thing <LabIcon name="arrow"/></Link></div> : null}
          <div className="alternative-results-head"><div><p className="eyebrow">STOCK-BACKED OPTIONS</p><h2>{result.solutions.length ? `${result.solutions.length} bounded alternative${result.solutions.length === 1 ? "" : "s"}` : "No viable alternative"}</h2></div><p>Functional evidence is not a promise of electrical or drop-in compatibility.</p></div>
          {result.solutions.map((solution) => <SolutionCard solution={solution} busy={busy} onCreate={(id) => void createBuild(id)} key={solution.id}/>)}
          {!result.solutions.length ? <div className="alternative-empty compact"><span><LabIcon name="box"/></span><h2>Current stock cannot cover the target.</h2><p>{result.gaps?.map((gap) => gap.name).join(" · ") || "Add reviewed capabilities or receive suitable stock, then search again."}</p></div> : null}
        </> : null}
      </section>
    </div>
  </Shell>;
}
