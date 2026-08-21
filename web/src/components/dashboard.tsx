"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Shell } from "./shell";

export function Dashboard() {
  const [message, setMessage] = useState("Checking local server…");
  useEffect(() => { api<{ setup_required: boolean }>("/setup").then((status) => setMessage(status.setup_required ? "OpenLab needs its first owner account. Use the setup token printed by the server." : "Your local lab is ready. Capture a part, scan a location, or search inventory.")).catch(() => setMessage("OpenLab server is unavailable. Start the Compose stack and reload.")); }, []);
  return <Shell title="Your lab, at a glance."><div className="hero"><div><p className="eyebrow">PRIVATE BY DEFAULT</p><h2>Know what you have. Build with confidence.</h2><p>{message}</p><div className="actions"><a href="/inbox">Capture something</a><a href="/inventory" className="quiet">Browse inventory</a></div></div><div className="hero-orbit"><span>○</span><small>Local<br />knowledge graph</small></div></div><section className="workspace-grid"><article><p className="eyebrow">01 · INGEST</p><h3>Capture</h3><p>Text, photos, voice, and PDFs become reviewable inventory candidates.</p><a href="/inbox">Open Inbox →</a></article><article><p className="eyebrow">02 · STORE</p><h3>Locate</h3><p>QR-labelled drawers and bins make every physical part retrievable.</p><a href="/locations">Manage locations →</a></article><article><p className="eyebrow">03 · BUILD</p><h3>Plan</h3><p>Track BOMs and allocations now; future build checks will use real stock and facts.</p><a href="/projects">Open projects →</a></article></section><section className="future-panel"><div><p className="eyebrow">MVP 4 FOUNDATION</p><h2>Answers will stay evidence-based.</h2><p>Semantic retrieval and build intelligence will cite your Things, quantities, locations, and verified hardware knowledge—not guess.</p></div><span>Planned</span></section></Shell>;
}
