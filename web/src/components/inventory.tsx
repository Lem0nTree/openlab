"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, type Thing } from "@/lib/api";
import { Shell } from "./shell";

export function Inventory() {
  const [things, setThings] = useState<Thing[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const load = (term = "") => api<Thing[]>(`/things${term ? `?q=${encodeURIComponent(term)}` : ""}`).then(setThings).catch((e: Error) => setError(e.message));
  useEffect(() => { load(); }, []);
  function search(event: FormEvent) { event.preventDefault(); load(query); }
  return <Shell title="Inventory"><form className="toolbar" onSubmit={search}><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search MPN, name, or alias"/><button>Search</button></form>{error && <p className="error">{error}</p>}<div className="list">{things.map((thing) => <article key={thing.id}><strong>{thing.name}</strong><span>{thing.category}</span><small>{thing.mpn ?? "No MPN"}</small></article>)}{things.length === 0 && <p>No Things found. Add inventory through the Inbox or API.</p>}</div></Shell>;
}

