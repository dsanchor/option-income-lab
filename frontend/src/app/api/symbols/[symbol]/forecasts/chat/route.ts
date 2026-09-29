import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

const ALLOWED_FIELDS = new Set(["mode", "range", "message", "history"]);

export async function POST(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const contentLength = Number(req.headers.get("content-length") ?? "0");
  if (Number.isFinite(contentLength) && contentLength > 128 * 1024) {
    return Response.json({ error: "Request body is too large" }, { status: 413 });
  }
  const rawBody = await req.text();
  if (new TextEncoder().encode(rawBody).byteLength > 128 * 1024) {
    return Response.json({ error: "Request body is too large" }, { status: 413 });
  }
  const mediaType = req.headers
    .get("content-type")
    ?.split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (rawBody.length > 0 && mediaType !== "application/json") {
    return Response.json(
      { error: "Content-Type must be application/json" },
      { status: 415 },
    );
  }
  let incoming: unknown;
  try {
    incoming = JSON.parse(rawBody);
  } catch {
    return Response.json({ error: "Malformed JSON body" }, { status: 400 });
  }
  if (!incoming || typeof incoming !== "object" || Array.isArray(incoming)) {
    return Response.json({ error: "JSON body must be an object" }, { status: 400 });
  }
  const unknown = Object.keys(incoming).filter((key) => !ALLOWED_FIELDS.has(key));
  if (unknown.length) {
    return Response.json(
      { error: `Unknown field(s): ${unknown.sort().join(", ")}` },
      { status: 400 },
    );
  }
  const body = Object.fromEntries(
    Object.entries(incoming).filter(([key]) => ALLOWED_FIELDS.has(key)),
  );
  const { symbol: rawSymbol } = await params;
  const symbol = decodeSymbolParam(rawSymbol);
  try {
    const upstream = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/forecasts/chat`,
      {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      },
    );
    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json({ error: "Upstream API unavailable" }, { status: 502 });
  }
}
