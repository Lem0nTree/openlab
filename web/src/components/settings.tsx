"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { api, type Job, type KicadSettings, type LabSettings, type McpIntegration, type ProviderConfig, type SettingsOverview, type TelemetrySettings } from "@/lib/api";
import { Shell } from "./shell";
import { McpConnection } from "./mcp-connection";

const presets = {
  ollama: "http://host.docker.internal:11434/v1",
  openrouter: "https://openrouter.ai/api/v1",
  openai: "https://api.openai.com/v1",
};
const terminalJobs = new Set(["completed", "dead_letter", "expired"]);

export function Settings() {
  const [config, setConfig] = useState<ProviderConfig | null>(null);
  const [overview, setOverview] = useState<SettingsOverview | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [embeddingsEnabled, setEmbeddingsEnabled] = useState(false);
  const [preset, setPreset] = useState("");
  const [labName, setLabName] = useState("");
  const [units, setUnits] = useState<"metric" | "imperial">("metric");
  const [kicadPath, setKicadPath] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [checkingKicad, setCheckingKicad] = useState(false);
  const [mcp, setMcp] = useState<McpIntegration | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetrySettings | null>(null);

  function applyOverview(value: SettingsOverview) {
    setOverview(value);
    setLabName(value.lab.name);
    setUnits(value.lab.units);
    setKicadPath(value.kicad.cli_path ?? "");
  }

  async function refreshOverview() {
    applyOverview(await api<SettingsOverview>("/settings"));
  }

  useEffect(() => {
    Promise.all([api<ProviderConfig | null>("/ai/provider"), api<SettingsOverview>("/settings"), api<McpIntegration>("/integrations/mcp"), api<TelemetrySettings>("/settings/telemetry")])
      .then(([provider, settings, integration, telemetrySettings]) => {
        setConfig(provider);
        setBaseUrl(provider?.base_url ?? "");
        setModel(provider?.model ?? "");
        setEmbeddingModel(provider?.embedding_model ?? "");
        setEnabled(provider?.enabled ?? false);
        setEmbeddingsEnabled(provider?.embeddings_enabled ?? false);
        applyOverview(settings);
        setMcp(integration);
        setTelemetry(telemetrySettings);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  function clearFeedback() { setError(""); setMessage(""); }

  async function saveProvider(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); clearFeedback();
    try {
      const payload: Record<string, unknown> = { base_url: baseUrl, model, embedding_model: embeddingModel || null, enabled, embeddings_enabled: embeddingsEnabled };
      if (apiKey) payload.api_key = apiKey;
      const saved = await api<ProviderConfig>("/ai/provider", { method: "PUT", body: JSON.stringify(payload) });
      setConfig(saved); setApiKey(""); setMessage("Smart Inbox settings saved.");
    } catch (e) { setError((e as Error).message); }
  }

  async function saveLab(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); clearFeedback();
    try {
      const saved = await api<LabSettings>("/settings/lab", { method: "PUT", body: JSON.stringify({ name: labName, units }) });
      window.dispatchEvent(new CustomEvent("openlab:lab-updated", { detail: { name: saved.name } }));
      await refreshOverview(); setMessage("Lab preferences saved.");
    } catch (e) { setError((e as Error).message); }
  }

  async function pollKicadJob(job: Job) {
    let current = job;
    for (let attempt = 0; attempt < 30 && !terminalJobs.has(current.status); attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      current = await api<Job>(`/jobs/${current.id}`);
    }
    await refreshOverview();
    if (current.status === "completed" && current.result?.status === "available") setMessage(`KiCad detected: ${String(current.result.version ?? "available")}`);
    else if (!terminalJobs.has(current.status)) setError("KiCad check is still running. Its result will remain available on this page.");
  }

  async function checkKicad() {
    setCheckingKicad(true); clearFeedback();
    try {
      const job = await api<Job>("/settings/kicad/check", { method: "POST" });
      setOverview((current) => current ? { ...current, kicad: { ...current.kicad, check_status: job.status as KicadSettings["check_status"] } } : current);
      await pollKicadJob(job);
    } catch (e) { setError((e as Error).message); }
    finally { setCheckingKicad(false); }
  }

  async function saveKicad(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); clearFeedback();
    try {
      await api<KicadSettings>("/settings/kicad", { method: "PUT", body: JSON.stringify({ cli_path: kicadPath.trim() || null }) });
      await refreshOverview();
      await checkKicad();
    } catch (e) { setError((e as Error).message); }
  }

  async function loadModels() {
    clearFeedback();
    try {
      const result = await api<{ models: string[] }>("/ai/provider/models");
      setModels(result.models); setMessage(`${result.models.length} models found at the endpoint.`);
    } catch (e) { setError((e as Error).message); }
  }

  async function saveMcp(enabled: boolean) {
    clearFeedback();
    try {
      const saved = await api<McpIntegration>("/integrations/mcp", { method: "PUT", body: JSON.stringify({ enabled }) });
      setMcp(saved); setMessage(enabled ? "MCP integration enabled." : "MCP integration disabled.");
    } catch (e) { setError((e as Error).message); }
  }

  async function revokeMcpGrant(id: string) {
    clearFeedback();
    try { await api<void>("/integrations/mcp/revoke", { method: "POST", body: JSON.stringify({ grant_id: id }) }); setMcp(await api<McpIntegration>("/integrations/mcp")); setMessage("MCP client revoked."); }
    catch (e) { setError((e as Error).message); }
  }

  async function saveTelemetry(usageEnabled: boolean) {
    clearFeedback();
    try { const saved = await api<TelemetrySettings>("/settings/telemetry", { method: "PUT", body: JSON.stringify({ usage_enabled: usageEnabled }) }); setTelemetry(saved); setMessage(usageEnabled ? "Daily usage sharing is enabled." : "Daily usage sharing is disabled."); }
    catch (e) { setError((e as Error).message); }
  }

  async function saveNewsletter(newsletterOptIn: boolean) {
    clearFeedback();
    try { const saved = await api<TelemetrySettings>("/settings/communications", { method: "PUT", body: JSON.stringify({ newsletter_opt_in: newsletterOptIn }) }); setTelemetry(saved); setMessage(newsletterOptIn ? "Newsletter preference saved; delivery is queued." : "Newsletter preference removed."); }
    catch (e) { setError((e as Error).message); }
  }

  async function deleteTelemetryHistory() {
    clearFeedback();
    try { await api<void>("/settings/telemetry/history", { method: "DELETE" }); setMessage("Telemetry history deletion is queued."); }
    catch (e) { setError((e as Error).message); }
  }

  const kicad = overview?.kicad;
  return <Shell title="Settings"><div className="settings-layout">
    <aside className="settings-nav"><p className="nav-label">SETTINGS</p><Link href="/onboarding">Setup & readiness</Link><a href="#lab">Lab</a><a href="#smart-inbox">Smart Inbox</a><a href="#mcp">MCP integrations</a><a href="#kicad">KiCad</a><a href="#privacy">Privacy and data</a><a href="#deployment">Deployment</a></aside>
    <div className="settings-content">
      <section className="settings-section" id="lab"><div className="section-heading"><div><p className="eyebrow">LAB</p><h2>Identity and units</h2></div><span className="settings-state is-on">Local</span></div><p className="settings-copy">Name this installation and record its default measurement system.</p><form className="settings-form" onSubmit={saveLab}><label>Lab name<input value={labName} onChange={(event) => setLabName(event.target.value)} maxLength={200} required /></label><label>Default measurement system<select value={units} onChange={(event) => setUnits(event.target.value as "metric" | "imperial")}><option value="metric">Metric</option><option value="imperial">Imperial</option></select></label><div className="settings-actions"><button disabled={loading}>Save lab</button></div></form></section>

      <section className="settings-section" id="smart-inbox"><div className="section-heading"><div><p className="eyebrow">SMART INBOX</p><h2>Processing model</h2></div><span className={`settings-state ${enabled || embeddingsEnabled ? "is-on" : ""}`}>{enabled || embeddingsEnabled ? "Enabled" : "Disabled"}</span></div><p className="settings-copy">Choose one OpenAI-compatible endpoint for candidate extraction. The Inbox stays usable with AI disabled.</p><form className="settings-form" onSubmit={saveProvider}><label>Provider preset<select value={preset} onChange={(event) => { const value = event.target.value; setPreset(value); if (value) setBaseUrl(presets[value as keyof typeof presets]); }}><option value="">Custom endpoint</option><option value="ollama">Ollama on this network</option><option value="openrouter">OpenRouter</option><option value="openai">OpenAI</option></select></label><label>Endpoint URL<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} type="url" placeholder="http://localhost:11434/v1" required /></label><label>Processing model ID<input value={model} onChange={(event) => setModel(event.target.value)} list="provider-models" placeholder="qwen2.5-vl:7b" required /><datalist id="provider-models">{models.map((item) => <option key={item} value={item} />)}</datalist></label><label>Embedding model ID<input value={embeddingModel} onChange={(event) => setEmbeddingModel(event.target.value)} list="provider-models" placeholder="text-embedding-3-small" /></label><label>API key<input value={apiKey} onChange={(event) => setApiKey(event.target.value)} type="password" placeholder={config?.has_api_key ? "Stored securely; leave blank to keep" : "Not required for local endpoints"} /></label><label className="check-row"><input checked={enabled} onChange={(event) => setEnabled(event.target.checked)} type="checkbox" /> Enable processing for new Inbox items</label><label className="check-row"><input checked={embeddingsEnabled} onChange={(event) => setEmbeddingsEnabled(event.target.checked)} type="checkbox" /> Enable semantic inventory retrieval</label><div className="settings-actions"><button disabled={loading || (embeddingsEnabled && !embeddingModel)}>Save model</button><button className="secondary-button" type="button" onClick={loadModels}>Test endpoint</button></div></form>{config && (config.enabled || config.embeddings_enabled) && <p className={config.egress === "external" ? "settings-warning" : "settings-note"}>{config.egress === "external" ? "External processing is enabled. Captures or canonical profiles may leave this local server." : "Local endpoint detected. Captured content and canonical profiles stay on your configured network."}</p>}</section>

      <section className="settings-section" id="mcp"><div className="section-heading"><div><p className="eyebrow">MCP INTEGRATIONS</p><h2>Connected AI harnesses</h2></div><span className={`settings-state ${mcp?.enabled ? "is-on" : ""}`}>{mcp?.enabled ? "Enabled" : "Disabled"}</span></div><p className="settings-copy">Product MCP shares typed lab records with approved clients. It never exposes provider keys, setup controls, files, or shell access.</p><div className="settings-actions"><button type="button" onClick={() => void saveMcp(!mcp?.enabled)}>{mcp?.enabled ? "Disable MCP" : "Enable MCP"}</button></div><McpConnection integration={mcp} onChange={setMcp} />{mcp?.grants?.map((grant) => <div className="environment-row" key={grant.id}><div><strong>{grant.client_name}</strong><p>{grant.scopes.join(", ")} · last used {grant.last_used_at ? new Date(grant.last_used_at).toLocaleString() : "never"}</p></div><button className="secondary-button" type="button" onClick={() => void revokeMcpGrant(grant.id)}>Revoke</button></div>)}</section>

      <section className="settings-section" id="kicad"><div className="section-heading"><div><p className="eyebrow">KICAD</p><h2>Electrical rules check</h2></div><span className={`settings-state ${kicad?.check_status === "available" ? "is-on" : ""}`}>{kicad?.check_status?.replaceAll("_", " ") ?? "Unknown"}</span></div><p className="settings-copy">The command must exist inside the worker container. OpenLab’s standard Raspberry Pi image stays lightweight and does not install KiCad.</p><form className="settings-form" onSubmit={saveKicad}><label>Worker command or path<input value={kicadPath} onChange={(event) => setKicadPath(event.target.value)} placeholder="kicad-cli or /usr/bin/kicad-cli" maxLength={500} /></label><div className="settings-actions"><button disabled={loading || checkingKicad}>{checkingKicad ? "Checking…" : "Save and check"}</button><button type="button" className="secondary-button" disabled={checkingKicad} onClick={() => void checkKicad()}>Check again</button></div></form><div className="settings-fact"><span>Effective command</span><strong>{kicad?.effective_cli ?? "Not configured"}</strong></div><div className="settings-fact"><span>Configuration source</span><strong>{kicad?.source === "settings" ? "Settings override" : kicad?.source === "environment" ? "OPENLAB_KICAD_CLI" : "None"}</strong></div>{kicad?.version && <p className="settings-note">Worker detected {kicad.version}.</p>}{kicad?.error && <p className="settings-warning">{kicad.error}</p>}</section>

      <section className="settings-section" id="privacy"><p className="eyebrow">PRIVACY AND DATA</p><h2>Where data goes</h2><p className="settings-copy">OpenLab stores inventory and normalized capture evidence locally. Raw images, recordings, email files, and PDFs are deleted after processing.</p><div className="settings-fact"><span>Raw capture media</span><strong>Temporary until processing completes</strong></div><div className="settings-fact"><span>Inventory writes</span><strong>Only after human confirmation</strong></div>{telemetry && <><h3>Usage telemetry</h3><p className="settings-copy">When enabled, OpenLab sends a stable pseudonymous installation ID, version, platform, and daily aggregate counts to telemetry.openlab.tools. Inventory, captures, account email, and provider settings stay local.</p><label className="check-row"><input type="checkbox" checked={telemetry.usage_enabled} onChange={(event) => void saveTelemetry(event.target.checked)} /> Share privacy-preserving daily usage data</label><div className="settings-fact"><span>Installation ID</span><code>{telemetry.installation_id}</code></div><div className="settings-fact"><span>Last daily report</span><strong>{telemetry.last_reported_day ? new Date(telemetry.last_reported_day).toLocaleDateString() : "Not delivered yet"}</strong></div><div className="settings-fact"><span>Queued deliveries</span><strong>{telemetry.pending_delivery_count}</strong></div><div className="settings-actions"><button type="button" className="secondary-button" onClick={() => void deleteTelemetryHistory()}>Request telemetry history deletion</button></div><h3>OpenLab updates</h3><label className="check-row"><input type="checkbox" checked={telemetry.newsletter_status === "subscribed" || telemetry.newsletter_status === "pending_delivery"} onChange={(event) => void saveNewsletter(event.target.checked)} /> Receive occasional OpenLab product updates</label><p className="settings-copy">Status: {telemetry.newsletter_status.replaceAll("_", " ")}. This preference is separate from usage telemetry.</p></>}</section>

      <section className="settings-section" id="deployment"><p className="eyebrow">DEPLOYMENT</p><h2>Environment variables</h2><p className="settings-copy">These values come from the repository’s root <code>.env</code> or Compose configuration. Secrets are never returned to the browser. Deployment-managed changes require recreating the affected service.</p><div className="environment-list">{overview?.environment.map((variable) => <div className="environment-row" key={variable.name}><div><code>{variable.name}</code><p>{variable.description}</p></div><div className="environment-value"><strong>{variable.secret ? variable.status === "configured" ? "Configured · redacted" : variable.status.replaceAll("_", " ") : variable.value ?? variable.status.replaceAll("_", " ")}</strong><small>{variable.editable ? "Settings override available" : variable.restart_required ? "Restart required" : "Applies immediately"}</small></div></div>)}</div></section>

      {message && <p className="notice settings-feedback" role="status">{message}</p>}{error && <p className="error settings-feedback" role="alert">{error}</p>}
    </div>
  </div></Shell>;
}
