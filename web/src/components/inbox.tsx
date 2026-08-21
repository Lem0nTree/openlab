"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, idempotencyHeaders, upload, type InboxCandidate, type InboxItem, type Location } from "@/lib/api";
import { Shell } from "./shell";

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
      const [nextItems, nextLocations] = await Promise.all([
        api<InboxItem[]>("/inbox"), api<Location[]>("/locations"),
      ]);
      setItems(nextItems); setLocations(nextLocations);
      const entries = await Promise.all(nextItems.filter((item) => item.status === "needs_review").map(async (item) => [item.id, await api<InboxCandidate[]>(`/inbox/${item.id}/candidates`)] as const));
      setCandidates(Object.fromEntries(entries));
    } catch (e) { setError((e as Error).message); }
  };
  useEffect(() => { load(); }, []);

  async function capture(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const item = await api<InboxItem>("/inbox", { method: "POST", body: JSON.stringify({ input_type: inputType, text: text || null }) });
      if (file) { const form = new FormData(); form.set("upload", file); await upload(`/inbox/${item.id}/attachments`, form); }
      await api(`/inbox/${item.id}/process`, { method: "POST" });
      setText(""); setFile(null); await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function confirm(event: FormEvent<HTMLFormElement>, item: InboxItem, candidate: InboxCandidate) {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    try {
      await api(`/inbox/${item.id}/confirm`, { method: "POST", headers: idempotencyHeaders(), body: JSON.stringify({ location_id: data.get("location_id"), candidate: { name: data.get("name"), quantity: Number(data.get("quantity")), category: data.get("category"), confidence: "confirmed" } }) });
      await load();
    } catch (e) { setError((e as Error).message); }
  }
  return <Shell title="Inbox"><div className="inbox-intro"><div><p className="eyebrow">CAPTURE · REVIEW · COMMIT</p><p>Bring in a note, image, voice memo, or PDF. Nothing changes inventory until you review and confirm it.</p></div><a href="/settings#smart-inbox" className="text-link">Inbox settings →</a></div><section className="capture-panel"><div className="section-heading"><div><p className="eyebrow">NEW ITEM</p><h2>Capture something</h2></div><span className="muted-note">AI processing is optional</span></div><form className="capture" onSubmit={capture}><select value={inputType} onChange={(event) => setInputType(event.target.value)}><option value="text">Text note</option><option value="photo">Photo</option><option value="screenshot">Screenshot</option><option value="voice">Voice memo</option><option value="pdf">PDF artifact</option></select><textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="e.g. 10 × ESP32-C3 SuperMini; add context for a photo or voice note" required={inputType === "text"}/><input type="file" accept={inputType === "photo" || inputType === "screenshot" ? "image/*" : inputType === "voice" ? "audio/*" : inputType === "pdf" ? "application/pdf" : undefined} onChange={(event) => setFile(event.target.files?.[0] ?? null)} required={inputType !== "text"}/><button disabled={busy}>{busy ? "Queueing…" : "Add to inbox"}</button></form></section>{error && <p className="error">{error}</p>}<section className="inbox-list"><div className="section-heading"><div><p className="eyebrow">QUEUE</p><h2>Recent captures</h2></div><span className="muted-note">{items.length} {items.length === 1 ? "item" : "items"}</span></div><div className="list">{items.map((item) => <article key={item.id}><strong>{item.input_type}</strong><span className="status">{item.status}</span><p>{item.text}</p>{item.error && <p className="error">{item.error}</p>}{Boolean(item.processing_evidence.provider) && <small>Processed by {String(item.processing_evidence.provider)} {item.processing_evidence.model ? `(${String(item.processing_evidence.model)})` : ""}</small>}{item.status === "needs_review" && (candidates[item.id] ?? []).map((candidate) => <form className="review" key={candidate.id} onSubmit={(event) => confirm(event, item, candidate)}><input name="name" defaultValue={candidate.name}/><input name="quantity" type="number" min="0.000001" step="any" defaultValue={candidate.quantity}/><input name="category" defaultValue={candidate.category}/><span className="status">{candidate.confidence}</span><select name="location_id" required defaultValue=""><option value="" disabled>Choose physical location</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select><button disabled={locations.length === 0}>Confirm and receive</button></form>)}</article>)}</div>{items.length === 0 && <p className="empty-state">Your inbox is empty. Add a capture above to start.</p>}</section></Shell>;
}
