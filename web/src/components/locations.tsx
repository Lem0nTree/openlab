"use client";

import Image from "next/image";
import { FormEvent, useEffect, useState } from "react";
import { api, type Location } from "@/lib/api";
import { Shell } from "./shell";

export function Locations() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const load = () => api<Location[]>("/locations").then(setLocations).catch((e: Error) => setError(e.message));
  useEffect(() => { load(); }, []);
  async function add(event: FormEvent) { event.preventDefault(); try { await api<Location>("/locations", { method: "POST", body: JSON.stringify({ name }) }); setName(""); load(); } catch (e) { setError((e as Error).message); } }
  return <Shell title="Locations"><form className="toolbar" onSubmit={add}><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Drawer, bin, shelf…" required/><button>Add location</button></form>{error && <p className="error">{error}</p>}<div className="list">{locations.map((location) => <article key={location.id}><Image className="qr" src={`/api/v1/locations/${location.id}/qr.svg`} width={78} height={78} unoptimized alt={`QR label for ${location.name}`}/><div><strong>{location.name}</strong><small>Location ID: {location.public_code}</small></div></article>)}</div></Shell>;
}
