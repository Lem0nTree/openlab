"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, idempotencyHeaders, type Job, type Location, type Pin, type ProjectDetail, type Thing } from "@/lib/api";
import { formatQuantity } from "@/lib/format";
import { LabIcon } from "./lab-icon";
import { Shell } from "./shell";

type PlanComponent = { role_key: string; quantity: string; thing_id: string; thing_name: string; match_status: string };
type MissingComponent = { role_key: string; name: string; quantity: string };
type PlanSolution = { id: string; score: number; components: PlanComponent[]; missing_components: MissingComponent[] };
type SchematicPin = { id: string; name: string; number?: string | null };
type SchematicComponent = { role_key: string; thing_id: string; name: string; pins: SchematicPin[] };
type SchematicNet = { name: string; endpoints: Array<{ role_key: string; pin_id: string }> };
type ErcItem = { description: string; position?: { x?: unknown; y?: unknown } };
type ErcFinding = { severity: string; type: string; description: string; items: ErcItem[] };
type ErcResult = {
  status?: string;
  reason?: string;
  report?: string | null;
  findings?: ErcFinding[];
  counts?: { errors?: number; warnings?: number; total?: number };
};
type SchematicResult = {
  status?: string;
  summary?: string;
  source_job_id?: string;
  components?: SchematicComponent[];
  nets?: SchematicNet[];
  validation?: { status?: string; errors?: string[]; warnings?: string[]; required_support?: string[] };
  erc?: ErcResult;
};

const ACTIVE_JOB_STATES = new Set(["queued", "running"]);

function getErcFindings(erc: ErcResult | undefined): ErcFinding[] {
  if (Array.isArray(erc?.findings)) return erc.findings;
  if (!erc?.report) return [];
  try {
    const parsed = JSON.parse(erc.report) as { sheets?: Array<{ violations?: unknown[] }> };
    return (parsed.sheets ?? []).flatMap((sheet) => (sheet.violations ?? []).flatMap((value) => {
      if (!value || typeof value !== "object") return [];
      const finding = value as { severity?: unknown; type?: unknown; description?: unknown; items?: unknown };
      const items = Array.isArray(finding.items) ? finding.items.flatMap((item) => {
        if (!item || typeof item !== "object") return [];
        const row = item as { description?: unknown; pos?: { x?: unknown; y?: unknown } };
        return [{ description: String(row.description ?? "Affected schematic item"), position: row.pos }];
      }) : [];
      return [{
        severity: String(finding.severity ?? "warning"),
        type: String(finding.type ?? "unknown"),
        description: String(finding.description ?? "KiCad ERC finding"),
        items,
      }];
    }));
  } catch {
    return [];
  }
}

function ErcFindings({ erc }: { erc: ErcResult }) {
  const findings = getErcFindings(erc);
  const grouped = new Map<string, ErcFinding & { count: number }>();
  for (const finding of findings) {
    const key = `${finding.severity}:${finding.type}:${finding.description}`;
    const existing = grouped.get(key);
    if (existing) {
      existing.count += 1;
      existing.items.push(...finding.items);
    } else {
      grouped.set(key, { ...finding, items: [...finding.items], count: 1 });
    }
  }
  const groups = [...grouped.values()].sort((left, right) => (left.severity === "error" ? -1 : 1) - (right.severity === "error" ? -1 : 1));
  const errors = findings.filter((finding) => finding.severity === "error").length;
  const warnings = findings.filter((finding) => finding.severity === "warning").length;
  return <section className={`erc-findings is-${erc.status ?? "unknown"}`} aria-live="polite">
    <div className="erc-findings-head"><div><small>KICAD ELECTRICAL RULES CHECK</small><strong>{erc.status?.replaceAll("_", " ") ?? "not run"}</strong></div>{findings.length ? <p><b>{errors}</b> error{errors === 1 ? "" : "s"} · <b>{warnings}</b> warning{warnings === 1 ? "" : "s"}</p> : null}</div>
    {erc.reason ? <p className="error">{erc.reason}</p> : null}
    {groups.map((group) => <details key={`${group.severity}:${group.type}`} open={group.severity === "error"}>
      <summary><span className={`erc-severity is-${group.severity}`}>{group.severity}</span><strong>{group.description}</strong><b>{group.count}</b></summary>
      <p><code>{group.type}</code></p>
      {group.items.length ? <ul>{group.items.map((item, index) => <li key={`${item.description}:${index}`}>{item.description}</li>)}</ul> : null}
    </details>)}
    {erc.status === "passed" ? <p className="erc-clear">No KiCad ERC findings remain.</p> : null}
    {erc.status === "not_run" ? <p>KiCad is not configured. <a href="/settings#kicad">Configure KiCad</a></p> : null}
  </section>;
}

function ConnectionDiagram({ schematic }: { schematic: SchematicResult | null }) {
  const components = schematic?.components ?? [];
  const pins = new Map<string, { role: string; name: string }>();
  for (const component of components) {
    for (const pin of component.pins ?? []) pins.set(pin.id, { role: component.role_key, name: pin.name });
  }
  const nets = schematic?.nets ?? [];
  if (nets.length === 0) return <div className="build-panel-empty"><LabIcon name="layers"/><strong>No connection diagram yet</strong><p>Generate wiring after a solution has reviewed pin data.</p></div>;
  return <div className="connection-diagram">
    {nets.map((net) => <article className="connection-net" key={net.name}>
      <strong>{net.name}</strong>
      <div>{net.endpoints.map((endpoint, index) => {
        const pin = pins.get(endpoint.pin_id);
        return <span className="connection-endpoint" key={`${endpoint.role_key}-${endpoint.pin_id}`}><small>{pin?.role ?? endpoint.role_key}</small><b>{pin?.name ?? endpoint.pin_id}</b>{index < net.endpoints.length - 1 ? <i/> : null}</span>;
      })}</div>
    </article>)}
  </div>;
}

function PinoutCard({ thing, pins }: { thing: Thing; pins: Pin[] }) {
  return <article className="build-pinout-card">
    <div><span><LabIcon name="chip"/></span><div><small>{thing.category}</small><strong>{thing.name}</strong></div><b className={pins.length ? "is-ready" : "is-missing"}>{pins.length ? `${pins.length} pins` : "missing"}</b></div>
    {pins.length ? <div className="build-pin-list">{pins.map((pin) => <div key={pin.id}><code>{pin.name}</code><span>{pin.role}</span><small>{pin.electrical_type.replaceAll("_", " ")}</small>{pin.restrictions ? <p>{pin.restrictions}</p> : null}</div>)}</div> : <p>No reviewed source is saved for this exact module yet.</p>}
  </article>;
}

function BuildInstructions({ hasSolution, pinoutsReady, schematic }: { hasSolution: boolean; pinoutsReady: boolean; schematic: SchematicResult | null }) {
  const nets = schematic?.nets ?? [];
  const steps = !hasSolution
    ? ["Run BUILD intelligence and select one inventory-backed solution."]
    : !pinoutsReady
      ? ["Enrich and review the selected components before deciding any connection."]
      : nets.length === 0
        ? ["Generate a checked wiring proposal from the reviewed pin records."]
        : [
            "Disconnect every power source before changing the wiring.",
            "Connect the common ground net first.",
            ...nets.filter((net) => net.name.toUpperCase() !== "GND").map((net) => `Connect the ${net.name} net exactly as shown in the connection diagram.`),
            "Inspect polarity and supply rails, then power the controller before attaching external loads.",
            "Run a sensor reading test and an indicator test before moving the circuit into its enclosure.",
          ];
  return <ol className="build-instructions">{steps.map((step, index) => <li key={step}><span>{String(index + 1).padStart(2, "0")}</span><p>{step}</p></li>)}</ol>;
}

export function BuildDetail({ projectId }: { projectId: string }) {
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [things, setThings] = useState<Thing[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [pinouts, setPinouts] = useState<Record<string, Pin[]>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editingDetails, setEditingDetails] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");

  const load = useCallback(async () => {
    try {
      const [nextDetail, nextThings, nextLocations, nextJobs] = await Promise.all([
        api<ProjectDetail>(`/projects/${projectId}`),
        api<Thing[]>("/things"),
        api<Location[]>("/locations"),
        api<Job[]>(`/projects/${projectId}/jobs`),
      ]);
      const selectedIds = [...new Set(nextDetail.requirements.flatMap((item) => item.selected_thing_id ? [item.selected_thing_id] : []))];
      const pinRows = await Promise.all(selectedIds.map(async (thingId) => [thingId, await api<Pin[]>(`/things/${thingId}/pins`)] as const));
      setDetail(nextDetail);
      setThings(nextThings);
      setLocations(nextLocations);
      setJobs(nextJobs);
      setPinouts(Object.fromEntries(pinRows));
      setError("");
    } catch (nextError) {
      setError((nextError as Error).message);
    }
  }, [projectId]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const hasActiveJob = jobs.some((job) => ACTIVE_JOB_STATES.has(job.status));
  useEffect(() => {
    if (!hasActiveJob) return;
    const timer = window.setInterval(() => { void load(); }, 1500);
    return () => window.clearInterval(timer);
  }, [hasActiveJob, load]);

  async function mutate(action: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    setError("");
    try { await action(); await load(); return true; }
    catch (nextError) { setError((nextError as Error).message); return false; }
    finally { setBusy(false); }
  }

  function startEditingDetails() {
    if (!detail) return;
    setEditName(detail.name);
    setEditDescription(detail.description ?? "");
    setError("");
    setEditingDetails(true);
  }

  async function saveDetails(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const name = editName.trim();
    if (!name) {
      setError("A build title is required.");
      return;
    }
    const saved = await mutate(() => api(`/projects/${detail.id}`, {
      method: "PATCH",
      body: JSON.stringify({ name, description: editDescription.trim() || null }),
    }));
    if (saved) setEditingDetails(false);
  }

  async function generatePlan() {
    if (!detail) return;
    await mutate(() => api<Job>(`/projects/${detail.id}/plan`, { method: "POST", body: JSON.stringify({ goal: detail.description }) }));
  }

  async function acceptPlan(solutionId: string, job: Job) {
    if (!detail) return;
    await mutate(() => api(`/projects/${detail.id}/plan/accept`, { method: "POST", body: JSON.stringify({ job_id: job.id, solution_id: solutionId, revision: detail.revision }) }));
  }

  async function enrichSelected() {
    if (!detail) return;
    await mutate(() => api(`/projects/${detail.id}/enrich`, { method: "POST" }));
  }

  async function generateSchematic() {
    if (!detail) return;
    await mutate(() => api<Job>(`/projects/${detail.id}/schematic`, { method: "POST", body: JSON.stringify({ notes: null, repair_job_id: null }) }));
  }

  async function fixSchematic(sourceJobId: string) {
    if (!detail) return;
    await mutate(() => api<Job>(`/projects/${detail.id}/schematic`, {
      method: "POST",
      body: JSON.stringify({
        notes: "Repair the wiring using the referenced KiCad ERC findings. Preserve verified connections and change only what the findings require.",
        repair_job_id: sourceJobId,
      }),
    }));
  }

  async function acceptSchematic(job: Job) {
    if (!detail) return;
    await mutate(() => api(`/projects/${detail.id}/schematic/accept`, { method: "POST", body: JSON.stringify({ job_id: job.id, revision: detail.revision }) }));
  }

  async function setStatus(status: "pending" | "active" | "completed") {
    if (!detail) return;
    await mutate(() => api(`/projects/${detail.id}`, { method: "PATCH", body: JSON.stringify({ status }) }));
  }

  async function addRequirement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    await mutate(() => api(`/projects/${detail.id}/requirements`, { method: "POST", body: JSON.stringify({ name: data.get("name"), quantity: Number(data.get("quantity")), priority: data.get("priority"), constraints: {} }) }));
    form.reset();
  }

  async function allocate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    await mutate(() => api(`/projects/${detail.id}/allocations`, { method: "POST", headers: idempotencyHeaders(), body: JSON.stringify({ thing_id: data.get("thing_id"), location_id: data.get("location_id"), quantity: Number(data.get("quantity")), state: data.get("state") }) }));
    form.reset();
  }

  if (!detail) return <Shell title="Build"><div className="build-detail-loading"><span/><p>{error || "Opening build workspace…"}</p></div></Shell>;

  const thingById = new Map(things.map((thing) => [thing.id, thing]));
  const latestPlan = jobs.find((job) => job.kind === "project.plan") ?? null;
  const latestSchematicJob = jobs.find((job) => job.kind === "project.schematic") ?? null;
  const planSolutions = (latestPlan?.result?.solutions as PlanSolution[] | undefined) ?? [];
  const acceptedSolution = detail.design_json.solution as PlanSolution | undefined;
  const acceptedSchematic = detail.design_json.schematic as SchematicResult | undefined;
  const acceptedSchematicJobId = acceptedSchematic?.source_job_id;
  const latestSchematicResult = latestSchematicJob?.result as SchematicResult | null;
  const latestIsNewProposal = Boolean(latestSchematicJob && latestSchematicJob.id !== acceptedSchematicJobId);
  const schematic = latestIsNewProposal ? latestSchematicResult ?? acceptedSchematic ?? null : acceptedSchematic ?? latestSchematicResult ?? null;
  const schematicSourceJobId = latestIsNewProposal && latestSchematicResult ? latestSchematicJob?.id : acceptedSchematicJobId ?? latestSchematicJob?.id;
  const selectedRequirements = detail.requirements.filter((item) => item.selected_thing_id);
  const missingRequirements = detail.requirements.filter((item) => !item.selected_thing_id || item.match_status === "missing");
  const missingPinouts = selectedRequirements.filter((item) => !pinouts[item.selected_thing_id ?? ""]?.length);
  const pinoutsReady = selectedRequirements.length > 0 && missingPinouts.length === 0;
  let knownCost = 0;
  let pricedCount = 0;
  for (const requirement of selectedRequirements) {
    const thing = thingById.get(requirement.selected_thing_id ?? "");
    const unitCost = Number(thing?.metadata_json.unit_cost);
    if (Number.isFinite(unitCost)) { knownCost += unitCost * Number(requirement.quantity); pricedCount += 1; }
  }
  const priceMissing = selectedRequirements.length - pricedCount;
  const validation = schematic?.validation;
  const ercFindings = getErcFindings(schematic?.erc);
  const ercErrorCount = ercFindings.filter((finding) => finding.severity === "error").length;
  const ercWarningCount = ercFindings.filter((finding) => finding.severity === "warning").length;
  const plannerRunning = latestPlan ? ACTIVE_JOB_STATES.has(latestPlan.status) : false;
  const schematicRunning = latestSchematicJob ? ACTIVE_JOB_STATES.has(latestSchematicJob.status) : false;
  const canAcceptLatestSchematic = Boolean(
    latestSchematicJob
    && latestSchematicResult
    && latestSchematicJob.id !== acceptedSchematicJobId
    && latestSchematicResult.status !== "blocked"
    && latestSchematicResult.erc?.status !== "failed"
    && ercErrorCount === 0
  );

  return <Shell title="Build workspace">
    <div className="build-detail-nav"><Link href="/projects">← All builds</Link><div className="build-status-actions"><span className={`build-status is-${detail.status}`}><i/>{detail.status}</span><button className="secondary-button" disabled={busy || detail.status === "pending"} onClick={() => void setStatus("pending")}>Pending</button><button className="secondary-button" disabled={busy || detail.status === "active"} onClick={() => void setStatus("active")}>Active</button><button disabled={busy || detail.status === "completed"} onClick={() => void setStatus("completed")}>Complete</button></div></div>
    <section className="build-detail-hero"><div className="build-detail-hero-copy"><p className="eyebrow">BUILD / {detail.id.slice(0, 8).toUpperCase()}</p>{editingDetails ? <form className="build-details-form" onSubmit={saveDetails}><label htmlFor="build-title">Title<input id="build-title" value={editName} onChange={(event) => setEditName(event.target.value)} maxLength={300} autoFocus required/></label><label htmlFor="build-purpose">Purpose<textarea id="build-purpose" value={editDescription} onChange={(event) => setEditDescription(event.target.value)} maxLength={2000} rows={3} placeholder="What are you building?"/></label><div className="build-details-actions"><button type="submit" disabled={busy}>Save changes</button><button type="button" className="secondary-button" disabled={busy} onClick={() => setEditingDetails(false)}>Cancel</button></div></form> : <><div className="build-title-row"><h2>{detail.name}</h2><button type="button" className="build-edit-button" aria-label="Edit build title and purpose" title="Edit build title and purpose" onClick={startEditingDetails}><LabIcon name="edit"/></button></div><p>{detail.description || "No purpose recorded yet."}</p></>}</div><div className="build-health"><span><small>SOLUTION</small><strong>{acceptedSolution ? "Selected" : "Pending"}</strong></span><span><small>PINOUTS</small><strong>{selectedRequirements.length ? `${selectedRequirements.length - missingPinouts.length}/${selectedRequirements.length}` : "—"}</strong></span><span><small>WIRING</small><strong>{validation?.status?.replaceAll("_", " ") ?? "Not run"}</strong></span><span><small>COST</small><strong>{pricedCount ? `€${knownCost.toFixed(2)}` : "Not recorded"}</strong></span></div></section>
    {error && <p className="error build-error">{error}</p>}

    <div className="build-detail-grid">
      <section className="build-detail-main">
        <article className="build-panel build-solution-panel">
          <div className="build-panel-head"><div><p className="eyebrow">01 / SOLUTION</p><h2>{acceptedSolution ? "Selected solution" : "Choose a solution"}</h2><p>{String(detail.design_json.summary ?? "Match the goal against inventory before making a buy list.")}</p></div><button disabled={busy || plannerRunning} onClick={() => void generatePlan()}>{plannerRunning ? "Planning…" : acceptedSolution ? "Replan" : "Find best build"}</button></div>
          {latestPlan && <div className={`build-job-state is-${latestPlan.status}`}><span>Planner</span><strong>{latestPlan.status.replaceAll("_", " ")}</strong></div>}
          {!acceptedSolution && latestPlan && planSolutions.map((solution, index) => <article className="build-solution" key={solution.id}><div><strong>Solution {index + 1}</strong><small>{solution.components.length} owned · {solution.missing_components.length} to buy</small></div><div className="build-item-list">{solution.components.map((component) => <div className="build-item" key={component.role_key}><span>{component.quantity}×</span><strong>{component.thing_name}</strong><small>{component.role_key.replaceAll("_", " ")} · {component.match_status}</small></div>)}{solution.missing_components.map((component) => <div className="build-item missing" key={component.role_key}><span>{component.quantity}×</span><strong>{component.name}</strong><small>Component required</small></div>)}</div><button disabled={busy} onClick={() => void acceptPlan(solution.id, latestPlan)}>Use this solution</button></article>)}
          {acceptedSolution ? <div className="selected-component-grid">{acceptedSolution.components.map((component) => { const thing = thingById.get(component.thing_id); const pins = pinouts[component.thing_id] ?? []; return <div key={component.role_key}><span><LabIcon name="chip"/></span><small>{component.role_key.replaceAll("_", " ")}</small><strong>{thing?.name ?? component.thing_name}</strong><p>{pins.length ? `${pins.length} reviewed pins ready` : "Pinout source required"}</p><b className={component.match_status === "pass" ? "pass" : "unknown"}>{component.match_status}</b></div>; })}</div> : null}
        </article>

        <article className="build-panel">
          <div className="build-panel-head"><div><p className="eyebrow">02 / CONNECTIONS</p><h2>Connection diagram</h2><p>Every endpoint comes from saved pin data. OpenLab will not invent a connection.</p></div>{acceptedSolution ? <button disabled={busy || schematicRunning} onClick={() => void generateSchematic()}>{schematicRunning ? "Checking…" : schematic?.nets?.length ? "Regenerate" : "Generate wiring"}</button> : null}</div>
          {latestSchematicJob && <div className={`build-job-state is-${latestSchematicJob.status}`}><span>Wiring</span><strong>{latestSchematicJob.status.replaceAll("_", " ")}</strong></div>}
          <ConnectionDiagram schematic={schematic}/>
          {validation ? <div className={`wiring-validation is-${validation.status}`}><div><small>DETERMINISTIC CHECK</small><strong>{validation.status?.replaceAll("_", " ")}</strong></div>{validation.errors?.map((value) => <p className="error" key={value}>{value}</p>)}{validation.warnings?.map((value) => <p className="notice" key={value}>{value}</p>)}</div> : null}
          {schematic?.erc ? <ErcFindings erc={schematic.erc}/> : null}
          {acceptedSchematic && !latestIsNewProposal && ercErrorCount > 0 ? <p className="erc-accepted-warning">This saved schematic was accepted with KiCad errors. Repair and re-check it before building.</p> : null}
          {(ercErrorCount > 0 && schematicSourceJobId) || canAcceptLatestSchematic ? <div className="erc-actions">
            {ercErrorCount > 0 && schematicSourceJobId ? <button disabled={busy || schematicRunning} onClick={() => void fixSchematic(schematicSourceJobId)}>{schematicRunning ? "Repairing…" : "Fix wiring"}</button> : null}
            {canAcceptLatestSchematic && latestSchematicJob ? <button className="secondary-button" disabled={busy || schematicRunning} onClick={() => void acceptSchematic(latestSchematicJob)}>{ercWarningCount ? "Accept with warnings" : "Accept checked wiring"}</button> : null}
          </div> : null}
          {acceptedSchematic ? <a className="text-link" href={`/api/v1/projects/${detail.id}/schematic.kicad_sch`}>Download KiCad schematic →</a> : null}
        </article>

        <article className="build-panel">
          <div className="build-panel-head"><div><p className="eyebrow">03 / ASSEMBLY</p><h2>Build instructions</h2><p>Instructions unlock progressively as the solution and wiring become grounded in reviewed data.</p></div></div>
          <BuildInstructions hasSolution={Boolean(acceptedSolution)} pinoutsReady={pinoutsReady} schematic={schematic}/>
        </article>

        <article className="build-panel">
          <div className="build-panel-head"><div><p className="eyebrow">04 / PIN DATA</p><h2>Selected component pinouts</h2><p>Accepted local records are shown with their restrictions and provenance state.</p></div>{missingPinouts.length ? <button disabled={busy || hasActiveJob} onClick={() => void enrichSelected()}>{hasActiveJob ? "Enriching…" : "Enrich pinouts"}</button> : <span className="panel-ready"><LabIcon name="check"/>READY</span>}</div>
          <div className="build-pinout-grid">{selectedRequirements.map((requirement) => { const thing = thingById.get(requirement.selected_thing_id ?? ""); return thing ? <PinoutCard key={thing.id} thing={thing} pins={pinouts[thing.id] ?? []}/> : null; })}</div>
        </article>
      </section>

      <aside className="build-detail-aside">
        <article className="build-side-card"><p className="eyebrow">BUILD COST</p><strong className="build-cost">{pricedCount ? `€${knownCost.toFixed(2)}` : "—"}</strong><p>{priceMissing ? `${priceMissing} selected component price${priceMissing === 1 ? " is" : "s are"} not recorded. Totals never use guessed prices.` : "All selected component prices are recorded."}</p></article>
        <article className="build-side-card"><p className="eyebrow">WHAT YOU NEED</p><div className="needed-list">{selectedRequirements.map((requirement) => <div key={requirement.id}><span>{formatQuantity(requirement.quantity)}×</span><strong>{thingById.get(requirement.selected_thing_id ?? "")?.name ?? requirement.name}</strong><small>owned</small></div>)}{missingRequirements.map((requirement) => <div className="missing" key={requirement.id}><span>{formatQuantity(requirement.quantity)}×</span><strong>{requirement.name}</strong><small>buy</small></div>)}{validation?.required_support?.map((value) => <div className="missing" key={value}><span>!</span><strong>{value}</strong><small>verify</small></div>)}</div></article>
        <article className="build-side-card"><p className="eyebrow">TEST STATUS</p><div className="test-checks"><div><span className={pinoutsReady ? "pass" : "wait"}/><p><strong>Pin coverage</strong><small>{pinoutsReady ? "Reviewed pin data available" : `${missingPinouts.length} component pinout${missingPinouts.length === 1 ? "" : "s"} missing`}</small></p></div><div><span className={validation?.status === "valid" ? "pass" : validation?.status === "blocked" ? "fail" : "wait"}/><p><strong>Wiring validation</strong><small>{validation?.status?.replaceAll("_", " ") ?? "Not run"}</small></p></div><div><span className={schematic?.erc?.status === "passed" ? "pass" : schematic?.erc?.status === "failed" || ercErrorCount ? "fail" : "wait"}/><p><strong>KiCad ERC</strong><small>{schematic?.erc?.status === "violations" ? `${ercErrorCount} errors · ${ercWarningCount} warnings` : schematic?.erc?.status ?? "Not configured"}</small></p></div></div></article>
        <article className="build-side-card"><p className="eyebrow">BOM REQUIREMENTS</p><div className="build-item-list">{detail.requirements.map((item) => <div className={`build-item ${item.match_status === "missing" ? "missing" : ""}`} key={item.id}><span>{formatQuantity(item.quantity)}×</span><strong>{item.name}</strong><small>{item.priority}</small></div>)}</div><form className="requirement-form compact-form" onSubmit={addRequirement}><input name="name" placeholder="Part or capability" required/><input name="quantity" aria-label="Quantity" type="number" min="0.000001" step="any" defaultValue="1" required/><select name="priority" aria-label="Priority" defaultValue="required"><option value="required">Required</option><option value="recommended">Recommended</option><option value="optional">Optional</option></select><button disabled={busy}>Add</button></form></article>
        <article className="build-side-card"><p className="eyebrow">STOCK ALLOCATION</p><div className="needed-list">{detail.allocations.map((item) => <div key={item.id}><span>{item.quantity}×</span><strong>{thingById.get(item.thing_id)?.name ?? item.thing_id}</strong><small>{item.state.replaceAll("_", " ")}</small></div>)}{detail.allocations.length === 0 ? <p>No physical stock reserved yet.</p> : null}</div><form className="allocation-form compact-form" onSubmit={allocate}><select name="thing_id" required defaultValue=""><option value="" disabled>Choose Thing</option>{things.map((thing) => <option key={thing.id} value={thing.id}>{thing.name}</option>)}</select><select name="location_id" required defaultValue=""><option value="" disabled>Source location</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select><input name="quantity" aria-label="Quantity" type="number" min="0.000001" step="any" defaultValue="1" required/><select name="state" aria-label="Allocation state" defaultValue="reserved"><option value="reserved">Reserve</option><option value="in_use">Move into use</option><option value="recoverable">Recoverable</option></select><button disabled={busy}>Allocate</button></form></article>
      </aside>
    </div>
  </Shell>;
}
