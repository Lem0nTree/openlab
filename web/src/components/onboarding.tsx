"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, type Job, type LabSettings, type McpIntegration, type ProviderConfig, type SettingsOverview } from "@/lib/api";
import { connectOnboardingProvider, initialSetupStep, openRouterEndpoint, openRouterFreeModel, readinessLabel, readinessStep, setupSteps, type InstallationOverview, type InstallationPolicy, type NetworkSettings, type OnboardingState } from "@/lib/onboarding";
import styles from "./onboarding.module.css";

async function loadSetup() {
  const [next, settings, install, integration] = await Promise.all([
    api<OnboardingState>("/onboarding"), api<SettingsOverview>("/settings"),
    api<InstallationOverview>("/settings/installation"),
    api<McpIntegration>("/integrations/mcp"),
  ]);
  return { next, settings, install, integration };
}

export function Onboarding() {
  const [state, setState] = useState<OnboardingState | null>(null);
  const [overview, setOverview] = useState<SettingsOverview | null>(null);
  const [installation, setInstallation] = useState<InstallationOverview | null>(null);
  const [mcp, setMcp] = useState<McpIntegration | null>(null);
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
  const [models, setModels] = useState<string[]>([]);
  const [kicad, setKicad] = useState("");
  const [installGuide, setInstallGuide] = useState<"kicad" | "tailscale" | null>(null);
  const running = useRef(false);
  const aiDirty = endpoint !== (provider?.base_url ?? "") || model !== (provider?.model ?? "") || !!key;
  const aiKeyStored = provider?.has_api_key && provider.base_url === endpoint;

  const refresh = useCallback(async () => {
    const { next, settings, install, integration } = await loadSetup();
    setState(next); setOverview(settings); setInstallation(install);
    setMcp(integration);
    return { next, settings, install };
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([loadSetup(), api<ProviderConfig | null>("/ai/provider")])
      .then(([{ next, settings, install, integration }, ai]) => {
        if (cancelled) return;
        setState(next); setOverview(settings); setInstallation(install);
        setStep(initialSetupStep(next)); setName(settings.lab.name); setUnits(settings.lab.units);
        setUrl(next.network.public_url ?? window.location.origin); setPolicy(install.policy);
        setKicad(settings.kicad.cli_path ?? ""); setProvider(ai);
        setEndpoint(ai?.base_url ?? ""); setModel(ai?.model ?? ""); setMcp(integration);
      }).catch((failure: Error) => { if (!cancelled) setError(failure.message); });
    return () => { cancelled = true; };
  }, [refresh]);

  useEffect(() => {
    if (step !== readinessStep && step !== 4 && step !== 5) return;
    const timer = window.setInterval(() => { void refresh().catch((failure: Error) => setError(failure.message)); }, 10000);
    return () => window.clearInterval(timer);
  }, [step, refresh]);

  async function run(action: () => Promise<void>) {
    if (running.current) return;
    running.current = true;
    setBusy(true); setError(""); setMessage("");
    try { await action(); await refresh(); }
    catch (failure) { setError((failure as Error).message); }
    finally { running.current = false; setBusy(false); }
  }

  function submit(event: FormEvent, action: () => Promise<void>) {
    event.preventDefault(); void run(action);
  }

  async function connectAI() {
    const listed = await connectOnboardingProvider({ endpoint, model, key }, provider, (saved) => {
      setProvider(saved); setKey(""); setEndpoint(saved.base_url); setModel(saved.model);
    });
    setModels(listed);
    await refresh();
    setMessage("AI settings saved and enabled. Model listing passed; generation and model permissions have not been tested.");
    setStep(3);
  }

  async function skipAI() {
    // Skip is explicit and persists disabled processing; never save partial inputs.
    if (provider) {
      const saved = await api<ProviderConfig>("/ai/provider", { method: "PUT", body: JSON.stringify({
        base_url: provider.base_url, model: provider.model, enabled: false,
        embedding_model: provider.embedding_model, embeddings_enabled: false,
      }) });
      setProvider(saved); setEndpoint(saved.base_url); setModel(saved.model);
    } else { setEndpoint(""); setModel(""); }
    setKey(""); await refresh(); setStep(3);
    setMessage("AI skipped. Automatic AI processing is disabled; you can connect later in Settings.");
  }

  function choosePreset(value: string) {
    setEndpoint(value); setModels([]); setKey(""); setError(""); setMessage("");
    setModel(value === openRouterEndpoint ? openRouterFreeModel : "");
  }

  function navigate(index: number) {
    if (step === 2 && index !== 2 && aiDirty) {
      setError("Your AI changes are not saved. Choose Connect and continue, or Skip AI to discard them.");
      return;
    }
    setStep(index); setError(""); setMessage(""); setInstallGuide(null);
  }

  async function copy(value: string) {
    if (!navigator.clipboard) throw new Error("Clipboard access is unavailable on this address. Select and copy the displayed text manually.");
    await navigator.clipboard.writeText(value);
    setMessage("Copied. Follow the instructions below to finish connecting.");
  }

  async function checkKicad() {
    const cli = kicad.trim() || "kicad-cli";
    await api("/settings/kicad", { method: "PUT", body: JSON.stringify({ cli_path: cli }) });
    setKicad(cli);
    let job = await api<Job>("/settings/kicad/check", { method: "POST" });
    for (let attempt = 0; attempt < 30 && ["queued", "running"].includes(job.status); attempt++) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      job = await api<Job>(`/jobs/${job.id}`);
    }
    setMessage(job.status === "completed" && job.result?.status === "available"
      ? `KiCad detected: ${String(job.result.version ?? "available")}`
      : ["queued", "running"].includes(job.status) ? "Still checking. Refresh readiness later; this optional check does not block setup."
      : "KiCad is unavailable. Use the install guide to add it inside the worker container, then check again.");
  }

  return <main className={styles.page}>
    <header className={styles.header}><Link href="/" className="brand">OpenLab</Link><Link href="/settings">Settings</Link></header>
    <div className={styles.intro}><p className="eyebrow">YOUR LAB. YOUR SERVER. YOUR DATA.</p><h1>Let’s get your lab ready.</h1><p>Set up the essentials, then choose your optional tools. AI, KiCad, remote access, and Product MCP can all be added later.</p></div>
    <nav className={styles.steps} aria-label="Setup steps">{setupSteps.map((label, index) => <button type="button" key={label} aria-current={step === index ? "step" : undefined} disabled={busy || !state} onClick={() => navigate(index)}><span>{index + 1}</span>{label}</button>)}</nav>
    <p className={styles.progress}>Step {step + 1} of {setupSteps.length} · {setupSteps[step]}</p>
    {error && <p className={styles.error} role="alert">{error} <button type="button" disabled={busy} onClick={() => void run(async () => { await refresh(); })}>Retry checks</button></p>}
    {message && <p className={styles.message} role="status">{message}</p>}
    {!state ? <section className={styles.card}><h2>Checking your installation…</h2><p>Waiting for authenticated setup and service diagnostics.</p></section> : <section className={styles.card} aria-busy={busy}>
      {step === 0 && <><p className="eyebrow">01 · LAB</p><h2>A home for your components.</h2><form className="settings-form" onSubmit={(event) => submit(event, async () => {
        await api<LabSettings>("/settings/lab", { method: "PUT", body: JSON.stringify({ name, units }) }); setStep(1);
      })}><label>Lab name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={200} required /></label><label>Measurement system<select value={units} onChange={(event) => setUnits(event.target.value as "metric" | "imperial")}><option value="metric">Metric</option><option value="imperial">Imperial</option></select></label><button disabled={busy}>Save and continue</button></form></>}
      {step === 1 && <><p className="eyebrow">02 · NETWORK</p><h2>One reliable address.</h2><p>This address is used on your drawer QR labels. Use a stable LAN hostname or your Tailscale address. Do not expose this HTTP port to the public internet.</p><form className="settings-form" onSubmit={(event) => submit(event, async () => {
        const saved = await api<NetworkSettings>("/settings/network", { method: "PUT", body: JSON.stringify({ public_url: url }) });
        if (saved.verified) { setMessage("This browser verified the canonical address."); setStep(2); }
        else setMessage("Address saved but not verified. Open that address, sign in, and save it again from there.");
      })}><label>Canonical address<input type="url" value={url} onChange={(event) => setUrl(event.target.value)} maxLength={600} required placeholder="http://openlab.local:3000" /></label><div className={styles.actions}><button disabled={busy}>Save and verify</button><button type="button" className="secondary-button" onClick={() => setUrl(window.location.origin)}>Use this browser’s address</button></div></form><p className={styles.note}>Verification stays between your browser and OpenLab. The server does not fetch arbitrary addresses.</p></>}
      {step === 2 && <>
        <p className="eyebrow">03 · OPTIONAL AI</p><h2>Bring your own intelligence.</h2>
        <p>Connect a provider to enable AI processing. With a hosted provider, captured content may leave your server. This connection check lists models only and does not send inventory.</p>
        <aside className={styles.callout}>
          <div className={styles.calloutHeading}><strong>Try OpenRouter</strong><span className={styles.freeBadge}>Free models</span></div>
          <p>Start with <code>openrouter/free</code>, which routes requests to available free models. Bring an OpenRouter API key. Availability, capabilities, and rate limits vary.</p>
          <div className={styles.actions}><button type="button" disabled={busy} onClick={() => choosePreset(openRouterEndpoint)}>Try with OpenRouter</button><a href="https://openrouter.ai/settings/keys" target="_blank" rel="noreferrer">Get an API key ↗</a><a href="https://openrouter.ai/openrouter/free" target="_blank" rel="noreferrer">About free models ↗</a></div>
        </aside>
        <form className="settings-form" onSubmit={(event) => submit(event, connectAI)}>
          <fieldset disabled={busy} className={styles.fields}>
            <label>Provider preset<select value={[openRouterEndpoint, "http://host.docker.internal:11434/v1", "https://api.openai.com/v1"].includes(endpoint) ? endpoint : ""} onChange={(event) => choosePreset(event.target.value)}><option value="">Custom endpoint</option><option value="http://host.docker.internal:11434/v1">Ollama on the Docker host</option><option value={openRouterEndpoint}>OpenRouter · Free models</option><option value="https://api.openai.com/v1">OpenAI</option></select></label>
            <label>Endpoint URL<input type="url" value={endpoint} onChange={(event) => { setEndpoint(event.target.value); setKey(""); }} required /></label>
            <label>Model ID<input value={model} onChange={(event) => setModel(event.target.value)} list="onboarding-models" required /><datalist id="onboarding-models">{models.map((item) => <option key={item} value={item} />)}</datalist></label>
            <label>API key<input type="password" autoComplete="off" value={key} onChange={(event) => setKey(event.target.value)} required={endpoint === openRouterEndpoint && !aiKeyStored} placeholder={aiKeyStored ? "Stored securely; leave blank to keep" : "Required for hosted providers"} /></label>
          </fieldset>
          <p className={styles.note}>Connect saves these settings, enables processing, and checks the endpoint before continuing. A model list does not verify generation or model permissions.</p>
          <div className={styles.actions}><button disabled={busy}>{busy ? "Connecting…" : "Connect and continue"}</button><button type="button" className="secondary-button" disabled={busy} onClick={() => void run(skipAI)}>Skip AI</button></div>
        </form>
        <p className={styles.note}>{aiDirty ? "Unsaved changes — connect to save them, or skip to discard them." : state.readiness.checks.find((check) => check.id === "ai")?.summary}</p>
        <p className={styles.note}>For Ollama on Linux, allow connections from the Docker network; localhost inside a container is not the host.</p>
      </>}
      {step === 3 && <>
        <p className="eyebrow">04 · OPTIONAL KICAD</p><h2>Check your wired connections.</h2>
        <p>KiCad is required for electrical rules checks on schematic wiring. It can flag connection issues in the design; it cannot inspect physical wires or prove a circuit is safe. You can use inventory without it.</p>
        <aside className={styles.callout}><strong>Install where OpenLab runs its checks</strong><p>KiCad CLI must be inside the worker container, not just on your computer. The standard image keeps it optional.</p>
          <button type="button" disabled={busy} aria-expanded={installGuide === "kicad"} aria-controls="kicad-install" onClick={() => setInstallGuide(installGuide === "kicad" ? null : "kicad")}>Install KiCad and connect…</button>
        </aside>
        {installGuide === "kicad" && <div id="kicad-install" className={styles.guide}>
          <h3>Install on the OpenLab host</h3>
          {installation?.managed ? <p>This signed-release installation needs a worker image containing KiCad. Ask your installation administrator to supply a supported image; installing on the host alone will not connect it. The web app cannot change signed release images.</p> : <><ol><li>In the OpenLab source checkout on your Linux host, set <code>OPENLAB_INSTALL_KICAD=1</code> in the root <code>.env</code> file.</li><li>Run this command from that checkout. It installs KiCad in a rebuilt worker image and restarts the worker; downloads may take several minutes.</li></ol><code className={styles.command}>sh deploy/up.sh --build --no-deps -d openlab-worker</code><div className={styles.actions}><button type="button" className="secondary-button" disabled={busy} onClick={() => void run(() => copy("sh deploy/up.sh --build --no-deps -d openlab-worker"))}>Copy installation command</button></div></>}
          <p>After installation finishes, use <strong>Connect KiCad</strong> below to save the executable and check its version. Opening this guide does not run commands on the host.</p>
        </div>}
        <form className="settings-form" onSubmit={(event) => submit(event, checkKicad)}><label>Worker executable<input disabled={busy} value={kicad} onChange={(event) => setKicad(event.target.value)} placeholder="kicad-cli" maxLength={500} /></label><div className={styles.actions}><button disabled={busy}>{busy ? "Checking…" : "Connect KiCad"}</button><button type="button" className="secondary-button" disabled={busy} onClick={() => navigate(4)}>{overview?.kicad.check_status === "available" ? "Continue" : "Skip for now"}</button></div></form>
        <p className={styles.note}>Worker status: {overview?.kicad.check_status?.replaceAll("_", " ") ?? "not checked"}{overview?.kicad.version ? ` · ${overview.kicad.version}` : ""}</p>
      </>}
      {step === 4 && <><p className="eyebrow">05 · ACCESS & UPDATES</p><h2>Your lab, wherever you are.</h2><h3>Optional Tailscale</h3>
        <p>Tailscale gives your devices private remote access to OpenLab. Install it on the lab host and the devices you want to connect. No public port forwarding is needed.</p>
        <p className={styles.status}>Host status: <strong>{installation?.status_stale ? "Unknown — installer status is stale" : installation?.status?.tailscale.replaceAll("_", " ") ?? "Not reported by this installation"}</strong></p>
        <div className={styles.actions}><button type="button" disabled={busy} aria-expanded={installGuide === "tailscale"} aria-controls="tailscale-install" onClick={() => setInstallGuide(installGuide === "tailscale" ? null : "tailscale")}>Install Tailscale and connect…</button><button type="button" className="secondary-button" disabled={busy} onClick={() => { setInstallGuide(null); setMessage("Tailscale setup skipped; any existing connection is unchanged. Review security updates below, then continue."); document.getElementById("security-updates")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>Skip Tailscale</button></div>
        {installGuide === "tailscale" && <div id="tailscale-install" className={styles.guide}>
          <h3>Install and authorize on the host</h3>
          {installation?.managed ? <><p>Run this command in a terminal on your OpenLab host, or ask your connected installer MCP to configure Tailscale with dependency installation enabled.</p><code className={styles.command}>openlabctl network tailscale --install-deps</code><div className={styles.actions}><button type="button" className="secondary-button" disabled={busy} onClick={() => void run(() => copy("openlabctl network tailscale --install-deps"))}>Copy installation command</button></div><p>Open the authorization URL returned in the terminal and sign in yourself. Then rerun the command to connect OpenLab to your tailnet.</p></> : <><p>This installation is not managed by openlabctl. Open Tailscale’s installer for your host, install it there, then run <code>sudo tailscale up</code> on Linux and follow its sign-in link.</p><a className={styles.buttonLink} href="https://tailscale.com/download" target="_blank" rel="noreferrer">Open Tailscale installer ↗</a><p>Visit OpenLab using the host’s Tailscale address and your existing port. This wizard cannot verify unmanaged host status.</p></>}
          <p>The browser cannot install host software directly. Complete the terminal authorization first, then refresh. Never paste authorization links or credentials into chat.</p>
          <div className={styles.actions}><button type="button" disabled={busy} onClick={() => void run(async () => { const { install } = await refresh(); setMessage(!install.status_stale && install.status?.tailscale === "connected" ? "The installer reports Tailscale connected. Verify access from your other device." : "Connection is not verified yet. Complete the host installation and authorization first."); })}>Check Tailscale connection</button><button type="button" className="secondary-button" disabled={busy} onClick={() => navigate(1)}>Update my lab address</button></div>
        </div>}
        <h3 id="security-updates">Security updates</h3>
        {installation?.managed ? <form className="settings-form" onSubmit={(event) => submit(event, async () => { await api("/settings/installation", { method: "PUT", body: JSON.stringify(policy) }); setStep(5); })}>
          <label className="check-row"><input type="checkbox" checked={policy.security_updates} onChange={(event) => setPolicy({ ...policy, security_updates: event.target.checked })} />Install eligible signed security releases automatically</label>
          <label>Maintenance day (server local time)<select value={policy.weekday} onChange={(event) => setPolicy({ ...policy, weekday: Number(event.target.value) })}>{["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label>
          <div className={styles.time}><label>Hour<input type="number" min={0} max={23} value={policy.hour} onChange={(event) => setPolicy({ ...policy, hour: Number(event.target.value) })} required /></label><label>Minute<input type="number" min={0} max={59} value={policy.minute} onChange={(event) => setPolicy({ ...policy, minute: Number(event.target.value) })} required /></label></div>
          <p>Updates require a backup, verified release, compatible migrations, and passing readiness. Feature releases stay manual.</p><button disabled={busy}>Save and continue to Product MCP</button>
        </form> : <><p>Automatic updates require an installation managed by openlabctl. Existing Compose installations stay unchanged.</p><div className={styles.actions}><button type="button" disabled={busy} onClick={() => navigate(5)}>Continue to Product MCP</button></div></>}
        {installation?.status && <p className={styles.note}>Latest update: {installation.status.update_status.replaceAll("_", " ")}{installation.status_stale ? " · status is stale" : ""}</p>}
      </>}
      {step === 5 && <>
        <p className="eyebrow">06 · OPTIONAL PRODUCT MCP</p><h2>Bring your lab into your AI tools.</h2>
        <p>Product MCP lets an approved AI client work with your lab records. It is separate from the installer MCP: it does not install software, run shell commands, or expose provider keys. You choose permissions during authorization.</p>
        <p className={styles.status}>Status: <strong>{!mcp?.enabled ? "Disabled" : mcp.grants?.some((grant) => grant.last_used_at) ? "Enabled · client use recorded" : mcp.grants?.length ? "Enabled · client authorized, not yet used" : "Enabled · waiting for client authorization"}</strong></p>
        <div className={styles.actions}><button type="button" disabled={busy || !!mcp?.enabled} onClick={() => void run(async () => { setMcp(await api<McpIntegration>("/integrations/mcp", { method: "PUT", body: JSON.stringify({ enabled: true }) })); setMessage("Product MCP enabled. Add the endpoint in your AI client, then authorize it. Enabling alone does not connect a client."); })}>{mcp?.enabled ? "Product MCP enabled" : "Enable Product MCP"}</button><button type="button" className="secondary-button" disabled={busy} onClick={() => void run(async () => { setMcp(await api<McpIntegration>("/integrations/mcp")); })}>Check client connection</button></div>
        {mcp?.direct_http_ready && mcp.mcp_url ? <div className={styles.guide}><h3>Connect your AI client</h3><ol><li>Add a remote MCP server in a client that supports Streamable HTTP and OAuth.</li><li>Use this server URL, then sign in to OpenLab in the authorization window.</li><li>Review the requested scopes and approve only the access you want to grant.</li></ol><code className={styles.command}>{mcp.mcp_url}</code><button type="button" className="secondary-button" disabled={busy} onClick={() => void run(() => copy(mcp.mcp_url!))}>Copy MCP server URL</button></div> : <aside className={styles.callout}><strong>HTTPS is needed for a direct connection</strong><p>Your current lab address is not eligible for direct MCP transport. Configure HTTPS for the lab first, then update its address in the Network step. Do not expose the plain HTTP application port to the internet.</p><button type="button" className="secondary-button" disabled={busy} onClick={() => navigate(1)}>Review network address</button></aside>}
        {!!mcp?.grants?.length && <ul className={styles.grants}>{mcp.grants?.map((grant) => <li key={grant.id}><strong>{grant.client_name}</strong><span>{grant.scopes.join(", ")} · {grant.last_used_at ? `Last used ${new Date(grant.last_used_at).toLocaleString()}` : "Authorized, not yet used"}</span></li>)}</ul>}
        <p className={styles.note}>Manage permissions or revoke clients in <Link href="/settings#mcp">Settings → MCP integrations</Link>. Skipping leaves your current MCP settings unchanged.</p>
        <div className={styles.actions}><button type="button" disabled={busy} onClick={() => navigate(readinessStep)}>{mcp?.enabled ? "Continue to readiness" : "Skip for now"}</button></div>
      </>}
      {step === readinessStep && <><p className="eyebrow">07 · READINESS</p><h2>{readinessLabel(state.readiness)}</h2><p>Release: {state.readiness.version}. Last checked: {new Date(state.readiness.checked_at).toLocaleTimeString()}. Checks refresh every 10 seconds.</p>
        <ul className={styles.checks}>{state.readiness.checks.map((check) => <li key={check.id} data-status={check.status}><span className={styles.badge}>{check.status === "pass" ? "✓" : check.status === "pending" ? "…" : "!"}</span><div><h3>{check.label} <small>{check.required ? "Required" : "Optional"}</small></h3><p>{check.summary}</p>{check.remediation && <p className={styles.fix}>{check.remediation}</p>}{check.code !== "OK" && <small>{check.code}</small>}</div></li>)}</ul>
        <div className={styles.actions}><button type="button" disabled={busy || state.readiness.overall === "blocked"} onClick={() => void run(async () => { await api("/onboarding/complete", { method: "POST" }); window.location.replace("/"); })}>Finish and open my lab</button><button type="button" className="secondary-button" disabled={busy} onClick={() => void run(async () => { await refresh(); })}>Check again</button></div>
        <p className={styles.note}>Optional warnings do not block your lab. You can revisit this guide from Settings at any time.</p>
      </>}
    </section>}
    <footer className={styles.footer}>Need help? Run <code>openlabctl doctor</code> or ask your installer MCP to inspect the installation. Diagnostics redact secrets.</footer>
  </main>;
}
