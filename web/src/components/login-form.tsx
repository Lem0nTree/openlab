"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { api } from "@/lib/api";

export function LoginForm({ created = false, destination = "/" }: { created?: boolean; destination?: string }) {
  const [message, setMessage] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api("/session", { method: "POST", body: JSON.stringify(Object.fromEntries(data)) });
      window.location.replace(destination);
    } catch (error) { setMessage((error as Error).message); }
  }
  return <main className="auth"><Link href="/login" className="brand">OpenLab</Link><p className="eyebrow">LOCAL-FIRST LAB OS</p><h1>Welcome back.</h1><p>Sign in to access your lab. Inventory, knowledge, and project decisions stay private.</p><form onSubmit={submit}><input name="email" type="email" autoComplete="username" placeholder="Email" required/><input name="password" type="password" autoComplete="current-password" placeholder="Password" required/><button>Sign in</button></form>{created && <p className="notice">Owner account created. Sign in to open your lab.</p>}{message && <p className="error">{message}</p>}<Link href="/setup">First time here? Set up your lab</Link></main>;
}
