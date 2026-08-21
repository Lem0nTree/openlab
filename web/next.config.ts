import type { NextConfig } from "next";

// Rewrites are compiled into the standalone build. Docker Compose therefore
// needs a service-network default; local development can override this value.
const api = process.env.OPENLAB_API_INTERNAL_URL ?? "http://openlab-server:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${api}/api/v1/:path*` }];
  },
};

export default nextConfig;
