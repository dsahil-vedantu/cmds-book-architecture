// Minimal fetch wrapper for the CMDS backend.
//
// We intentionally avoid pulling in a query library (TanStack Query, SWR,
// etc.) for the first wiring pass — plain fetch + small hooks keeps the
// shape obvious so backend swaps are easy. We can layer one on later if
// staleness/refetch policies start to matter.

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE ?? 'http://localhost:8000').replace(/\/$/, '');

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string, message?: string) {
    super(message ?? `Request failed: ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/**
 * Wrap fetch with JSON parsing, sane error throws, and a shared base URL.
 * Generic over the response shape so callers get typed results.
 */
export async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const res = await fetch(url, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new ApiError(res.status, text);
  }
  // 204 No Content support
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
