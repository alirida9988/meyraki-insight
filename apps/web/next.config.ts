import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits .next/standalone: a self-contained server with only the packages actually
  // imported, so the runtime image carries no pnpm store and no build toolchain.
  output: "standalone",
};

export default nextConfig;
