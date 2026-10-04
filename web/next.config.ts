import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Same reason as the KYC demo: the dev-tools bubble sits on top of every local screenshot.
  devIndicators: false,
  // No x-powered-by header: it advertises the framework and version for nothing in return.
  poweredByHeader: false,
};

export default nextConfig;
