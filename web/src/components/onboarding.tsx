"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type Job, type LabSettings, type ProviderConfig, type SettingsOverview } from "@/lib/api";
import { initialSetupStep, readinessLabel, setupSteps, type InstallationOverview, type InstallationPolicy, type NetworkSettings, type OnboardingState } from "@/lib/onboarding";
import styles from "./onboarding.module.css";

async function loadSetup() {
  const [next, settings, install] = await Promise.all([
    api<OnboardingState>("/onboarding"), api<SettingsOverview>("/settings"),
    api<InstallationOverview>("/settings/installation"),
  ]);
  return { next, settings, install };
}

export function Onboarding() {
  const [state, setState] = useState<OnboardingState | null>(null);
  const [overview, setOverview] = useState<SettingsOverview | null>(null);
  const [installation, setInstallation] = useState<InstallationOverview | null>(null);
  const [policy, setPolicy] = useState<InstallationPolicy>({ security_updates: true, weekday: 0, hour: 3, minute: 0 });
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [name, setName] = useState("");
  const [units, setUnits] = useState<"metric" | "imperial">("metric");
  const [url, setUrl] = useState("");
  const [provider, setProvider] = useState<ProviderConfig | null>(null);
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [kicad, setKicad] = useState("");

  const refresh = useCallback(async () => {
    const { next, settings, install } = await loadSetup();
    setState(next); setOverview(settings); setInstallation(install);
    return { next, settings, install };
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([loadSetup(), api<ProviderConfig | null>("/ai/provider")])
      .then(([{ next, settings, install }, ai]) => {
        if (cancelled) return;
        setState(next); setOverview(settings); setInstallation(install);
        setStep(initialSetupStep(next)); setName(settings.lab.name); setUnits(settings.lab.units);
        setUrl(next.network.public_url ?? window.location.origin); setPolicy(install.policy);
        setKicad(settings.kicad.cli_path ?? ""); setProvider(ai);
        setEndpoint(ai?.base_url ?? ""); setModel(ai?.model ?? ""); setEnabled(ai?.enabled ?? false);
      }).catch((failure: Error) => { if (!cancelled) setError(failure.message); });
    return () => { cancelled = true; };
  }, [refresh]);

  useEffect(() => {
    if (step !== 5) return;
    const timer = window.setInterval(() => { void refresh().catch((failure: Error) => setError(failure.message)); }, 10000);
    return () => window.clearInterval(timer);
  }, [step, refresh]);

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try { await action(); await refresh(); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }

  function submit(event: FormEvent, action: () => Promise<void>) {
    event.preventDefault(); void run(action);
  }

  async function saveProvider(test: boolean) {
    const payload: Record<string, unknown> = {
      base_url: endpoint, model, enabled,
      embedding_model: provider?.embedding_model ?? null,
      embeddings_enabled: provider?.embeddings_enabled ?? false,
    };
    if (key) payload.api_key = key;
    const saved = await api<ProviderConfig>("/ai/provider", { method: "PUT", body: JSON.stringify(payload) });
    setProvider(saved); setKey("");
    if (test) {
      const result = await api<{ models: string[] }>("/ai/provider/models");
      setModels(result.models);
      setMessage(`Connection verified: ${result.models.length} models listed. This does not prove vision, audio, or generation support.`);
    } else setMessage("AI settings saved. You can test the endpoint or continue.");
  }

  async function checkKicad() {
    await api("/settings/kicad", { method: "PUT", body: JSON.stringify({ cli_path: kicad.trim() || null }) });
    let job = await api<Job>("/settings/kicad/check", { method: "POST" });
    for (let attempt = 0; attempt < 30 && ["queued", "running"].includes(job.status); attempt++) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      job = await api<Job>(`/jobs/${job.id}`);
    }
    setMessage(job.status === "completed" && job.result?.status === "available"
      ? `KiCad detected: ${String(job.result.version ?? "available")}`
      : ["queued", "running"].includes(job.status) ? "Still checking. Refresh readiness later; this optional check does not block setup."
      : "KiCad is unavailable. Confirm the binary is installed inside the worker container.");
  }

  return <main className={styles.page}>
    <header className={styles.header}><Link href="/" className="brand">OpenLab</Link><Link href="/settings">Settings</Link></header>
    <div className={styles.intro}><p className="eyebrow">YOUR LAB. YOUR SERVER. YOUR DATA.</p><h1>Let’s get your lab ready.</h1><p>Core services are required. AI, KiCad, and remote access are optional. Saved settings survive restarts.</p></div>
    <nav className={styles.steps} aria-label="Setup steps">{setupSteps.map((label, index) => <button type="button" key={label} aria-current={step === index ? "step" : undefined} disabled={busy || !state} onClick={() => { setStep(index); setError(""); setMessage(""); }}><span>{index + 1}</span>{label}</button>)}</nav>
    {error && <p className={styles.error} role="alert">{error} <button type="button" disabled={busy} onClick={() => void run(async () => { await refresh(); })}>Retry checks</button></p>}
    {message && <p className={styles.message} role="status">{message}</p>}
    {!state ? <section className={styles.card}><h2>Checking your installation…</h2><p>Waiting for authenticated setup and service diagnostics.</p></section> : <section className={styles.card}>
      {step === 0 && <><p className="eyebrow">01 · LAB</p><h2>A home for your components.</h2><form className="settings-form" onSubmit={(event) => submit(event, async () => {
        await api<LabSettings>("/settings/lab", { method: "PUT", body: JSON.stringify({ name, units }) }); setStep(1);
      })}><label>Lab name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={200} required /></label><label>Measurement system<select value={units} onChange={(event) => setUnits(event.target.value as "metric" | "imperial")}><option value="metric">Metric</option><option value="imperial">Imperial</option></select></label><button disabled={busy}>Save and continue</button></form></>}
      {step === 1 && <><p className="eyebrow">02 · NETWORK</p><h2>One reliable address.</h2><p>This address is used on your drawer QR labels. Use a stable LAN hostname or your Tailscale address. Do not expose this HTTP port to the public internet.</p><form className="settings-form" onSubmit={(event) => submit(event, async () => {
        const saved = await api<NetworkSettings>("/settings/network", { method: "PUT", body: JSON.stringify({ public_url: url }) });
        if (saved.verified) { setMessage("This browser verified the canonical address."); setStep(2); }
        else setMessage("Address saved but not verified. Open that address, sign in, and save it again from there.");
      })}><label>Canonical address<input type="url" value={url} onChange={(event) => setUrl(event.target.value)} maxLength={600} required placeholder="http://openlab.local:3000" /></label><div className={styles.actions}><button disabled={busy}>Save and verify</button><button type="button" className="secondary-button" onClick={() => setUrl(window.location.origin)}>Use this browser’s address</button></div></form><p className={styles.note}>Verification stays between your browser and OpenLab. The server does not fetch arbitrary addresses.</p></>}
      {step === 2 && <><p className="eyebrow">03 · OPTIONAL AI</p><h2>Bring your own intelligence.</h2><p>OpenLab works without AI. Enabling a hosted provider allows captured content to leave your server. Testing lists models only; it does not send inventory.</p><form className="settings-form" onSubmit={(event) => submit(event, () => saveProvider(false))}>
        <label>Preset<select defaultValue="" onChange={(event) => { if (event.target.value) setEndpoint(event.target.value); }}><option value="">Choose a preset or use a custom endpoint</option><option value="http://host.docker.internal:11434/v1">Ollama on the Docker host</option><option value="https://openrouter.ai/api/v1">OpenRouter</option><option value="https://api.openai.com/v1">OpenAI</option></select></label>
        <label>Endpoint URL<input type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} required /></label>
        <label>Model ID<input value={model} onChange={(event) => setModel(event.target.value)} list="onboarding-models" required /><datalist id="onboarding-models">{models.map((item) => <option key={item} value={item} />)}</datalist></label>
        <label>API key<input type="password" autoComplete="off" value={key} onChange={(event) => setKey(event.target.value)} placeholder={provider?.has_api_key ? "Stored securely; leave blank to keep" : "Optional for local providers"} /></label>
        <label className="check-row"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />Enable AI processing</label>
        <div className={styles.actions}><button disabled={busy}>Save</button><button type="button" disabled={busy || !endpoint || !model} onClick={() => void run(() => saveProvider(true))}>Save and test connection</button></div>
      </form><p className={styles.note}>For Ollama on Linux, allow connections from the Docker network; localhost inside a container is not the host.</p><button type="button" className="secondary-button" disabled={busy} onClick={() => setStep(3)}>Continue with current settings</button></>}
      {step === 3 && <><p className="eyebrow">04 · OPTIONAL KICAD</p><h2>Check the worker’s tools.</h2><p>KiCad must exist inside the worker container, not just on the host. The lightweight image does not include it.</p><form className="settings-form" onSubmit={(event) => submit(event, checkKicad)}><label>Worker executable<input value={kicad} onChange={(event) => setKicad(event.target.value)} placeholder="kicad-cli" maxLength={500} /></label><button disabled={busy}>{busy ? "Checking…" : "Save and check"}</button></form><p className={styles.note}>Current status: {overview?.kicad.check_status ?? "unknown"}{overview?.kicad.version ? ` · ${overview.kicad.version}` : ""}</p><button type="button" className="secondary-button" disabled={busy} onClick={() => setStep(4)}>Continue</button></>}
      {step === 4 && <><p className="eyebrow">05 · ACCESS & UPDATES</p><h2>Local by default. Reachable when you choose.</h2><h3>Optional Tailscale</h3><p>Status: <strong>{installation?.status?.tailscale.replaceAll("_", " ") ?? "not managed by installer"}</strong></p><p>To enable private remote access, run this on the host or ask your connected installer MCP. Complete the authorization in your browser.</p><code className={styles.command}>openlabctl network tailscale</code><h3>Security updates</h3>
        {installation?.managed ? <form className="settings-form" onSubmit={(event) => submit(event, async () => { await api("/settings/installation", { method: "PUT", body: JSON.stringify(policy) }); setStep(5); })}>
          <label className="check-row"><input type="checkbox" checked={policy.security_updates} onChange={(event) => setPolicy({ ...policy, security_updates: event.target.checked })} />Install eligible signed security releases automatically</label>
          <label>Maintenance day (server local time)<select value={policy.weekday} onChange={(event) => setPolicy({ ...policy, weekday: Number(event.target.value) })}>{["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label>
          <div className={styles.time}><label>Hour<input type="number" min={0} max={23} value={policy.hour} onChange={(event) => setPolicy({ ...policy, hour: Number(event.target.value) })} required /></label><label>Minute<input type="number" min={0} max={59} value={policy.minute} onChange={(event) => setPolicy({ ...policy, minute: Number(event.target.value) })} required /></label></div>
          <p>Updates require a backup, verified release, compatible migrations, and passing readiness. Feature releases stay manual.</p><button disabled={busy}>Save and review readiness</button>
        </form> : <><p>Automatic updates require an installation managed by openlabctl. Existing Compose installations stay unchanged.</p><button type="button" onClick={() => setStep(5)}>Review readiness</button></>}
        {installation?.status && <p className={styles.note}>Latest update: {installation.status.update_status.replaceAll("_", " ")}{installation.status_stale ? " · status is stale" : ""}</p>}
      </>}
      {step === 5 && <><p className="eyebrow">06 · READINESS</p><h2>{readinessLabel(state.readiness)}</h2><p>Release: {state.readiness.version}. Last checked: {new Date(state.readiness.checked_at).toLocaleTimeString()}. Checks refresh every 10 seconds.</p>
        <ul className={styles.checks}>{state.readiness.checks.map((check) => <li key={check.id} data-status={check.status}><span className={styles.badge}>{check.status === "pass" ? "✓" : check.status === "pending" ? "…" : "!"}</span><div><h3>{check.label} <small>{check.required ? "Required" : "Optional"}</small></h3><p>{check.summary}</p>{check.remediation && <p className={styles.fix}>{check.remediation}</p>}{check.code !== "OK" && <small>{check.code}</small>}</div></li>)}</ul>
        <div className={styles.actions}><button type="button" disabled={busy || state.readiness.overall === "blocked"} onClick={() => void run(async () => { await api("/onboarding/complete", { method: "POST" }); window.location.replace("/"); })}>Finish and open my lab</button><button type="button" className="secondary-button" disabled={busy} onClick={() => void run(async () => { await refresh(); })}>Check again</button></div>
        <p className={styles.note}>Optional warnings do not block your lab. You can revisit this guide from Settings at any time.</p>
      </>}
    </section>}
    <footer className={styles.footer}>Need help? Run <code>openlabctl doctor</code> or ask your installer MCP to inspect the installation. Diagnostics redact secrets.</footer>
  </main>;
}
