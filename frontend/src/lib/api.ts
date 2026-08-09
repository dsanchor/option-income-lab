/**
 * Server-side API base URL for the Python backend.
 *
 * The `api` container uses INTERNAL-only ingress, so the browser cannot reach
 * it directly. All calls must go through the Next.js server (route handlers /
 * server components), which proxies to the internal DNS name of the api app.
 *
 * Set API_BASE_URL in the web container's env (e.g.
 *   http://ca-option-income-lab-api.internal.<env>.azurecontainerapps.io
 * or, for local dev, http://localhost:8000).
 */
export const API_BASE_URL =
  process.env.API_BASE_URL ?? "http://localhost:8000";

type FetchOpts = RequestInit & { revalidate?: number };

/** Fetch JSON from the backend API. Server-only (uses API_BASE_URL). */
export async function apiFetch<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const { revalidate, ...init } = opts;
  const url = `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
  const res = await fetch(url, {
    ...init,
    headers: { Accept: "application/json", ...(init.headers ?? {}) },
    ...(revalidate !== undefined ? { next: { revalidate } } : {}),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API ${res.status} ${res.statusText} for ${path}: ${body.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}
