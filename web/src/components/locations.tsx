"use client";

import Image from "next/image";
import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, type Location } from "@/lib/api";
import { formatQuantity } from "@/lib/format";
import { LabIcon } from "./lab-icon";
import { Shell } from "./shell";

export function Locations() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [origin, setOrigin] = useState("");
  const [error, setError] = useState("");
  const visibleLocations = useMemo(() => locations.filter((location) => location.name.toLowerCase().includes(query.toLowerCase())), [locations, query]);

  async function load() {
    try { setLocations(await api<Location[]>("/locations")); }
    catch (cause) { setError((cause as Error).message); }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => { setOrigin(window.location.origin); void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  async function add(event: FormEvent) {
    event.preventDefault(); setError("");
    try { await api<Location>("/locations", { method: "POST", body: JSON.stringify({ name }) }); setName(""); await load(); }
    catch (cause) { setError((cause as Error).message); }
  }

  return <Shell title="Locations">
    <div className="locations-intro"><div><p className="eyebrow">PHYSICAL STORAGE</p><h2>Every drawer, immediately knowable.</h2><p>Open a drawer to inspect stock, or scan its label to capture directly into it.</p></div><form className="location-create" onSubmit={(event) => void add(event)}><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Drawer name" required/><button><LabIcon name="plus"/> Add drawer</button></form></div>
    <form className="toolbar inventory-search location-search" onSubmit={(event) => event.preventDefault()}><span><LabIcon name="search"/></span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search drawers"/></form>
    {error && <p className="error">{error}</p>}
    <div className="location-grid">{visibleLocations.map((location) => { const qrSource = origin ? `/api/v1/locations/${location.id}/qr.svg?${new URLSearchParams({ base_url: origin })}` : ""; return <article className="location-card" key={location.id}><div className="location-qr-mini">{qrSource ? <Image src={qrSource} width={86} height={86} unoptimized alt={`QR label for ${location.name}`}/> : <span/>}</div><div className="location-card-copy"><p className="eyebrow">DRAWER</p><h3>{location.name}</h3><div><span><strong>{location.thing_count}</strong> Things</span><span><strong>{formatQuantity(location.total_quantity)}</strong> units</span></div><code>{location.public_code.slice(0,10)}</code></div><div className="location-card-actions"><Link className="button-link" href={`/locations/${location.id}`}>Open drawer <LabIcon name="arrow"/></Link><Link href={`/inbox?location=${encodeURIComponent(location.public_code)}`}>Capture here</Link></div></article>; })}{visibleLocations.length === 0 && <div className="inventory-empty"><span><LabIcon name="pin"/></span><h2>{locations.length ? "No matching drawers." : "No drawers yet."}</h2><p>Add the first physical drawer, then print its label and start receiving stock.</p></div>}</div>
  </Shell>;
}
