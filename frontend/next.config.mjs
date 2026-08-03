const backendApi = process.env.BACKEND_API_URL || 'https://smartbetsports-api.vercel.app';
const nextConfig = {
  images: { unoptimized: true, remotePatterns: [{ protocol: 'https', hostname: 'a.espncdn.com' }] },
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${backendApi}/api/:path*` }];
  },
};
export default nextConfig;
