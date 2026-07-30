import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle (.next/standalone) for a slim
  // production Docker image — see frontend/Dockerfile.
  output: "standalone",
};

export default nextConfig;
