export const num = (n: number | null | undefined) => (n == null ? "–" : Math.round(n).toLocaleString("de-DE"));

export const pct = (n: number | null | undefined, digits = 1) =>
  n == null ? "–" : `${n.toLocaleString("de-DE", { maximumFractionDigits: digits })} %`;

export const compact = (n: number | null | undefined) =>
  n == null ? "–" : n >= 1e6 ? `${(n / 1e6).toLocaleString("de-DE", { maximumFractionDigits: 1 })} Mio.`
    : n >= 1e4 ? `${Math.round(n / 1000).toLocaleString("de-DE")} Tsd.` : num(n);

export const dateTime = (t: number) =>
  new Date(t * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

export const hhmm = (t: number) => new Date(t * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

export const day = (t: number) =>
  new Date(t * 1000).toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" });

export function relative(t: number, now = Date.now() / 1000): string {
  const d = Math.max(0, now - t);
  if (d < 60) return "gerade eben";
  if (d < 3600) return `vor ${Math.floor(d / 60)} Min.`;
  if (d < 86400) return `vor ${Math.floor(d / 3600)} Std.`;
  return `vor ${Math.floor(d / 86400)} T.`;
}

export function countdown(t: number, now = Date.now() / 1000): string {
  const d = t - now;
  if (d <= 0) return "abgelaufen";
  const days = Math.floor(d / 86400);
  const hours = Math.floor((d % 86400) / 3600);
  if (days > 0) return `${days} T. ${hours} Std.`;
  return `${hours} Std. ${Math.floor((d % 3600) / 60)} Min.`;
}

// Bilder über den Zwischenspeicher des VPS (verkleinert, lange im Cache) - Pfad relativ wegen /beta
const IMG_VERSION = 3;
export const imgSrc = (u: string | undefined, w = 640) =>
  !u ? "" : /^https:\/\/([\w-]+\.)*gtchaxonline\.com\//.test(u) ? `img?v=${IMG_VERSION}&w=${w}&u=${encodeURIComponent(u)}` : "";

export type Tone = "good" | "ok" | "bad" | "muted";
export const evTone = (p: number | null | undefined): Tone => (p == null ? "muted" : p >= 100 ? "good" : p >= 90 ? "ok" : "bad");
