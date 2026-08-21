"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";

export function SetupForm() {
  const [message, setMessage] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api("/setup", { method: "POST", body: JSON.stringify(Object.fromEntries(data)) });
      setMessage("Lab created. Sign in with the owner account.");
      event.currentTarget.reset();
    } catch (error) { setMessage((error as Error).message); }
  }
  return <main className="auth"><a href="/" className="brand">OpenLab</a><h1>Create your lab</h1><p>Enter the one-time setup token printed by the server. It is never stored in the browser.</p><form onSubmit={submit}><input name="lab_name" placeholder="Lab name" required/><input name="display_name" placeholder="Your name" required/><input name="email" type="email" placeholder="Email" required/><input name="password" type="password" minLength={12} placeholder="Password (12+ characters)" required/><input name="token" placeholder="One-time setup token" required/><button>Create owner account</button></form>{message && <p className="notice">{message}</p>}<a href="/login">Already configured? Sign in</a></main>;
}

