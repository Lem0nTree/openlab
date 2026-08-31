"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { components } from "@/lib/openapi";
import styles from "./onboarding.module.css";

type HostStatus = components["schemas"]["HostSetupOut"];
type Operation = components["schemas"]["HostSetupOperation"];
type Action = components["schemas"]["HostSetupInput"]["action"];

const tailnetLabels: Record<string, string> = {
  connected: "Connected to Tailscale", not_installed: "Tailscale is not installed",
  needs_authorization: "Tailscale needs sign-in or device approval", unavailable: "Host connection not verified",
};

export function OnboardingHostSetup({ kind, managed, onConnected }: {
  kind: "kicad" | "tailscale" | "https";
  managed: boolean;
  onConnected?: () => Promise<void>;
}) {
  const [host, setHost] = useState<HostStatus | null>(null);
  const [pending, setPending] = useState<Operation | null>(null);
  const [error, setError] = useState("");
  const [waiting, setWaiting] = useState(false);
  const alive = useRef(true);
  const starting = useRef(false);
  useEffect(() => {
    alive.current = true;
    const refresh = () => api<HostStatus>("/settings/host-setup").then((next) => {
      if (alive.current) setHost(next);
    }).catch(() => { if (alive.current) setHost({ available: false, tailscale: "unavailable", kicad_supported: false }); });
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { alive.current = false; window.clearInterval(timer); };
  }, []);
  const operation = pending && host?.operation?.id !== pending.id ? pending : host?.operation;
  const busy = waiting || (host?.available && operation?.status === "running");
  const ownOperation = operation?.action === kind || operation?.action === "refresh";
  async function start(action: Action) {
    if (starting.current || busy) return;
    starting.current = true; setWaiting(true); setError("");
    try {
      const requested = await api<Operation>("/settings/host-setup", { method: "POST", body: JSON.stringify({ action }) });
      if (!alive.current) return;
      setPending(requested);
      for (let attempt = 0; attempt < 600 && alive.current; attempt++) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        if (!alive.current) return;
        let next: HostStatus;
        try { next = await api<HostStatus>("/settings/host-setup"); }
        catch {
          // Connecting Tailscale briefly recreates the web container. Keep the
          // original request ID; never enqueue another install on a network error.
          if (alive.current) setPending({ ...requested, message: "Waiting for the web app to reconnect…" });
          continue;
        }
        if (!alive.current) return;
        setHost(next);
        const result = next.operation;
        if (result?.id !== requested.id) continue;
        if (result.status === "failed") throw new Error(result.message);
        if (result.status === "completed") {
          setPending(null);
          if (action === "kicad") await onConnected?.();
          return;
        }
      }
      throw new Error("The host has not finished yet. Check its status before retrying; installation may still be running.");
    } catch (failure) {
      if (alive.current) { setError((failure as Error).message); setPending(null); }
    } finally { starting.current = false; if (alive.current) setWaiting(false); }
  }
  const title = kind === "kicad" ? "Install the KiCad worker" : kind === "https" ? "Enable private HTTPS" : "Connect your existing Tailscale host";
  const label = kind === "kicad" ? "Install KiCad & connect" : kind === "https" ? "Enable HTTPS with Tailscale" : host?.tailscale === "connected" ? "Connect OpenLab to Tailscale" : "Install / connect Tailscale";
  return <div className={styles.toolPanel}>
    <div className={styles.toolHeading}><h3>{title}</h3><span className={styles.optionalBadge}>Optional</span></div>
    <p>{kind === "kicad" ? "Downloads the release’s verified KiCad worker, restarts only that worker, and checks the executable. No build on your Pi and no change to your inventory." : kind === "https" ? "Tailscale Serve supplies a trusted HTTPS certificate for your private lab address. Your lab stays inside your tailnet; public Funnel is never enabled." : "Checks Tailscale on the Pi itself. An existing installation is reused; OpenLab installs it only when it is missing."}</p>
    {kind !== "kicad" && <p className={styles.status} role="status"><span className={styles.statusDot} data-connected={host?.available && host.tailscale === "connected"} />{host === null ? "Checking the host…" : tailnetLabels[host.available ? host.tailscale ?? "unavailable" : "unavailable"]}</p>}
    {host?.available ? <>
      <div className={styles.actions}>
        <button type="button" disabled={!!busy || (kind === "kicad" && !host.kicad_supported)} onClick={() => void start(kind)}>{busy && ownOperation ? <><span className={styles.spinner} aria-hidden="true" />Working…</> : label}</button>
        {kind !== "kicad" && <button type="button" className="secondary-button" disabled={!!busy} onClick={() => void start("refresh")}>Refresh host status</button>}
      </div>
      {kind === "kicad" && !host.kicad_supported && <p className={styles.note}>This older release has no signed KiCad worker. Upgrade through the signed installer to enable this button.</p>}
      {operation && ownOperation && <p className={styles.operation} role="status">{operation.message}</p>}
    </> : host !== null && <p className={styles.note}>{managed ? "The host setup service is unavailable or out of date. Run the current signed OpenLab installer on the Pi once to enable these actions. Existing host tools are left unchanged." : "Automatic host setup requires the signed OpenLab installer. Source installations can use the manual instructions below."}</p>}
    {error && <p className={styles.error} role="alert">{error}</p>}
    {kind === "https" && <>
      <p className={styles.note}>You may need to allow HTTPS in <a href="https://login.tailscale.com/admin/dns" target="_blank" rel="noreferrer">Tailscale DNS settings</a>. Certificate issuance publishes your machine’s DNS name in certificate transparency logs.</p>
      {host?.operation?.action === "https" && host.operation.status === "completed" && host.operation.url && <div className={styles.guide}><strong>Your secure address is ready</strong><p>Open this address, sign in, then choose “Use this HTTPS address” in the Product MCP step.</p><a className={styles.buttonLink} href={`${host.operation.url}/onboarding`}>Open secure setup ↗</a></div>}
      <p className={styles.note}>Only clients that can reach your tailnet can connect. A cloud MCP client may require a separately configured public HTTPS endpoint.</p>
    </>}
    {kind === "tailscale" && <p className={styles.note}>If sign-in is required, run <code>sudo tailscale up</code> in your Pi terminal and approve the device. Login links and credentials never enter this page.</p>}
  </div>;
}
