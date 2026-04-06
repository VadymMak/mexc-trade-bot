import type { NextRequest } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

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

  const data = await response.json();
  return Response.json(data, { status: response.status });
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

  const data = await response.json();
  return Response.json(data, { status: response.status });
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

  const data = await response.json();
  return Response.json(data, { status: response.status });
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

  const data = await response.json();
  return Response.json(data, { status: response.status });
}
