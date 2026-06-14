import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // required for the multi-stage Docker runner stage

  async rewrites() {
    // API_UPSTREAM_URL is server-side only (no NEXT_PUBLIC_ prefix).
    // In local dev it defaults to localhost:8000.
    // On VM/CI set it to the real backend URL in client/.env:
    //   API_UPSTREAM_URL=http://<VM_PUBLIC_IP>:8000
    const upstream = (process.env.API_UPSTREAM_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
    return [
      {
        source: "/api/v1/:path*",
        destination: `${upstream}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;

