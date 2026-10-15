/** collector への薄い fetch ヘルパー。同一オリジン配信が前提（dev は Vite proxy）。 */

export const TOKEN_KEY = 'triptych.apiToken';
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
    /** サーバーが返した `{"detail": "..."}`（文字列のときだけ）。同じ 404 でも理由で出し分けるのに使う。 */
    public detail?: string,
  ) {
    super(message);
  }
}

/** エラー応答の本文から FastAPI の `detail` を取り出す。JSON でない・文字列でない（422 の配列など）ときは undefined。 */
export async function readErrorDetail(res: Response): Promise<string | undefined> {
  try {
    const body: unknown = await res.json();
    const detail = (body as { detail?: unknown } | null)?.detail;
    return typeof detail === 'string' ? detail : undefined;
  } catch {
    return undefined;
  }
}

export async function apiGet<T>(path: string, params?: Record<string, string>): Promise<T> {
  const qs = params ? `?${new URLSearchParams(params)}` : '';
  const headers: Record<string, string> = {};
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${getApiBase()}${path}${qs}`, { headers });
  if (!res.ok) throw new ApiError(res.status, `${path}: ${res.status}`, await readErrorDetail(res));
  return (await res.json()) as T;
}
