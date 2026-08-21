"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";

export function LoginForm() {
  const [message, setMessage] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api("/session", { method: "POST", body: JSON.stringify(Object.fromEntries(data)) });
      window.location.assign("/");
    } catch (error) { setMessage((error as Error).message); }
  }
  return <main className="auth"><a href="/" className="brand">OpenLab</a><h1>Welcome back</h1><form onSubmit={submit}><input name="email" type="email" placeholder="Email" required/><input name="password" type="password" placeholder="Password" required/><button>Sign in</button></form>{message && <p className="error">{message}</p>}<a href="/setup">Set up a new local lab</a></main>;
}

