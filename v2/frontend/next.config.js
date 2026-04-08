/** @type {import('next').NextConfig} */
const nextConfig = {
  // Proxy API calls to FastAPI backend in development
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:8000/api/:path*',
      },
    ];
  },
};

module.exports = nextConfig;
