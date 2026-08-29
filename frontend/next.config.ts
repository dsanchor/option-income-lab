import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle (.next/standalone) for a slim
  // production Docker image — see frontend/Dockerfile.
  output: "standalone",

  // Root path always lands on the dashboard.
  async redirects() {
    return [
      {
        source: "/",
        destination: "/dashboard",
        permanent: false,
      },
      // DGI moved under the new "Screener" nav grouping alongside Options
      // (`.squad/decisions/inbox/copilot-options-screener-approved.md`) —
      // not permanent, matching the "/" -> "/dashboard" convention above,
      // since this is an in-app reorg rather than a canonical URL change.
      {
        source: "/dgi",
        destination: "/screener/dgi",
        permanent: false,
      },
      {
        source: "/dgi/analyze/:symbol",
        destination: "/screener/dgi/analyze/:symbol",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
