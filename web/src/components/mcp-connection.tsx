"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { api, type McpIntegration } from "@/lib/api";
import type { InstallationOverview, NetworkSettings } from "@/lib/onboarding";
import { OnboardingHostSetup } from "./onboarding-host-setup";
import styles from "./onboarding.module.css";

const subscribeToOrigin = () => () => {};

export function McpConnection({ integration, onChange, returnTo = "/settings#mcp" }: {
  integration: McpIntegration | null;
  onChange: (value: McpIntegration) => void;
  returnTo?: "/settings#mcp" | "/onboarding";
}) {
  const [managed, setManaged] = useState(false);
  const secure = useSyncExternalStore(subscribeToOrigin, () => window.location.protocol === "https:", () => false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void api<InstallationOverview>("/settings/installation").then(value => { if (active) setManaged(value.managed); }).catch(() => {});
    return () => { active = false; };
  }, []);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setMessage("");
    try { await action(); } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  const endpoint = integration?.direct_http_ready ? integration.mcp_url : null;
  const instructions = endpoint ? `Connect to my OpenLab MCP server at ${endpoint} using Streamable HTTP and OAuth. Open the authorization page so I can sign in and approve permissions. Start with read-only access. Do not request my password, API keys, or tokens in chat. This address may be private: your MCP runtime must have network access to my lab.` : "";
  async function copy(value: string) {
    await navigator.clipboard.writeText(value);
    setMessage("Copied to clipboard.");
  }
  return <div className={styles.toolPanel}>
    <h3>Connect an AI client</h3>
    <p role="status">{!integration ? "Loading MCP status…" : !integration.enabled ? "MCP is disabled. You can prepare HTTPS now, then enable MCP to authorize a client." : integration.grants?.some(grant => grant.last_used_at) ? "Client use recorded" : integration.grants?.length ? "Client authorized · not yet used" : "Enabled · waiting for client authorization"}</p>
    {endpoint ? <>
      <label>MCP server address<code className={styles.command}>{endpoint}</code></label>
      <div className={styles.actions}><button type="button" disabled={busy} onClick={() => void run(() => copy(endpoint))}>Copy MCP server URL</button></div>
      <ol><li>Add a remote MCP server in a client supporting Streamable HTTP and OAuth.</li><li>Paste the server address and sign in to OpenLab in the authorization window.</li><li>Review and approve the requested permissions. Start with read-only access.</li></ol>
      <details className={styles.guide}><summary>What to send to your AI harness</summary><p>{instructions}</p><button type="button" disabled={busy} onClick={() => void run(() => copy(instructions))}>Copy harness instructions</button><p className={styles.note}>A message alone does not configure every client. Some require adding the server manually in their MCP settings. A cloud client cannot necessarily reach a private Tailscale address.</p></details>
    </> : <>
      <p>Network MCP requires a verified HTTPS address. You can finish setup here even if you skipped it during onboarding.</p>
      <OnboardingHostSetup kind="https" managed={managed} returnTo={returnTo} />
      {secure ? <button type="button" disabled={busy} onClick={() => void run(async () => {
        const saved = await api<NetworkSettings>("/settings/network", { method: "PUT", body: JSON.stringify({ public_url: window.location.origin }) });
        if (!saved.verified) throw new Error("The HTTPS address could not be verified. Check the reverse proxy and try again.");
        onChange(await api<McpIntegration>("/integrations/mcp"));
        setMessage("HTTPS address verified.");
      })}>Use this HTTPS address</button> : <p className={styles.note}>After enabling HTTPS, open the secure setup link and sign in again. Then choose “Use this HTTPS address”.</p>}
      <details className={styles.guide}><summary>Use another HTTPS address</summary><p>If you already have a trusted HTTPS reverse proxy for OpenLab, open Settings through that address and choose “Use this HTTPS address”. Plain HTTP and an untrusted self-signed certificate are not supported by this connection flow.</p></details>
    </>}
    <div className={styles.actions}><button className="secondary-button" type="button" disabled={busy || !integration} onClick={() => void run(async () => { onChange(await api<McpIntegration>("/integrations/mcp")); setMessage("Client status refreshed. This checks existing authorizations; it does not connect a client."); })}>Check client connection</button></div>
    {message && <p role="status">{message}</p>}{error && <p className={styles.error} role="alert">{error}</p>}
  </div>;
}
