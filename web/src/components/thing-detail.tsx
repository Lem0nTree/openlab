"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, type Balance, type Location, type StockMovement, type Thing } from "@/lib/api";
import { formatQuantity } from "@/lib/format";
import { movementSummary } from "@/lib/inventory-utils";
import { LabIcon } from "./lab-icon";
import { Shell } from "./shell";
import { StockActions } from "./stock-actions";

const categories = ["module", "ic", "board", "sensor", "passive", "connector", "power", "tool", "other", "uncategorized"];

export function ThingDetail({ thingId }: { thingId: string }) {
  const [thing, setThing] = useState<Thing | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [balances, setBalances] = useState<Balance[]>([]);
  const [movements, setMovements] = useState<StockMovement[]>([]);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const total = useMemo(() => balances.reduce((sum, balance) => sum + Number(balance.quantity), 0), [balances]);

  const load = useCallback(async () => {
    try {
      const [nextThing, nextLocations, nextBalances, nextMovements] = await Promise.all([
        api<Thing>(`/things/${thingId}`), api<Location[]>("/locations"),
        api<Balance[]>(`/inventory/balances?thing_id=${encodeURIComponent(thingId)}`),
        api<StockMovement[]>(`/inventory/movements?thing_id=${encodeURIComponent(thingId)}&limit=30`),
      ]);
      setThing(nextThing); setLocations(nextLocations); setBalances(nextBalances); setMovements(nextMovements);
    } catch (cause) { setError((cause as Error).message); }
  }, [thingId]);

  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!thing) return; setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      const updated = await api<Thing>(`/things/${thing.id}`, { method: "PATCH", body: JSON.stringify({ name: data.get("name"), category: data.get("category"), manufacturer: data.get("manufacturer") || null, mpn: data.get("mpn") || null, revision: thing.revision }) });
      setThing(updated); setEditing(false);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function archive() {
    if (!thing || !window.confirm(`Archive ${thing.name}? Its history will be preserved.`)) return;
    setBusy(true); setError("");
    try { await api(`/things/${thing.id}`, { method: "DELETE" }); window.location.assign("/inventory"); }
    catch (cause) { setError((cause as Error).message); setBusy(false); }
  }

  if (!thing) return <Shell title="Thing"><div className="build-detail-loading"><span/><p>{error || "Loading Thing…"}</p></div></Shell>;
  return <Shell title={thing.name}>
    <nav className="detail-back"><Link href="/inventory">← All Things</Link><span>{formatQuantity(total)} total units</span></nav>
    <section className="thing-detail-hero"><div className="thing-detail-icon"><LabIcon name="chip"/></div><div className="thing-detail-copy"><p className="eyebrow">{thing.category.toUpperCase()}</p>{editing ? <form className="thing-edit-form" onSubmit={(event) => void save(event)}><label><span>Name</span><input name="name" defaultValue={thing.name} required/></label><label><span>Category</span><select name="category" defaultValue={thing.category}>{categories.map((category) => <option key={category}>{category}</option>)}</select></label><label><span>Manufacturer</span><input name="manufacturer" defaultValue={thing.manufacturer ?? ""}/></label><label><span>MPN</span><input name="mpn" defaultValue={thing.mpn ?? ""}/></label><div><button disabled={busy}>Save Thing</button><button type="button" className="secondary-button" onClick={() => setEditing(false)}>Cancel</button></div></form> : <><h2>{thing.name}</h2><p>{thing.manufacturer ?? "Manufacturer not recorded"} · {thing.mpn ?? "No MPN recorded"}</p><div className="detail-hero-actions"><button type="button" className="secondary-button" onClick={() => setEditing(true)}><LabIcon name="edit"/> Edit Thing</button><button type="button" className="danger-button" disabled={busy || total > 0} title={total > 0 ? "Move or use all stock before archiving" : undefined} onClick={() => void archive()}>Archive Thing</button></div></>}</div><div className="thing-total"><small>TOTAL STOCK</small><strong>{formatQuantity(total)}</strong><span>{balances.length} {balances.length === 1 ? "drawer" : "drawers"}</span></div></section>
    {error && <p className="error">{error}</p>}
    <div className="thing-detail-grid"><section className="inventory-panel"><div className="section-heading"><div><p className="eyebrow">PHYSICAL STOCK</p><h2>Where it is</h2></div></div><div className="stock-table">{balances.map((balance) => <Link href={`/locations/${balance.location_id}`} key={balance.location_id}><span className="stock-thing-icon"><LabIcon name="pin"/></span><span><strong>{balance.location_name}</strong><small>Balance revision {balance.revision}</small></span><b>{formatQuantity(balance.quantity)}</b><LabIcon name="arrow"/></Link>)}{balances.length === 0 && <p className="empty-state">No physical stock recorded.</p>}</div></section><aside className="operation-panel"><div><p className="eyebrow">STOCK ACTION</p><h2>Update stock</h2><p>Receive, move, use, or correct this Thing.</p></div><StockActions thing={thing} locations={locations} balances={balances} onDone={load}/></aside></div>
    <section className="movement-panel"><div className="section-heading"><div><p className="eyebrow">RECENT ACTIVITY</p><h2>Thing history</h2></div></div><div className="movement-list">{movements.map((movement) => <article key={movement.id}><span className={`movement-kind is-${movement.movement_type}`}>{movement.movement_type}</span><div><strong>{movementSummary(movement.movement_type, movement.from_location_name, movement.to_location_name)}</strong>{movement.note && <small>{movement.note}</small>}</div><b>{formatQuantity(movement.quantity)}</b><time>{new Date(movement.created_at).toLocaleString()}</time></article>)}{movements.length === 0 && <p className="empty-state">No stock activity recorded for this Thing.</p>}</div></section>
  </Shell>;
}
