"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, idempotencyHeaders, upload, type InboxCandidate, type InboxItem, type Location, type ProviderConfig } from "@/lib/api";
import { Shell } from "./shell";

const presets = {
  ollama: "http://host.docker.internal:11434/v1",
  openrouter: "https://openrouter.ai/api/v1",
  openai: "https://api.openai.com/v1",
};

export function Inbox() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [candidates, setCandidates] = useState<Record<string, InboxCandidate[]>>({});
  const [config, setConfig] = useState<ProviderConfig | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [inputType, setInputType] = useState("text");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [nextItems, nextLocations, nextConfig] = await Promise.all([
        api<InboxItem[]>("/inbox"), api<Location[]>("/locations"), api<ProviderConfig | null>("/ai/provider"),
      ]);
      setItems(nextItems); setLocations(nextLocations); setConfig(nextConfig);
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
  async function saveProvider(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setError("");
    try {
      const apiKey = String(data.get("api_key") ?? "");
      const payload: Record<string, unknown> = { base_url: data.get("base_url"), model: data.get("model"), enabled: data.get("enabled") === "on" };
      if (apiKey) payload.api_key = apiKey;
      const saved = await api<ProviderConfig>("/ai/provider", { method: "PUT", body: JSON.stringify(payload) });
      setConfig(saved);
    } catch (e) { setError((e as Error).message); }
  }
  async function loadModels() {
    try { setModels((await api<{ models: string[] }>("/ai/provider/models")).models); }
    catch (e) { setError((e as Error).message); }
  }
  const egress = config?.enabled ? config.egress : "local";
  return <Shell title="Universal Inbox"><p>AI proposes candidates; you correct identity, quantity, and destination before any stock is written.</p><section className="inbox-config"><h2>Smart Inbox model</h2><p>{config?.enabled ? `Active model: ${config.model}. Capture data is sent to a ${egress} endpoint.` : "AI is disabled. Text stays local and uses a conservative fallback parser."}</p><form className="review" onSubmit={saveProvider}><select onChange={(event) => { const form = event.currentTarget.form; const endpoint = form?.elements.namedItem("base_url") as HTMLInputElement | null; if (endpoint && event.currentTarget.value) endpoint.value = presets[event.currentTarget.value as keyof typeof presets]; }} defaultValue=""><option value="">Custom compatible endpoint</option><option value="ollama">Ollama (local)</option><option value="openrouter">OpenRouter (external)</option><option value="openai">OpenAI (external)</option></select><input name="base_url" type="url" defaultValue={config?.base_url} placeholder="https://…/v1" required/><input name="model" list="provider-models" defaultValue={config?.model} placeholder="Model ID, e.g. qwen2.5-vl:7b" required/><datalist id="provider-models">{models.map((model) => <option key={model} value={model}/>)}</datalist><input name="api_key" type="password" placeholder={config?.has_api_key ? "Stored securely; leave blank to keep" : "Optional for local endpoints"}/><label><input name="enabled" type="checkbox" defaultChecked={config?.enabled}/> Enable Smart Inbox</label><button>Save model</button><button type="button" onClick={loadModels} disabled={!config?.enabled}>List endpoint models</button></form></section><section><h2>Capture</h2><p className={egress === "external" ? "warning" : "status"}>{egress === "external" ? "External processing: the selected text/image/audio source will leave this local server." : "Local processing: the configured endpoint is local, or AI is disabled."}</p><form className="capture" onSubmit={capture}><select value={inputType} onChange={(event) => setInputType(event.target.value)}><option value="text">Text</option><option value="photo">Photo</option><option value="screenshot">Screenshot</option><option value="voice">Voice</option><option value="pdf">PDF artifact</option></select><textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="e.g. 10 × ESP32-C3 SuperMini; add context for a photo or voice note" required={inputType === "text"}/><input type="file" accept={inputType === "photo" || inputType === "screenshot" ? "image/*" : inputType === "voice" ? "audio/*" : inputType === "pdf" ? "application/pdf" : undefined} onChange={(event) => setFile(event.target.files?.[0] ?? null)} required={inputType !== "text"}/><button disabled={busy}>{busy ? "Queueing…" : "Capture and process"}</button></form></section>{error && <p className="error">{error}</p>}<div className="list">{items.map((item) => <article key={item.id}><strong>{item.input_type}</strong><span className="status">{item.status}</span><p>{item.text}</p>{item.error && <p className="error">{item.error}</p>}{Boolean(item.processing_evidence.provider) && <small>Processed by {String(item.processing_evidence.provider)} {item.processing_evidence.model ? `(${String(item.processing_evidence.model)})` : ""}</small>}{item.status === "needs_review" && (candidates[item.id] ?? []).map((candidate) => <form className="review" key={candidate.id} onSubmit={(event) => confirm(event, item, candidate)}><input name="name" defaultValue={candidate.name}/><input name="quantity" type="number" min="0.000001" step="any" defaultValue={candidate.quantity}/><input name="category" defaultValue={candidate.category}/><span className="status">{candidate.confidence}</span><select name="location_id" required defaultValue=""><option value="" disabled>Choose physical location</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select><button disabled={locations.length === 0}>Confirm and receive</button></form>)}</article>)}</div></Shell>;
}
