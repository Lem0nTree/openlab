"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, type ProviderConfig } from "@/lib/api";
import { Shell } from "./shell";

const presets = {
  ollama: "http://host.docker.internal:11434/v1",
  openrouter: "https://openrouter.ai/api/v1",
  openai: "https://api.openai.com/v1",
};

export function Settings() {
  const [config, setConfig] = useState<ProviderConfig | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [preset, setPreset] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api<ProviderConfig | null>("/ai/provider")
      .then((value) => {
        setConfig(value);
        setBaseUrl(value?.base_url ?? "");
        setModel(value?.model ?? "");
        setEnabled(value?.enabled ?? false);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      const payload: Record<string, unknown> = { base_url: baseUrl, model, enabled };
      if (apiKey) payload.api_key = apiKey;
      const saved = await api<ProviderConfig>("/ai/provider", { method: "PUT", body: JSON.stringify(payload) });
      setConfig(saved);
      setApiKey("");
      setMessage("Smart Inbox settings saved.");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function loadModels() {
    setError("");
    try {
      const result = await api<{ models: string[] }>("/ai/provider/models");
      setModels(result.models);
      setMessage(`${result.models.length} models found at the endpoint.`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return <Shell title="Settings"><div className="settings-layout"><aside className="settings-nav"><p className="nav-label">SETTINGS</p><a href="#smart-inbox">Smart Inbox</a><a href="#privacy">Privacy and data</a><a href="#lab">Lab</a></aside><div className="settings-content"><section className="settings-section" id="smart-inbox"><div className="section-heading"><div><p className="eyebrow">SMART INBOX</p><h2>Processing model</h2></div><span className={`settings-state ${enabled ? "is-on" : ""}`}>{enabled ? "Enabled" : "Disabled"}</span></div><p className="settings-copy">Choose one OpenAI-compatible endpoint for candidate extraction. The Inbox stays usable with AI disabled.</p><form className="settings-form" onSubmit={save}><label>Provider preset<select value={preset} onChange={(event) => { const value = event.target.value; setPreset(value); if (value) setBaseUrl(presets[value as keyof typeof presets]); }}><option value="">Custom endpoint</option><option value="ollama">Ollama on this network</option><option value="openrouter">OpenRouter</option><option value="openai">OpenAI</option></select></label><label>Endpoint URL<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} type="url" placeholder="http://localhost:11434/v1" required /></label><label>Model ID<input value={model} onChange={(event) => setModel(event.target.value)} list="provider-models" placeholder="qwen2.5-vl:7b" required /><datalist id="provider-models">{models.map((item) => <option key={item} value={item} />)}</datalist></label><label>API key<input value={apiKey} onChange={(event) => setApiKey(event.target.value)} type="password" placeholder={config?.has_api_key ? "Stored securely; leave blank to keep" : "Not required for local endpoints"} /></label><label className="check-row"><input checked={enabled} onChange={(event) => setEnabled(event.target.checked)} type="checkbox" /> Enable processing for new Inbox items</label><div className="settings-actions"><button disabled={loading}>{loading ? "Loading…" : "Save settings"}</button><button className="secondary-button" type="button" onClick={loadModels} disabled={!enabled}>Test endpoint</button></div></form>{config?.enabled && <p className={config.egress === "external" ? "settings-warning" : "settings-note"}>{config.egress === "external" ? "External processing is enabled. Captured content may leave this local server." : "Local endpoint detected. Captured content stays on your configured network."}</p>}{message && <p className="notice">{message}</p>}{error && <p className="error">{error}</p>}</section><section className="settings-section" id="privacy"><p className="eyebrow">PRIVACY AND DATA</p><h2>Where data goes</h2><p className="settings-copy">OpenLab stores inventory, capture history, and attachments locally. External provider processing is opt-in and always shown here before you use it.</p><div className="settings-fact"><span>Attachments</span><strong>Local content-addressed storage</strong></div><div className="settings-fact"><span>Inventory writes</span><strong>Only after human confirmation</strong></div></section><section className="settings-section" id="lab"><p className="eyebrow">LAB</p><h2>Installation preferences</h2><p className="settings-copy">Lab name, units, and display preferences will live here as the settings model expands.</p><span className="settings-state">Available in a later MVP</span></section></div></div></Shell>;
}
