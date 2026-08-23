"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, idempotencyHeaders, upload, type InboxCandidate, type InboxItem, type Location } from "@/lib/api";
import { Shell } from "./shell";

const modes = [["text", "Text"], ["photo", "Photo"], ["screenshot", "Screenshot"], ["voice", "Voice"], ["email", "Email"], ["pdf", "PDF"]] as const;

export function Inbox() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [candidates, setCandidates] = useState<Record<string, InboxCandidate[]>>({});
  const [inputType, setInputType] = useState("text");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [nextItems, nextLocations] = await Promise.all([api<InboxItem[]>("/inbox"), api<Location[]>("/locations")]);
      setItems(nextItems); setLocations(nextLocations);
      const entries = await Promise.all(nextItems.filter((item) => !["captured", "queued", "processing", "failed"].includes(item.status)).map(async (item) => [item.id, await api<InboxCandidate[]>(`/inbox/${item.id}/candidates`)] as const));
      setCandidates(Object.fromEntries(entries));
    } catch (event) { setError((event as Error).message); }
  };
  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  useEffect(() => {
    if (!items.some((item) => ["captured", "queued", "processing"].includes(item.status))) return;
    // Poll only while durable worker jobs are active.
    const timer = window.setInterval(() => { void load(); }, 2000);
    return () => window.clearInterval(timer);
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
  function confirm(event: FormEvent<HTMLFormElement>, item: InboxItem, candidate: InboxCandidate) {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    return request(`/inbox/${item.id}/candidates/${candidate.id}/confirm`, { method: "POST", body: JSON.stringify({ name: data.get("name"), description: data.get("description") || null, quantity: Number(data.get("quantity")), category: data.get("category") }) });
  }
  function linkLabel(candidate: InboxCandidate) {
    if (candidate.identity_confidence === "unresolved") return "Unable to parse information — provide product link";
    if (candidate.identity_confidence === "low") return "Add product link for automated retrieval";
    return "Add product link";
  }
  function receive(event: FormEvent<HTMLFormElement>, item: InboxItem, candidate: InboxCandidate) {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    return request(`/inbox/${item.id}/candidates/${candidate.id}/receive`, { method: "POST", headers: idempotencyHeaders(), body: JSON.stringify({ location_id: data.get("location_id"), quantity: Number(data.get("quantity")) }) });
  }
  async function productLink(item: InboxItem, candidate: InboxCandidate) {
    const url = window.prompt("Product link for automated identification");
    if (url) await request(`/inbox/${item.id}/candidates/${candidate.id}/enrich-url`, { method: "POST", body: JSON.stringify({ url }) });
  }
  const accept = inputType === "photo" || inputType === "screenshot" ? "image/*" : inputType === "voice" ? "audio/*" : inputType === "email" ? ".eml,message/rfc822,text/plain,text/html" : "application/pdf";
  return <Shell title="Capture"><div className="inbox-intro"><div><p className="eyebrow">UNIVERSAL INBOX</p><p>Capture notes, media, emails, and PDFs. Confirm identity before receiving any stock.</p></div><a href="/settings#smart-inbox" className="text-link">Intelligence settings →</a></div><section className="capture-panel"><div className="section-heading"><div><p className="eyebrow">NEW INPUT</p><h2>Show the lab what arrived.</h2></div><span className="muted-note">AI is optional · raw media is temporary</span></div><div className="capture-tabs">{modes.map(([value, label]) => <button type="button" key={value} className={inputType === value ? "active" : ""} onClick={() => setInputType(value)}>{label}</button>)}</div><form className="capture" onSubmit={capture}><textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="e.g. 10 × ESP32-C3 SuperMini" required={inputType === "text"}/>{inputType !== "text" && <input type="file" accept={accept} onChange={(event) => setFile(event.target.files?.[0] ?? null)} required/>}<button disabled={busy}>{busy ? "Queueing…" : "Add to review queue"}</button></form></section>{error && <p className="error">{error}</p>}<section className="inbox-list"><div className="section-heading"><div><p className="eyebrow">HUMAN REVIEW</p><h2>Recent captures</h2></div><span className="muted-note">{items.length} {items.length === 1 ? "item" : "items"}</span></div><div className="list">{items.map((item) => <article key={item.id}><strong>{item.input_type}</strong><span className="status">{item.status}</span><p>{item.text}</p>{item.error && <p className="error">{item.error}</p>}{(candidates[item.id] ?? []).map((candidate) => { const description = typeof candidate.provenance.description === "string" ? candidate.provenance.description : ""; const observations = Array.isArray(candidate.provenance.observations) ? candidate.provenance.observations.map(String) : []; const linkRequired = candidate.identity_confidence === "unresolved" && !candidate.product_url; return <div className="review" key={candidate.id}><form onSubmit={(event) => void confirm(event, item, candidate)}><input name="name" defaultValue={candidate.name} disabled={candidate.status !== "proposed"}/><textarea name="description" defaultValue={description} placeholder="Short functional sentence" disabled={candidate.status !== "proposed"}/><input name="quantity" type="number" min="0.000001" step="any" defaultValue={candidate.quantity}/><select name="category" defaultValue={candidate.category} disabled={candidate.status !== "proposed"}>{["module", "ic", "board", "sensor", "passive", "connector", "power", "tool", "other", "uncategorized"].map((value) => <option key={value} value={value}>{value}</option>)}</select><span className="status">{candidate.status} · model confidence: {candidate.identity_confidence}</span>{observations.length > 0 && <small>{observations.join(" · ")}</small>}{candidate.status === "proposed" && <><button disabled={busy || linkRequired}>Confirm identity</button><button type="button" className="secondary-button" disabled={busy} onClick={() => void productLink(item, candidate)}>{linkLabel(candidate)}</button><button type="button" className="secondary-button" disabled={busy} onClick={() => void request(`/inbox/${item.id}/candidates/${candidate.id}/ignore`, { method: "POST" })}>Ignore</button></>}{candidate.product_url && <small>Product-link data fetched. Review the proposed identity, then confirm it.</small>}</form>{candidate.status === "confirmed" && <form onSubmit={(event) => void receive(event, item, candidate)}><input name="quantity" type="number" min="0.000001" step="any" defaultValue={candidate.quantity}/><select name="location_id" required defaultValue=""><option value="" disabled>Choose physical location</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select><button disabled={busy || locations.length === 0}>Receive into inventory</button></form>}</div>; })}</article>)}</div>{items.length === 0 && <p className="empty-state">Your review queue is clear. Capture something above to start.</p>}</section></Shell>;
}
