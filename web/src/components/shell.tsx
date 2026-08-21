"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { LabIcon, type LabIconName } from "./lab-icon";

const navItems: { href: string; label: string; icon: LabIconName }[] = [
  { href: "/", label: "Today", icon: "grid" },
  { href: "/inbox", label: "Capture", icon: "inbox" },
  { href: "/inventory", label: "Things", icon: "box" },
  { href: "/locations", label: "Locations", icon: "pin" },
  { href: "/projects", label: "Builds", icon: "folder" },
];

type ShellSignal = { label: string; tone: "ready" | "offline" | "checking" | "setup" };

export function Shell({ title, children, signal }: { title: string; children: ReactNode; signal?: ShellSignal }) {
  const pathname = usePathname();
  return <main className="app-frame">
    <header className="topbar">
      <Link href="/" className="brand"><span className="brand-mark"><LabIcon name="spark" /></span><span>Open<span>Lab</span></span></Link>
      <span className="lab-context"><span>LAB /</span> HOME BENCH</span>
      <div className="topbar-actions"><span className="local-status"><i />LAB ONLINE</span><Link href="/settings" className="icon-link" aria-label="Settings"><LabIcon name="settings" /></Link></div>
    </header>
    <div className="shell">
      <aside className="sidebar">
        <p className="nav-label">LABORATORY</p>
        <nav className="side-nav">{navItems.map((item) => <Link key={item.href} href={item.href} aria-current={pathname === item.href ? "page" : undefined}><LabIcon name={item.icon}/><span>{item.label}</span>{pathname === item.href && <i />}</Link>)}</nav>
        <div className="sidebar-intel"><span className="intel-orb"><LabIcon name="spark" /></span><div><strong>Lab intelligence</strong><small>Local data. Your rules.</small></div></div>
        <div className="sidebar-future"><p className="nav-label">SYSTEM</p><Link href="/settings"><LabIcon name="settings" />Settings</Link></div>
      </aside>
      <section className="content"><section className="page-title"><div><p className="eyebrow">INGEST <i/> UNDERSTAND <i/> STORE <i/> BUILD</p><h1>{title}</h1></div>{signal && <span className={`page-signal ${signal.tone}`}><i/>{signal.label}</span>}</section>{children}</section>
    </div>
    <nav className="mobile-nav">{navItems.map((item) => <Link key={item.href} href={item.href} aria-current={pathname === item.href ? "page" : undefined}><LabIcon name={item.icon}/><span>{item.label}</span></Link>)}</nav>
  </main>;
}
