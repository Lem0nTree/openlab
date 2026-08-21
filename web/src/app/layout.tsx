import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "OpenLab",
  description: "Local-first intelligence for your electronics lab",
  manifest: "/manifest.webmanifest",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}

