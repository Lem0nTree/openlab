"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { api, type Thing } from "@/lib/api";
import { Shell } from "./shell";
import { LabIcon } from "./lab-icon";

export function Inventory() {
  const [things, setThings] = useState<Thing[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const load = (term = "") => api<Thing[]>(`/things${term ? `?q=${encodeURIComponent(term)}` : ""}`).then(setThings).catch((e: Error) => setError(e.message));
  useEffect(() => { load(); }, []);
  function search(event: FormEvent) { event.preventDefault(); load(query); }
  return <Shell title="Things"><div className="inventory-heading"><div><p className="eyebrow">PHYSICAL LAB MODEL</p><p>Components, boards, tools, modules and everything else you can build with.</p></div><a href="/inbox" className="button-link"><LabIcon name="plus"/>Capture a Thing</a></div><form className="toolbar inventory-search" onSubmit={search}><span><LabIcon name="search"/></span><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by name, MPN, capability or alias"/><button>Search</button></form>{error && <p className="error">{error}</p>}<div className="things-grid">{things.map((thing) => <Link className="inventory-thing" href={`/inventory/${thing.id}`} key={thing.id}><div className="inventory-visual"><LabIcon name="chip"/><span>THING</span></div><div className="inventory-card-head"><span>{thing.category}</span><i/></div><h3>{thing.name}</h3><p>{thing.manufacturer ?? "Manufacturer not recorded"}</p><div className="inventory-card-foot"><code>{thing.mpn ?? "NO MPN"}</code><span><i/>OPEN</span></div></Link>)}{things.length === 0 && <div className="inventory-empty"><span><LabIcon name="box"/></span><h2>No Things found.</h2><p>Capture a photo, voice note or text list to begin building your lab model.</p><a href="/inbox">Open Universal Inbox <LabIcon name="arrow"/></a></div>}</div></Shell>;
}
