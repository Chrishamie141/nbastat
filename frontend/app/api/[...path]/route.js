const DEFAULT_BACKEND_API = "https://smartbetsports-api.vercel.app";

function backendUrl(request, path, bypass) {
  const incoming = new URL(request.url);
  const base = process.env.BACKEND_API_URL || DEFAULT_BACKEND_API;
  const target = new URL(`/api/${path.map(encodeURIComponent).join("/")}`, base);
  target.search = incoming.search;
  if (bypass) target.searchParams.set("x-vercel-protection-bypass", bypass);
  return target;
}

async function proxy(request, context) {
  const { path = [] } = await context.params;
  const headers = new Headers();
  for (const name of ["accept", "authorization", "content-type", "cookie", "user-agent"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const bypass = process.env.API_PROTECTION_BYPASS_SECRET;
  if (bypass) headers.set("x-vercel-protection-bypass", bypass);

  const method = request.method.toUpperCase();
  const upstream = await fetch(backendUrl(request, path, bypass), {
    method,
    headers,
    body: method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer(),
    cache: "no-store",
    redirect: "manual",
  });
  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.set("x-smartbets-api-proxy", "active");
  responseHeaders.set("x-smartbets-api-bypass", bypass ? "configured" : "missing");
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");
  responseHeaders.delete("transfer-encoding");
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
