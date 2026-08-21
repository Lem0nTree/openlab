"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Shell } from "./shell";

export function Dashboard() {
  const [message, setMessage] = useState("Checking local server…");
  useEffect(() => { api<{ setup_required: boolean }>("/setup").then((status) => setMessage(status.setup_required ? "OpenLab needs its first owner account. Use the setup token printed by the server." : "Your local lab is ready. Capture a part, scan a location, or search inventory.")).catch(() => setMessage("OpenLab server is unavailable. Start the Compose stack and reload.")); }, []);
  return <Shell title="Overview"><section className="hero"><p className="eyebrow">LOCAL LAB</p><h2>A clear view of what you have and what you can build.</h2><p>{message}</p><div className="actions"><a href="/inbox">Add a capture</a><a href="/inventory" className="quiet">Open inventory</a></div></section><section className="workspace-grid"><article><p className="eyebrow">INBOX</p><h3>Capture and review</h3><p>Turn a note, photo, or voice memo into a candidate without writing unverified data.</p><a href="/inbox">Open inbox →</a></article><article><p className="eyebrow">INVENTORY</p><h3>Find a part</h3><p>Search Things and follow their physical location with QR labels.</p><a href="/inventory">Search inventory →</a></article><article><p className="eyebrow">PROJECTS</p><h3>Plan a build</h3><p>Track requirements, reservations, and allocations against real stock.</p><a href="/projects">Open projects →</a></article></section></Shell>;
}
