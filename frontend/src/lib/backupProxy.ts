import { API_BASE_URL } from "@/lib/api";

const PASSTHROUGH_HEADERS = [
  "content-type",
  "content-disposition",
  "x-backup-export-id",
  "x-backup-content-sha256",
  "x-backup-archive-sha256",
] as const;

export async function proxyBackupRequest(
  request: Request,
  backendPath: string,
): Promise<Response> {
  const headers = new Headers();
  headers.set("accept", request.headers.get("accept") ?? "application/json");
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  const body = ["GET", "HEAD"].includes(request.method)
    ? undefined
    : await request.arrayBuffer();

  try {
    const upstream = await fetch(`${API_BASE_URL}${backendPath}`, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
    });
    const responseHeaders = new Headers({ "cache-control": "no-store" });
    for (const name of PASSTHROUGH_HEADERS) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
