/** collector への薄い fetch ヘルパー。同一オリジン配信が前提（dev は Vite proxy）。 */

const TOKEN_KEY = 'triptych.apiToken';
const BASE_KEY = 'triptych.apiBase';

export function getApiBase(): string {
  return localStorage.getItem(BASE_KEY) ?? '';
}

export function setApiBase(base: string): void {
  localStorage.setItem(BASE_KEY, base);
}

export function setApiToken(token: string): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function apiGet<T>(path: string, params?: Record<string, string>): Promise<T> {
  const qs = params ? `?${new URLSearchParams(params)}` : '';
  const headers: Record<string, string> = {};
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${getApiBase()}${path}${qs}`, { headers });
  if (!res.ok) throw new ApiError(res.status, `${path}: ${res.status}`);
  return (await res.json()) as T;
}
