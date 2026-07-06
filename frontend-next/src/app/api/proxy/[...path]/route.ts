import type { NextRequest } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

// Headers forwarded verbatim when streaming a non-JSON upstream response
// (e.g. CSV downloads) straight through to the browser.
const PASSTHROUGH_HEADERS = [
  "content-type",
  "content-disposition",
  "content-length",
  "cache-control",
];

/**
 * Relay an upstream fetch response to the client.
 * - application/json → parse + re-emit as JSON (unchanged dashboard behavior).
 * - anything else    → stream the body through with the upstream status and
 *                      file-download headers (Content-Type/Content-Disposition/…).
 * Non-JSON bodies are streamed, never buffered in memory.
 */
async function relay(upstream: Response, jsonInit?: ResponseInit): Promise<Response> {
  const ct = upstream.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    const data = await upstream.json();
    return Response.json(data, { status: upstream.status, ...(jsonInit ?? {}) });
  }
  const headers = new Headers();
  for (const h of PASSTHROUGH_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) headers.set(h, v);
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}

export async function GET(
  request: NextRequest,
  ctx: RouteContext<"/api/proxy/[...path]">
) {
  const { path } = await ctx.params;
  const pathStr = path.join("/");
  const url = request.nextUrl;
  const backendUrl = `${BACKEND_URL}/${pathStr}${url.search}`;

  const response = await fetch(backendUrl, {
    headers: { "Content-Type": "application/json" },
  });

  return relay(response, {
    headers: {
      'Cache-Control': 'public, s-maxage=10, stale-while-revalidate=20',
    },
  });
}

export async function POST(
  request: NextRequest,
  ctx: RouteContext<"/api/proxy/[...path]">
) {
  const { path } = await ctx.params;
  const pathStr = path.join("/");
  const url = request.nextUrl;
  const body = await request.json();
  const backendUrl = `${BACKEND_URL}/${pathStr}${url.search}`;

  const upstream = request.headers.get("X-Idempotency-Key");
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (upstream) headers["X-Idempotency-Key"] = upstream;

  const response = await fetch(backendUrl, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  return relay(response);
}

export async function PUT(
  request: NextRequest,
  ctx: RouteContext<"/api/proxy/[...path]">
) {
  const { path } = await ctx.params;
  const pathStr = path.join("/");
  const url = request.nextUrl;
  const body = await request.json();

  const response = await fetch(`${BACKEND_URL}/${pathStr}${url.search}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  return relay(response);
}

export async function DELETE(
  request: NextRequest,
  ctx: RouteContext<"/api/proxy/[...path]">
) {
  const { path } = await ctx.params;
  const pathStr = path.join("/");
  const url = request.nextUrl;

  const response = await fetch(`${BACKEND_URL}/${pathStr}${url.search}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });

  return relay(response);
}
