const productionApi = 'https://smartbetsports-api.vercel.app';
const configuredApi =
  process.env.BACKEND_API_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL;
const isLocalRuntime = process.env.NODE_ENV !== 'production';
const targetsProduction = configuredApi?.includes('smartbetsports-api.vercel.app');
if (
  isLocalRuntime &&
  targetsProduction &&
  process.env.ALLOW_LOCAL_PRODUCTION_MUTATIONS !== 'true'
) {
  throw new Error(
    'Local development is configured for the production API. Use a local/test backend or explicitly set ALLOW_LOCAL_PRODUCTION_MUTATIONS=true.',
  );
}
const backendApi = configuredApi || (isLocalRuntime ? 'http://127.0.0.1:8000' : productionApi);
const nextConfig = {
  skipTrailingSlashRedirect: true,
  images: { unoptimized: true, remotePatterns: [{ protocol: 'https', hostname: 'a.espncdn.com' }] },
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${backendApi}/api/:path*` }];
  },
};
export default nextConfig;
