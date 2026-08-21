"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Shell } from "./shell";

export function Dashboard() {
  const [message, setMessage] = useState("Checking local server…");
  useEffect(() => { api<{ setup_required: boolean }>("/setup").then((status) => setMessage(status.setup_required ? "OpenLab needs its first owner account. Use the setup token printed by the server." : "Your local lab is ready. Capture a part, scan a location, or search inventory.")).catch(() => setMessage("OpenLab server is unavailable. Start the Compose stack and reload.")); }, []);
  return <Shell title="Your electronics lab"><div className="hero"><h2>Your inventory, your hardware, your data.</h2><p>{message}</p><div className="actions"><a href="/inbox">Capture to Inbox</a><a href="/inventory" className="quiet">Browse inventory</a><a href="/login" className="quiet">Sign in</a></div></div><div className="grid"><article><h3>Capture</h3><p>Text, photos, screenshots, voice, and PDFs enter one reviewable Inbox.</p></article><article><h3>Locate</h3><p>QR-labelled drawers and bins keep physical hardware retrievable.</p></article><article><h3>Build</h3><p>Projects and compatibility checks remain grounded in actual stock.</p></article></div></Shell>;
}
