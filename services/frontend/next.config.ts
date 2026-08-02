import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits .next/standalone: a self-contained server plus only the node_modules
  // it actually imports, which is what the Docker image ships. Harmless
  // otherwise — `next dev` and `next start` are unaffected.
  output: "standalone",
};

export default nextConfig;
