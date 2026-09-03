const allowedPrefixes = [
  '/dashboard', '/games', '/parlays', '/fantasy', '/history', '/performance',
  '/account', '/analyze', '/players', '/predictions', '/internal', '/nfl', '/subscribe',
];

export function safeNextPath(value, fallback = '/dashboard') {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return fallback;
  try {
    const parsed = new URL(value, 'https://smartbetsports.com');
    if (parsed.origin !== 'https://smartbetsports.com') return fallback;
    const path = parsed.pathname.replace(/\/{2,}/g, '/');
    return allowedPrefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`)) ? path : fallback;
  } catch {
    return fallback;
  }
}
