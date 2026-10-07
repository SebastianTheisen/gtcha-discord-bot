// Zugriff auf die Server-API. Beta läuft unter /beta auf demselben Ursprung wie die Live-App: Geräte-Token
// (localStorage "deviceToken") gilt für beide, sofern derselbe Speicher genutzt wird (iOS trennt installierte Apps).

export class LockedError extends Error {
  constructor(public info: LockInfo) {
    super("locked");
  }
}

export interface LockAccount { label: string; account?: string; gtcha_id?: string; last_sync: string; ok: boolean; days_left: number }
export interface LockInfo {
  reason?: "link" | "sync" | "accounts";
  last_sync?: string;
  days?: number;
  days_left?: number;
  ok?: boolean;
  exempt?: boolean;
  expected?: number;
  missing?: number;
  accounts?: LockAccount[];
}

export const getToken = (): string => {
  try {
    return localStorage.getItem("deviceToken") || "";
  } catch {
    return "";
  }
};

export const setToken = (token: string) => {
  try {
    localStorage.setItem("deviceToken", token);
  } catch {
    /* privater Modus */
  }
};

// relative Pfade: funktionieren unter / (lokal) und unter /beta/ (VPS)
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(path.replace(/^\//, ""), {
    cache: "no-store",
    ...init,
    headers: { ...(init?.headers || {}), ...(token ? { "X-Device-Token": token } : {}) },
  });
  if (res.status === 423) {
    const body = await res.json().catch(() => ({}));
    throw new LockedError(body.locked || {});
  }
  if (!res.ok) throw new Error(String(res.status));
  return res.json() as Promise<T>;
}

export const post = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// Wie authApi in der Live-App: mit Geräte-Token, Fehlermeldung des Servers als Error-Text
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function authApi<T = any>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path.replace(/^\//, ""), {
    method: body ? "POST" : "GET",
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-Device-Token": getToken() },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(async () => ({ error: await res.text().catch(() => "") }));
  if (!res.ok) throw new Error(data.error || data.text || `Fehler ${res.status}`);
  return data as T;
}
