import Link from "next/link";
import type { ReactNode } from "react";

export function Shell({ title, children }: { title: string; children: ReactNode }) {
  return <main className="shell">
    <header><Link href="/" className="brand">OpenLab</Link><nav><Link href="/inventory">Inventory</Link><Link href="/locations">Locations</Link><Link href="/inbox">Inbox</Link><Link href="/projects">Projects</Link></nav></header>
    <section className="page-title"><p>INGEST → UNDERSTAND → STORE → BUILD</p><h1>{title}</h1></section>
    {children}
  </main>;
}
