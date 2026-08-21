import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return { name: "OpenLab", short_name: "OpenLab", display: "standalone", start_url: "/", background_color: "#101713", theme_color: "#16794a", icons: [] };
}

