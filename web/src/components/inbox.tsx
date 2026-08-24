"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, idempotencyHeaders, upload, type InboxCandidate, type InboxItem, type Location, type Thing } from "@/lib/api";
import { captureMode, existingThingConfirmation, locationFromCode } from "@/lib/inventory-utils";
import { Shell } from "./shell";

const modes = [["text", "Text"], ["photo", "Photo"], ["screenshot", "Screenshot"], ["voice", "Voice"], ["email", "Email"], ["pdf", "PDF"]] as const;
const categories = ["module", "ic", "board", "sensor", "passive", "connector", "power", "tool", "other", "uncategorized"];

function confidence(candidate: InboxCandidate) {
  return ["high", "medium", "low", "unresolved"].includes(candidate.identity_confidence)
    ? candidate.identity_confidence : "unresolved";
}

function linkLabel(candidate: InboxCandidate) {
  if (confidence(candidate) === "unresolved") return "Provide product link";
  if (confidence(candidate) === "low") return "Add product link for retrieval";
  return candidate.product_url ? "Refresh from product link" : "Add product link";
}

type CandidateCardProps = {
  item: InboxItem;
  candidate: InboxCandidate;
  locations: Location[];
  things: Thing[];
  defaultLocationId: string;
  busy: boolean;
  request: (path: string, init: RequestInit) => Promise<void>;
};

function CandidateCard({ item, candidate, locations, things, defaultLocationId, busy, request }: CandidateCardProps) {
  const description = typeof candidate.provenance.description === "string" ? candidate.provenance.description : "";
  const linkRequired = confidence(candidate) === "unresolved" && !candidate.product_url;

  function edit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const existingThingId = String(data.get("existing_thing_id") ?? "");
    const payload = { name: data.get("name"), description: data.get("description") || null, quantity: Number(data.get("quantity")), category: data.get("category") };
    const path = `/inbox/${item.id}/candidates/${candidate.id}`;
    if (submitter?.dataset.action === "confirm") {
      return request(`${path}/confirm`, { method: "POST", body: JSON.stringify({ ...payload, ...existingThingConfirmation(existingThingId) }) });
    }
    return request(path, { method: "PATCH", body: JSON.stringify(payload) });
  }

  function receive(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    return request(`/inbox/${item.id}/candidates/${candidate.id}/receive`, {
      method: "POST", headers: idempotencyHeaders(),
      body: JSON.stringify({ location_id: data.get("location_id"), quantity: Number(data.get("quantity")) }),
    });
  }

  async function productLink() {
    const url = window.prompt("Product link for automated identification");
    if (url) await request(`/inbox/${item.id}/candidates/${candidate.id}/enrich-url`, { method: "POST", body: JSON.stringify({ url }) });
  }

  return (
    <article className="capture-card" aria-labelledby={`candidate-${candidate.id}`}>
      <header className="capture-card-head">
        <div><span className="capture-kind">{item.input_type}</span><h3 id={`candidate-${candidate.id}`}>{candidate.name}</h3></div>
        <span className={`candidate-state is-${candidate.status}`}>{candidate.status}</span>
      </header>
      {candidate.status === "proposed" ? (
        <form className="candidate-form" onSubmit={(event) => void edit(event)}>
          <label className="candidate-name"><span>Component</span><input name="name" defaultValue={candidate.name} required /></label>
          <label className="candidate-description"><span>What it does</span><textarea name="description" defaultValue={description} placeholder="Short functional sentence for semantic retrieval" /></label>
          <label><span>Quantity</span><input name="quantity" type="number" min="0.000001" step="any" defaultValue={candidate.quantity} required /></label>
          <label><span>Category</span><select name="category" defaultValue={candidate.category}>{categories.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          <label className="candidate-existing"><span>Inventory identity</span><select name="existing_thing_id" defaultValue=""><option value="">Create a new Thing</option>{things.map((thing) => <option key={thing.id} value={thing.id}>Add stock to {thing.name}</option>)}</select></label>
          <div className="candidate-meta"><span>Model confidence: {confidence(candidate)}</span>{candidate.product_url && <span>Product data fetched · confirm before inventory</span>}</div>
          <div className="candidate-actions">
            <button type="submit" className="secondary-button" data-action="save" disabled={busy}>Save changes</button>
            <button type="submit" data-action="confirm" disabled={busy || linkRequired}>Confirm component</button>
            <button type="button" className="secondary-button" disabled={busy} onClick={() => void productLink()}>{linkLabel(candidate)}</button>
            <button type="button" className="danger-button" disabled={busy} onClick={() => void request(`/inbox/${item.id}/candidates/${candidate.id}`, { method: "DELETE" })}>Remove</button>
          </div>
        </form>
      ) : (
        <div className="candidate-summary"><p>{description || `${candidate.category} ready for inventory.`}</p><div><span>{candidate.quantity} units</span><span>{candidate.category}</span><span>confidence: {confidence(candidate)}</span></div></div>
      )}
      {candidate.status === "confirmed" && (
        <form className="receive-form" onSubmit={(event) => void receive(event)}>
          <input aria-label="Quantity to receive" name="quantity" type="number" min="0.000001" step="any" defaultValue={candidate.quantity} required />
          <select key={defaultLocationId || "manual"} aria-label="Inventory location" name="location_id" required defaultValue={defaultLocationId}><option value="" disabled>Choose physical location</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select>
          <button disabled={busy || locations.length === 0}>Receive into inventory</button>
        </form>
      )}
      {candidate.status === "received" && <Link className="text-link capture-inventory-link" href="/inventory">View component in inventory →</Link>}
    </article>
  );
}

export function Inbox({ initialMode = "text", locationCode }: { initialMode?: string; locationCode?: string }) {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [things, setThings] = useState<Thing[]>([]);
  const [candidates, setCandidates] = useState<Record<string, InboxCandidate[]>>({});
  const [inputType, setInputType] = useState(captureMode(initialMode));
  const [selectedLocationId, setSelectedLocationId] = useState("");
  const [locationWarning, setLocationWarning] = useState("");
  const [labelDismissed, setLabelDismissed] = useState(false);
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const visibleCandidates = useMemo(() => items.flatMap((item) => (candidates[item.id] ?? []).filter((candidate) => candidate.status !== "ignored").map((candidate) => ({ item, candidate }))), [items, candidates]);
  const pendingItems = items.filter((item) => ["captured", "queued", "processing", "failed"].includes(item.status));

  const load = useCallback(async () => {
    try {
      const [nextItems, nextLocations, nextThings] = await Promise.all([api<InboxItem[]>("/inbox"), api<Location[]>("/locations"), api<Thing[]>("/things")]);
      const labelLocation = locationFromCode(nextLocations, locationCode);
      setItems(nextItems); setLocations(nextLocations); setThings(nextThings);
      if (locationCode && !labelLocation) { setSelectedLocationId(""); setLocationWarning("This drawer label is invalid or no longer available. Choose a destination manually."); }
      else if (labelLocation && !labelDismissed) { setSelectedLocationId(labelLocation.id); setLocationWarning(""); }
      const entries = await Promise.all(nextItems.filter((item) => !["captured", "queued", "processing", "failed"].includes(item.status)).map(async (item) => [item.id, await api<InboxCandidate[]>(`/inbox/${item.id}/candidates`)] as const));
      setCandidates(Object.fromEntries(entries));
    } catch (event) { setError((event as Error).message); }
  }, [labelDismissed, locationCode]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    if (!items.some((item) => ["captured", "queued", "processing"].includes(item.status))) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [items, load]);

  async function request(path: string, init: RequestInit) {
    setBusy(true); setError("");
    try { await api(path, init); await load(); } catch (event) { setError((event as Error).message); } finally { setBusy(false); }
  }

  async function capture(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const item = await api<InboxItem>("/inbox", { method: "POST", body: JSON.stringify({ input_type: inputType, text: text || null }) });
      if (file) { const form = new FormData(); form.set("upload", file); await upload(`/inbox/${item.id}/attachments`, form); }
      await api(`/inbox/${item.id}/process`, { method: "POST" }); setText(""); setFile(null); await load();
    } catch (event) { setError((event as Error).message); } finally { setBusy(false); }
  }

  const accept = inputType === "photo" || inputType === "screenshot" ? "image/*" : inputType === "voice" ? "audio/*" : inputType === "email" ? ".eml,message/rfc822,text/plain,text/html" : "application/pdf";
  const selectedLocation = locations.find((location) => location.id === selectedLocationId) ?? null;
  return (
    <Shell title="Capture">
      <div className="inbox-intro"><div><p className="eyebrow">UNIVERSAL INBOX</p><p>Capture notes, media, emails, and PDFs. Confirm identity before receiving stock.</p></div><a href="/settings#smart-inbox" className="text-link">Intelligence settings →</a></div>
      {selectedLocation && <div className="capture-destination"><span><strong>Receiving into {selectedLocation.name}</strong><small>Drawer selected from QR label. You can still change it during review.</small></span><button type="button" className="secondary-button" onClick={() => { setLabelDismissed(true); setSelectedLocationId(""); }}>Clear destination</button></div>}
      {locationWarning && <p className="notice-warning">{locationWarning}</p>}
      <section className="capture-panel">
        <div className="section-heading"><div><p className="eyebrow">NEW INPUT</p><h2>Show the lab what arrived.</h2></div><span className="muted-note">AI is optional · raw media is temporary</span></div>
        <div className="capture-tabs">{modes.map(([value, label]) => <button type="button" key={value} className={inputType === value ? "active" : ""} onClick={() => setInputType(value)}>{label}</button>)}</div>
        <form className="capture" onSubmit={capture}>
          <textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="e.g. 10 × ESP32-C3 SuperMini" required={inputType === "text"} />
          {inputType !== "text" && <input type="file" accept={accept} onChange={(event) => setFile(event.target.files?.[0] ?? null)} required />}
          <button disabled={busy}>{busy ? "Queueing…" : "Add to review queue"}</button>
        </form>
      </section>
      {error && <p className="error">{error}</p>}
      <section className="inbox-list">
        <div className="section-heading"><div><p className="eyebrow">COMPONENT REVIEW</p><h2>Recent components</h2></div><span className="muted-note">{visibleCandidates.length} {visibleCandidates.length === 1 ? "component" : "components"}</span></div>
        <div className="capture-card-grid">
          {visibleCandidates.map(({ item, candidate }) => <CandidateCard key={`${candidate.id}:${candidate.name}:${candidate.product_url ?? ""}`} item={item} candidate={candidate} locations={locations} things={things} defaultLocationId={selectedLocationId} busy={busy} request={request} />)}
          {pendingItems.map((item) => (
            <article className="capture-card capture-processing" key={item.id}>
              <header className="capture-card-head"><div><span className="capture-kind">{item.input_type}</span><h3>{item.status === "failed" ? "Component not identified" : "Identifying component…"}</h3></div><span className={`candidate-state is-${item.status}`}>{item.status}</span></header>
              <p>{item.status === "failed" ? "The source could not be processed. Its full contents are hidden from this view." : "Processing the source into a concise component identity."}</p>
              {item.status === "failed" && <button className="secondary-button" disabled={busy} onClick={() => void request(`/inbox/${item.id}/process`, { method: "POST" })}>Retry capture</button>}
            </article>
          ))}
        </div>
        {visibleCandidates.length === 0 && pendingItems.length === 0 && <p className="empty-state">No recent components. Capture something above to start.</p>}
      </section>
    </Shell>
  );
}
