/** @type {import('next').NextConfig} */
const nextConfig = {
  // In development (no NEXT_PUBLIC_API_URL set), proxy /api/* to local FastAPI.
  // In production, NEXT_PUBLIC_API_URL is an absolute URL so fetch() bypasses rewrites.
  async rewrites() {
    if (process.env.NEXT_PUBLIC_API_URL) return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

module.exports = nextConfig;
