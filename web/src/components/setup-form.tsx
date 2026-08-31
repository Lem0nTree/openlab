"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { setupTokenFromFragment } from "@/lib/onboarding";

export function SetupForm() {
  const [message, setMessage] = useState("");
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState("");

  useEffect(() => {
    const fragmentToken = setupTokenFromFragment(window.location.hash);
    // Never retain the bootstrap token in history, referrers, or browser storage.
    if (window.location.hash) window.history.replaceState(null, "", window.location.pathname);
    api<{ setup_required: boolean }>("/setup")
      .then((status) => { setSetupRequired(status.setup_required); if (fragmentToken) setToken(fragmentToken); })
      .catch((error: Error) => setMessage(error.message));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!setupRequired || busy) return;
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      await api("/setup", { method: "POST", body: JSON.stringify(Object.fromEntries(data)) });
      window.location.replace("/onboarding");
    } catch (error) {
      const detail = (error as Error).message;
      setMessage(detail);
      if (detail === "OpenLab is already configured") setSetupRequired(false);
    } finally {
      setBusy(false);
    }
  }

  if (setupRequired === false) {
    return <main className="auth"><Link href="/" className="brand">OpenLab</Link><p className="eyebrow">LOCAL-FIRST LAB OS</p><h1>Your lab is ready.</h1><p>An owner account already exists on this installation. Sign in instead of submitting the bootstrap token again.</p><Link href="/login" className="button-link">Sign in</Link></main>;
  }

  return <main className="auth"><Link href="/" className="brand">OpenLab</Link><p className="eyebrow">FIRST-RUN SETUP</p><h1>Create your lab</h1><p>Enter the one-time token printed by the installer. Setup links place it after <code>#</code>, so it is never sent in URLs or saved in browser storage.</p><form onSubmit={submit}><input name="lab_name" placeholder="Lab name" required disabled={setupRequired === null}/><input name="display_name" placeholder="Your name" required disabled={setupRequired === null}/><input name="email" type="email" placeholder="Email" required disabled={setupRequired === null}/><input name="password" type="password" autoComplete="new-password" minLength={12} placeholder="Password (12+ characters)" required disabled={setupRequired === null}/><label className="check-row"><input name="newsletter_opt_in" type="checkbox" disabled={setupRequired === null}/><span>Send me occasional OpenLab updates and product news.</span></label><input name="token" type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="One-time setup token" required disabled={setupRequired === null}/><button disabled={setupRequired === null || busy}>{busy ? "Creating owner…" : "Create owner account"}</button></form>{message && <p className="notice">{message}</p>}<Link href="/login">Already configured? Sign in</Link></main>;
}
