import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /**
   * Same-origin API proxy (Replit and other single-port hosts): set
   * NEXT_PUBLIC_API_URL="/api" and the browser's /api/* requests are
   * forwarded to the FastAPI app on 127.0.0.1:8000.
   */
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/:path*",
      },
    ];
  },
};

export default nextConfig;
