"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, idempotencyHeaders, upload, type InboxCandidate, type InboxItem, type Location } from "@/lib/api";
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
  busy: boolean;
  request: (path: string, init: RequestInit) => Promise<void>;
};

function CandidateCard({ item, candidate, locations, busy, request }: CandidateCardProps) {
  const description = typeof candidate.provenance.description === "string" ? candidate.provenance.description : "";
  const linkRequired = confidence(candidate) === "unresolved" && !candidate.product_url;

  function edit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const payload = { name: data.get("name"), description: data.get("description") || null, quantity: Number(data.get("quantity")), category: data.get("category") };
    const path = `/inbox/${item.id}/candidates/${candidate.id}`;
    if (submitter?.dataset.action === "confirm") {
      return request(`${path}/confirm`, { method: "POST", body: JSON.stringify(payload) });
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
          <select aria-label="Inventory location" name="location_id" required defaultValue=""><option value="" disabled>Choose physical location</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select>
          <button disabled={busy || locations.length === 0}>Receive into inventory</button>
        </form>
      )}
      {candidate.status === "received" && <a className="text-link capture-inventory-link" href="/inventory">View component in inventory →</a>}
    </article>
  );
}

export function Inbox() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [candidates, setCandidates] = useState<Record<string, InboxCandidate[]>>({});
  const [inputType, setInputType] = useState("text");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const visibleCandidates = useMemo(() => items.flatMap((item) => (candidates[item.id] ?? []).filter((candidate) => candidate.status !== "ignored").map((candidate) => ({ item, candidate }))), [items, candidates]);
  const pendingItems = items.filter((item) => ["captured", "queued", "processing", "failed"].includes(item.status));

  const load = async () => {
    try {
      const [nextItems, nextLocations] = await Promise.all([api<InboxItem[]>("/inbox"), api<Location[]>("/locations")]);
      setItems(nextItems); setLocations(nextLocations);
      const entries = await Promise.all(nextItems.filter((item) => !["captured", "queued", "processing", "failed"].includes(item.status)).map(async (item) => [item.id, await api<InboxCandidate[]>(`/inbox/${item.id}/candidates`)] as const));
      setCandidates(Object.fromEntries(entries));
    } catch (event) { setError((event as Error).message); }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!items.some((item) => ["captured", "queued", "processing"].includes(item.status))) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

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
  return (
    <Shell title="Capture">
      <div className="inbox-intro"><div><p className="eyebrow">UNIVERSAL INBOX</p><p>Capture notes, media, emails, and PDFs. Confirm identity before receiving stock.</p></div><a href="/settings#smart-inbox" className="text-link">Intelligence settings →</a></div>
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
          {visibleCandidates.map(({ item, candidate }) => <CandidateCard key={`${candidate.id}:${candidate.name}:${candidate.product_url ?? ""}`} item={item} candidate={candidate} locations={locations} busy={busy} request={request} />)}
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
