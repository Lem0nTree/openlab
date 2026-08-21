"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { api, type Thing } from "@/lib/api";
import { Shell } from "./shell";
import { LabIcon, type LabIconName } from "./lab-icon";

const prompts = ["Wi-Fi air monitor", "Tiny robot", "Battery sensor"];
const captureModes: { name: string; copy: string; icon: LabIconName; href?: string; badge?: string }[] = [
  { name: "Take a picture", copy: "Recognize a board, bag or label", icon: "camera", href: "/inbox?mode=photo" },
  { name: "Forward an email", copy: "Turn an order into reviewable parts", icon: "mail", badge: "PLANNED" },
  { name: "Speak it", copy: "Describe what just arrived", icon: "mic", href: "/inbox?mode=voice" },
  { name: "Paste anything", copy: "Lists, notes and order text", icon: "text", href: "/inbox?mode=text" },
];

export function Dashboard() {
  const [serverState, setServerState] = useState<"checking" | "ready" | "setup" | "offline">("checking");
  const [things, setThings] = useState<Thing[]>([]);
  const [idea, setIdea] = useState("");
  const [draft, setDraft] = useState("");

  useEffect(() => {
    Promise.all([api<{ setup_required: boolean }>("/setup"), api<Thing[]>("/things").catch(() => [])])
      .then(([status, inventory]) => { setServerState(status.setup_required ? "setup" : "ready"); setThings(inventory); })
      .catch(() => setServerState("offline"));
  }, []);

  function startDraft(event: FormEvent) {
    event.preventDefault();
    if (idea.trim()) setDraft(idea.trim());
  }

  const visibleThings = things.slice(0, 2);

  const serverSignal = serverState === "checking"
    ? { label: "CHECKING LOCAL SERVER", tone: "checking" as const }
    : serverState === "ready"
      ? { label: "LOCAL SERVER ONLINE", tone: "ready" as const }
      : serverState === "setup"
        ? { label: "OWNER SETUP REQUIRED", tone: "setup" as const }
        : { label: "LOCAL SERVER OFFLINE", tone: "offline" as const };

  return <Shell title="Today" signal={serverSignal}>
    <section className="intelligence-hero">
      <div className="hero-glow" />
      <div className="intelligence-kicker"><span><LabIcon name="spark" /></span>OPENLAB INTELLIGENCE <i /> <b>BUILD MODE</b></div>
      <div className="hero-copy"><h2>What are we<br/><em>building today?</em></h2><p>Describe an idea. OpenLab turns your real inventory into the beginning of a build plan.</p></div>
      <form className="assistant-box" onSubmit={startDraft}>
        <span className="assistant-avatar"><LabIcon name="spark" /></span><label htmlFor="build-idea">ASK YOUR LAB</label>
        <textarea id="build-idea" value={idea} onChange={(event) => setIdea(event.target.value)} placeholder="Can I build a battery-powered plant monitor with what I have?" rows={2}/>
        <div className="assistant-footer"><div className="prompt-chips">{prompts.map((prompt) => <button type="button" key={prompt} onClick={() => setIdea(prompt)}>{prompt}</button>)}</div><button className="send-button" aria-label="Start build brief"><LabIcon name="arrow" /></button></div>
      </form>
      {draft && <div className="draft-response" role="status"><span><LabIcon name="check" /></span><div><strong>Build brief captured</strong><p>“{draft}” is ready to turn into a project. Inventory-aware AI compatibility is shown below as a preview until the intelligence workflow is implemented.</p></div><Link href="/projects">Open BUILD <LabIcon name="arrow" /></Link></div>}
      <div className="hero-status-row"><span><LabIcon name="box" /> {things.length} THINGS INDEXED</span><span><LabIcon name="bolt" /> DETERMINISTIC STOCK</span></div>
    </section>

    <section className="today-grid">
      <div className="section-intro"><div><p className="eyebrow">YOUR LAB, IN CONTEXT</p><h2>From component to connection.</h2></div><Link href="/inventory">Explore all Things <LabIcon name="arrow" /></Link></div>
      <div className="component-showcase">
        <div className="component-stack">
          {visibleThings.length > 0 ? visibleThings.map((thing, index) => <article className="thing-card" key={thing.id}>
            <div className={`part-visual visual-${index + 1}`}><span className="chip-body"><i/><i/><i/><i/><b>{thing.category.slice(0, 3).toUpperCase()}</b><i/><i/><i/><i/></span><span className="scan-line"/></div>
            <div className="thing-copy"><span className="thing-state"><i/>IN YOUR LAB</span><h3>{thing.name}</h3><p>{thing.manufacturer ?? thing.category} <span>·</span> {thing.mpn ?? "No MPN recorded"}</p><div className="thing-meta"><span><LabIcon name="layers"/>{thing.category}</span><span><LabIcon name="pin"/>Location linked</span></div></div>
            <Link href="/inventory" aria-label={`View ${thing.name}`}><LabIcon name="arrow" /></Link>
          </article>) : <article className="thing-card empty-thing"><div className="part-visual"><LabIcon name="chip"/></div><div className="thing-copy"><span className="thing-state muted">INVENTORY READY</span><h3>Your components live here.</h3><p>Capture the first Thing to build an inventory-aware workspace.</p></div><Link href="/inbox"><LabIcon name="plus" /></Link></article>}
          <Link className="mini-inventory-link" href="/inventory"><span><LabIcon name="search"/>Search your complete inventory</span><LabIcon name="arrow"/></Link>
        </div>

        <article className="intelligence-panel">
          <div className="preview-label"><span><LabIcon name="spark"/>INTELLIGENCE PREVIEW</span><b>ROADMAP</b></div>
          <div className="intel-title"><div><p className="eyebrow">CONNECTION ADVISOR</p><h3>Pinout, without the guesswork.</h3></div><span className="confidence-ring">98<small>%</small></span></div>
          <div className="pinout-board">
            <div className="pin-side left"><span><b>3V3</b><i className="power"/></span><span><b>GND</b><i/></span><span><b>GPIO 4</b><i className="data"/></span><span><b>GPIO 5</b><i className="data"/></span></div>
            <div className="board-core"><span className="board-chip">C3</span><small>ESP32-C3<br/>SUPERMINI</small><i className="board-led"/></div>
            <div className="pin-side right"><span><i className="data"/><b>SDA</b></span><span><i className="data"/><b>SCL</b></span><span><i/><b>GND</b></span><span><i className="power"/><b>5V</b></span></div>
          </div>
          <div className="suggestion-list">
            <div><span className="suggest-icon good"><LabIcon name="check"/></span><p><b>Use GPIO 4 + 5</b><small>Safe I²C pair for this build concept</small></p><span className="suggest-tag">RECOMMENDED</span></div>
            <div><span className="suggest-icon swap"><LabIcon name="layers"/></span><p><b>BME280 can replace DHT22</b><small>Same role · better precision · I²C</small></p><span className="suggest-tag cyan">ALTERNATIVE</span></div>
          </div>
        </article>
      </div>
    </section>

    <section className="capture-feature">
      <div className="capture-lead"><span className="capture-symbol"><LabIcon name="plus"/></span><p className="eyebrow">UNIVERSAL INBOX</p><h2>Your inventory should<br/>update at the speed of thought.</h2><p>Skip the forms. Give OpenLab the messy input you already have, then review every candidate before stock changes.</p><Link href="/inbox">Open capture inbox <LabIcon name="arrow"/></Link></div>
      <div className="capture-methods">{captureModes.map((mode) => {
        const content = <><span className="capture-method-icon"><LabIcon name={mode.icon}/></span><div><h3>{mode.name}</h3><p>{mode.copy}</p></div>{mode.badge ? <b>{mode.badge}</b> : <LabIcon className="method-arrow" name="arrow"/>}</>;
        return mode.href ? <Link key={mode.name} href={mode.href} className="capture-method">{content}</Link> : <div key={mode.name} className="capture-method is-planned">{content}</div>;
      })}</div>
      <div className="ingest-flow"><span>INGEST</span><i/><span>UNDERSTAND</span><i/><span>REVIEW</span><i/><span>STORE</span></div>
    </section>
  </Shell>;
}
