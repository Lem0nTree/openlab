import Link from "next/link";
import type { ReactNode } from "react";

export function Shell({ title, children }: { title: string; children: ReactNode }) {
  return <main className="app-frame">
    <header className="topbar"><Link href="/" className="brand"><span className="brand-mark">O</span>OpenLab</Link><nav><Link href="/inventory">Inventory</Link><Link href="/locations">Locations</Link><Link href="/inbox">Inbox</Link><Link href="/projects">Projects</Link></nav><span className="local-status"><i />Local</span></header>
    <div className="shell">
      <aside className="sidebar"><p className="nav-label">WORKSPACE</p><Link href="/">Overview</Link><Link href="/inbox">Capture inbox</Link><Link href="/inventory">Inventory</Link><Link href="/locations">Locations</Link><Link href="/projects">Projects</Link><div className="sidebar-future"><p className="nav-label">COMING NEXT</p><span>Knowledge</span><span>Build assistant</span></div></aside>
      <section className="content"><section className="page-title"><p className="eyebrow">INGEST · UNDERSTAND · STORE · BUILD</p><h1>{title}</h1></section>{children}</section>
    </div>
  </main>;
}
