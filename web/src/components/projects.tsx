"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, idempotencyHeaders, type Job, type Location, type Project, type ProjectDetail, type Thing } from "@/lib/api";
import { LabIcon } from "./lab-icon";
import { Shell } from "./shell";

export function Projects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [things, setThings] = useState<Thing[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [error, setError] = useState("");
  const [planJob, setPlanJob] = useState<Job | null>(null);
  const [schematicJob, setSchematicJob] = useState<Job | null>(null);
  const [planning, setPlanning] = useState(false);

  const load = async () => {
    try {
      const [nextProjects, nextThings, nextLocations] = await Promise.all([
        api<Project[]>("/projects"), api<Thing[]>("/things"), api<Location[]>("/locations"),
      ]);
      setProjects(nextProjects);
      setThings(nextThings);
      setLocations(nextLocations);
      if (detail) setDetail(await api<ProjectDetail>(`/projects/${detail.id}`));
    } catch (nextError) {
      setError((nextError as Error).message);
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
    // Initial client synchronization only; load is intentionally not a render dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const active = [planJob, schematicJob].filter((job) => job && ["queued", "running"].includes(job.status)) as Job[];
    if (active.length === 0) return;
    const timer = window.setInterval(() => {
      for (const job of active) {
        void api<Job>(`/jobs/${job.id}`).then((next) => {
          if (next.kind === "project.plan") setPlanJob(next); else setSchematicJob(next);
          if (!["queued", "running"].includes(next.status)) setPlanning(false);
        }).catch((nextError: Error) => { setError(nextError.message); setPlanning(false); });
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [planJob, schematicJob]);

  async function select(id: string) {
    setPlanJob(null); setSchematicJob(null); setPlanning(false);
    try {
      const [nextDetail, jobs] = await Promise.all([
        api<ProjectDetail>(`/projects/${id}`),
        api<Job[]>(`/projects/${id}/jobs`),
      ]);
      const latestPlan = jobs.find((job) => job.kind === "project.plan") ?? null;
      const latestSchematic = jobs.find((job) => job.kind === "project.schematic") ?? null;
      setDetail(nextDetail);
      setPlanJob(latestPlan);
      setSchematicJob(latestSchematic);
      setPlanning(Boolean([latestPlan, latestSchematic].some((job) => job && ["queued", "running"].includes(job.status))));
    }
    catch (nextError) { setError((nextError as Error).message); }
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ name: data.get("name"), description: data.get("description") || null }),
      });
      form.reset();
      await load();
      await select(project.id);
    } catch (nextError) { setError((nextError as Error).message); }
  }

  async function addRequirement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api(`/projects/${detail.id}/requirements`, {
        method: "POST",
        body: JSON.stringify({ name: data.get("name"), quantity: Number(data.get("quantity")), priority: data.get("priority"), constraints: {} }),
      });
      form.reset();
      await select(detail.id);
    } catch (nextError) { setError((nextError as Error).message); }
  }

  async function allocate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api(`/projects/${detail.id}/allocations`, {
        method: "POST",
        headers: idempotencyHeaders(),
        body: JSON.stringify({ thing_id: data.get("thing_id"), location_id: data.get("location_id"), quantity: Number(data.get("quantity")), state: data.get("state") }),
      });
      form.reset();
      await select(detail.id);
    } catch (nextError) { setError((nextError as Error).message); }
  }

  async function generatePlan() {
    if (!detail) return;
    setPlanning(true); setError(""); setSchematicJob(null);
    try {
      setPlanJob(await api<Job>(`/projects/${detail.id}/plan`, { method: "POST", body: JSON.stringify({ goal: detail.description }) }));
    } catch (nextError) { setError((nextError as Error).message); setPlanning(false); }
  }

  async function acceptPlan(solutionId: string) {
    if (!detail || !planJob) return;
    setPlanning(true); setError("");
    try {
      const next = await api<ProjectDetail>(`/projects/${detail.id}/plan/accept`, { method: "POST", body: JSON.stringify({ job_id: planJob.id, solution_id: solutionId, revision: detail.revision }) });
      setDetail(next); setPlanJob(null);
    } catch (nextError) { setError((nextError as Error).message); } finally { setPlanning(false); }
  }

  async function generateSchematic() {
    if (!detail) return;
    setPlanning(true); setError("");
    try {
      setSchematicJob(await api<Job>(`/projects/${detail.id}/schematic`, { method: "POST", body: JSON.stringify({ notes: null }) }));
    } catch (nextError) { setError((nextError as Error).message); setPlanning(false); }
  }

  async function acceptSchematic() {
    if (!detail || !schematicJob) return;
    setPlanning(true); setError("");
    try {
      const next = await api<ProjectDetail>(`/projects/${detail.id}/schematic/accept`, { method: "POST", body: JSON.stringify({ job_id: schematicJob.id, revision: detail.revision }) });
      setDetail(next); setSchematicJob(null);
    } catch (nextError) { setError((nextError as Error).message); } finally { setPlanning(false); }
  }

  const planSolutions = (planJob?.result?.solutions as Array<{ id: string; score: number; components: Array<{ role_key: string; quantity: string; thing_name: string; match_status: string }>; missing_components: Array<{ role_key: string; name: string; quantity: string }> }> | undefined) ?? [];
  const schematicValidation = schematicJob?.result?.validation as { status?: string; errors?: string[]; warnings?: string[]; required_support?: string[] } | undefined;
  const acceptedDesign = detail?.design_json;
  const acceptedSolution = acceptedDesign?.solution as { components?: unknown[] } | undefined;
  const canGenerateSchematic = (acceptedSolution?.components?.length ?? 0) > 0;
  const planFailed = planJob?.status === "dead_letter" || planJob?.status === "expired";

  return <Shell title="Projects & BUILD">
    <section className="build-overview">
      <div><p className="eyebrow">FROM IDEA TO BENCH</p><h2>Build with what you already own.</h2><p>Define requirements, reserve real stock, and keep every recoverable component traceable.</p></div>
      <div className="build-stats"><span><b>{projects.length}</b><small>PROJECTS</small></span><i/><span><b>{things.length}</b><small>KNOWN THINGS</small></span></div>
    </section>
    {error && <p className="error build-error">{error}</p>}

    <div className="build-workspace">
      <section className="project-index-panel">
        <div className="build-panel-heading"><div><p className="eyebrow">NEW BUILD</p><h2>Create a project</h2></div><span><LabIcon name="plus"/></span></div>
        <form className="project-create-form" onSubmit={create}>
          <label><span>Project name</span><input name="name" placeholder="e.g. Battery plant monitor" required /></label>
          <label><span>Purpose</span><input name="description" placeholder="What should it do? (optional)" /></label>
          <button>Create project <LabIcon name="arrow"/></button>
        </form>

        <div className="project-list-heading"><span>YOUR PROJECTS</span><b>{projects.length}</b></div>
        <div className="project-list">
          {projects.map((project) => <button type="button" className={detail?.id === project.id ? "project-row active" : "project-row"} key={project.id} onClick={() => select(project.id)}>
            <span className="project-row-icon"><LabIcon name="folder"/></span>
            <span className="project-row-copy"><strong>{project.name}</strong><small>{project.description || "No description yet"}</small></span>
            <span className="project-row-status"><i/>{project.status}</span>
            <LabIcon className="project-row-arrow" name="arrow"/>
          </button>)}
          {projects.length === 0 && <div className="project-list-empty"><LabIcon name="folder"/><p>Your builds will appear here.</p></div>}
        </div>
      </section>

      {detail ? <section className="project-detail-panel">
        <div className="project-detail-head"><div><p className="eyebrow">ACTIVE BUILD</p><h2>{detail.name}</h2><p>{detail.description || "Add requirements to start shaping this build."}</p></div><span className="build-status"><i/>{detail.status}</span></div>

        <section className="build-block build-planner">
          <div className="build-block-heading"><span className="build-block-icon"><LabIcon name="spark"/></span><div><h3>BUILD intelligence</h3><p>Match the goal to owned items before making a buy list.</p></div><button type="button" onClick={() => void generatePlan()} disabled={planning}>{planning && planJob?.status !== "completed" ? "Planning…" : planFailed ? "Retry build plan" : "Find best build"}</button></div>
          {planJob && <div className={`build-job-state is-${planJob.status}`}><span>Planner</span><strong>{planJob.status.replaceAll("_", " ")}</strong></div>}
          {planFailed && <p className="error">The planner could not complete this build. Check the AI endpoint in Settings, then retry.</p>}
          {planSolutions.map((solution, index) => <article className="build-solution" key={solution.id}><div><strong>Solution {index + 1}</strong><small>{solution.components.length} inventory matches · {solution.missing_components.length} required to buy</small></div><div className="build-item-list">{solution.components.map((component) => <div className="build-item" key={component.role_key}><span>{component.quantity}×</span><strong>{component.thing_name}</strong><small>{component.role_key.replaceAll("_", " ")} · {component.match_status}</small></div>)}{solution.missing_components.map((component) => <div className="build-item missing" key={component.role_key}><span>{component.quantity}×</span><strong>{component.name}</strong><small>Component required</small></div>)}</div><button type="button" onClick={() => void acceptPlan(solution.id)} disabled={planning}>Use this solution</button></article>)}
          {planJob?.status === "completed" && planSolutions.length === 0 && <p className="build-empty-copy">No complete stock match was found. Add manual requirements or enrich inventory profiles, then try again.</p>}
          {Boolean(acceptedDesign?.status) && <div className="build-accepted"><strong>Accepted design</strong><span>{String(acceptedDesign?.status).replaceAll("_", " ")}</span></div>}
        </section>

        {canGenerateSchematic && <section className="build-block build-planner">
          <div className="build-block-heading"><span className="build-block-icon cyan"><LabIcon name="layers"/></span><div><h3>Wiring proposal</h3><p>Uses only saved pin data, then runs deterministic electrical checks.</p></div><button type="button" onClick={() => void generateSchematic()} disabled={planning}>{planning && schematicJob?.status !== "completed" ? "Checking…" : "Generate schematic"}</button></div>
          {schematicJob?.last_error && <p className="error">{schematicJob.last_error}</p>}
          {schematicValidation && <div className="schematic-review"><span className="status">{schematicValidation.status}</span>{schematicValidation.errors?.map((value) => <p className="error" key={value}>{value}</p>)}{schematicValidation.warnings?.map((value) => <p className="notice" key={value}>{value}</p>)}{schematicValidation.required_support?.map((value) => <div className="build-item missing" key={value}><strong>{value}</strong><small>Component required / verify first</small></div>)}{schematicJob?.result?.status !== "blocked" && <button type="button" onClick={() => void acceptSchematic()} disabled={planning}>Accept checked schematic</button>}</div>}
          {Boolean(acceptedDesign?.schematic) && <a className="text-link" href={`/api/v1/projects/${detail.id}/schematic.kicad_sch`}>Download KiCad schematic →</a>}
        </section>}

        <section className="build-block">
          <div className="build-block-heading"><span className="build-block-icon"><LabIcon name="layers"/></span><div><h3>BOM requirements</h3><p>What the build needs, before matching it to stock.</p></div><b>{detail.requirements.length}</b></div>
          <div className="build-item-list">{detail.requirements.map((item) => <div className="build-item" key={item.id}><span>{item.quantity}×</span><strong>{item.name}</strong><small>{item.priority}</small></div>)}{detail.requirements.length === 0 && <p className="build-empty-copy">No requirements yet. Add the first part or capability below.</p>}</div>
          <form className="requirement-form" onSubmit={addRequirement}>
            <input name="name" placeholder="Part or capability" required />
            <input name="quantity" aria-label="Quantity" type="number" min="0.000001" step="any" defaultValue="1" required />
            <select name="priority" aria-label="Priority" defaultValue="required"><option value="required">Required</option><option value="recommended">Recommended</option><option value="optional">Optional</option></select>
            <button>Add</button>
          </form>
        </section>

        <section className="build-block">
          <div className="build-block-heading"><span className="build-block-icon cyan"><LabIcon name="box"/></span><div><h3>Stock allocations</h3><p>Reserve a real Thing from a known location.</p></div><b>{detail.allocations.length}</b></div>
          <div className="build-item-list">{detail.allocations.map((item) => <div className="build-item" key={item.id}><span>{item.quantity}×</span><strong>{things.find((thing) => thing.id === item.thing_id)?.name ?? item.thing_id}</strong><small>{item.state.replace("_", " ")}</small></div>)}{detail.allocations.length === 0 && <p className="build-empty-copy">Nothing allocated yet. Requirements remain separate from physical stock.</p>}</div>
          <form className="allocation-form" onSubmit={allocate}>
            <select name="thing_id" required defaultValue=""><option value="" disabled>Choose Thing</option>{things.map((thing) => <option key={thing.id} value={thing.id}>{thing.name}</option>)}</select>
            <select name="location_id" required defaultValue=""><option value="" disabled>Source location</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select>
            <input name="quantity" aria-label="Quantity" type="number" min="0.000001" step="any" defaultValue="1" required />
            <select name="state" aria-label="Allocation state" defaultValue="reserved"><option value="reserved">Reserve</option><option value="in_use">Move into use</option><option value="recoverable">Recoverable</option></select>
            <button>Allocate</button>
          </form>
        </section>
      </section> : <section className="build-empty-state">
        <span className="empty-build-orb"><LabIcon name="spark"/></span><p className="eyebrow">BUILD WORKSPACE</p><h2>Select a project to open its bench.</h2><p>Requirements and physical allocations stay separate, so planning never silently changes your stock.</p>
        <div className="empty-build-flow"><span>IDEA</span><i/><span>BOM</span><i/><span>STOCK</span><i/><span>BUILD</span></div>
      </section>}
    </div>
  </Shell>;
}
