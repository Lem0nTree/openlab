"use client";

import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Balance, type Location, type LocationQRInfo, type StockMovement, type Thing } from "@/lib/api";
import { formatQuantity } from "@/lib/format";
import { movementSummary } from "@/lib/inventory-utils";
import { LabIcon } from "./lab-icon";
import { Shell } from "./shell";
import { StockActions } from "./stock-actions";

export function LocationDetail({ locationId }: { locationId: string }) {
  const [location, setLocation] = useState<Location | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [things, setThings] = useState<Thing[]>([]);
  const [balances, setBalances] = useState<Balance[]>([]);
  const [movements, setMovements] = useState<StockMovement[]>([]);
  const [qr, setQr] = useState<LocationQRInfo | null>(null);
  const [selectedThingId, setSelectedThingId] = useState("");
  const [query, setQuery] = useState("");
  const [origin, setOrigin] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!origin) return;
    try {
      const base = new URLSearchParams({ base_url: origin });
      const [nextLocation, nextLocations, nextThings, nextBalances, nextMovements, nextQr] = await Promise.all([
        api<Location>(`/locations/${locationId}`), api<Location[]>("/locations"), api<Thing[]>("/things"),
        api<Balance[]>(`/inventory/balances?location_id=${encodeURIComponent(locationId)}`),
        api<StockMovement[]>(`/inventory/movements?location_id=${encodeURIComponent(locationId)}&limit=20`),
        api<LocationQRInfo>(`/locations/${locationId}/qr-info?${base}`),
      ]);
      setLocation(nextLocation); setLocations(nextLocations); setThings(nextThings); setBalances(nextBalances); setMovements(nextMovements); setQr(nextQr);
      setSelectedThingId((current) => current || nextBalances[0]?.thing_id || nextThings[0]?.id || "");
    } catch (cause) { setError((cause as Error).message); }
  }, [locationId, origin]);

  useEffect(() => { const timer = window.setTimeout(() => setOrigin(window.location.origin), 0); return () => window.clearTimeout(timer); }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  const visibleBalances = useMemo(() => balances.filter((balance) => `${balance.thing_name} ${balance.thing_category} ${balance.thing_mpn ?? ""}`.toLowerCase().includes(query.toLowerCase())), [balances, query]);
  const selectedThing = things.find((thing) => thing.id === selectedThingId) ?? null;
  if (!location) return <Shell title="Drawer"><div className="build-detail-loading"><span/><p>{error || "Loading drawer…"}</p></div></Shell>;

  return <Shell title={location.name}>
    <nav className="detail-back"><Link href="/locations">← All drawers</Link><span>{location.thing_count} Things · {formatQuantity(location.total_quantity)} units</span></nav>
    <section className="location-detail-hero"><div><p className="eyebrow">DRAWER WORKSPACE</p><h2>{location.name}</h2><p>Inspect what is physically here, keep its count honest, or capture something directly into this drawer.</p><div className="detail-hero-actions"><Link className="button-link" href={`/inbox?location=${encodeURIComponent(location.public_code)}`}><LabIcon name="plus"/> Capture into this drawer</Link></div></div>{qr && <div className="drawer-label-panel"><div className="drawer-label-print"><Image src={qr.svg_url} width={116} height={116} unoptimized alt={`QR label for ${location.name}`}/><div><strong>{location.name}</strong><small>SCAN TO CAPTURE</small><code>{location.public_code.slice(0,12)}</code></div></div><p title={qr.target_url}>{qr.target_url}</p><div><a href={qr.svg_url} download={`openlab-${location.name}.svg`}>Download SVG</a><button type="button" onClick={() => window.print()}>Print label</button></div></div>}</section>
    {error && <p className="error">{error}</p>}
    <div className="location-detail-grid"><section className="inventory-panel"><div className="section-heading"><div><p className="eyebrow">DRAWER CONTENTS</p><h2>What is here</h2></div><span className="muted-note">{visibleBalances.length} records</span></div><form className="toolbar inventory-search" onSubmit={(event) => event.preventDefault()}><span><LabIcon name="search"/></span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search this drawer"/></form><div className="stock-table">{visibleBalances.map((balance) => <Link href={`/inventory/${balance.thing_id}`} key={balance.thing_id}><span className="stock-thing-icon"><LabIcon name="chip"/></span><span><strong>{balance.thing_name}</strong><small>{balance.thing_category} · {balance.thing_mpn ?? "No MPN"}</small></span><b>{formatQuantity(balance.quantity)}</b><LabIcon name="arrow"/></Link>)}{visibleBalances.length === 0 && <p className="empty-state">This drawer has no matching stock.</p>}</div></section><aside className="operation-panel"><div><p className="eyebrow">STOCK ACTION</p><h2>Update this drawer</h2><p>Choose a Thing, then record one physical operation.</p></div><label className="thing-picker"><span>Thing</span><select value={selectedThingId} onChange={(event) => setSelectedThingId(event.target.value)}><option value="">Choose a Thing</option>{things.map((thing) => <option key={thing.id} value={thing.id}>{thing.name}</option>)}</select></label>{selectedThing && <StockActions key={`${selectedThing.id}:${location.id}`} thing={selectedThing} locations={locations} balances={balances} defaultLocationId={location.id} onDone={load}/>}</aside></div>
    <section className="movement-panel"><div className="section-heading"><div><p className="eyebrow">RECENT ACTIVITY</p><h2>Drawer history</h2></div></div><div className="movement-list">{movements.map((movement) => <article key={movement.id}><span className={`movement-kind is-${movement.movement_type}`}>{movement.movement_type}</span><div><strong>{movement.thing_name}</strong><p>{movementSummary(movement.movement_type, movement.from_location_name, movement.to_location_name)}</p>{movement.note && <small>{movement.note}</small>}</div><b>{formatQuantity(movement.quantity)}</b><time>{new Date(movement.created_at).toLocaleString()}</time></article>)}{movements.length === 0 && <p className="empty-state">No stock activity recorded for this drawer.</p>}</div></section>
  </Shell>;
}
