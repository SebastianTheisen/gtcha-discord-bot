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

// Bilder über den Zwischenspeicher des VPS (verkleinert, lange im Cache). Absoluter Pfad /img: liefert die
// Live-App (gleicher Ursprung) - dort laden die Bilder nachweislich. Fehlt ein Bild, lädt main.tsx es direkt von GTCHA.
const IMG_VERSION = 4;
const WIDTHS = [320, 640, 960];
export const imgSrc = (u: string | undefined, w = 640) =>
  !u ? "" : /^https:\/\/([\w-]+\.)*gtchaxonline\.com\//.test(u)
    ? `/img?v=${IMG_VERSION}&w=${WIDTHS.find((x) => x >= w) || 960}&u=${encodeURIComponent(u)}` : "";

export const shortTime = (t: number) =>
  new Date(t * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).replace(",", "");

// Verkaufsende einheitlich in deutscher Zeit (die Seite liefert verschiedene Formate, teils japanisch)
export function untilText(b: { archived?: boolean; ended_at?: number | null; end_ts?: number | null; end?: string | null }): string {
  if (b.archived) return b.ended_at ? `Beendet am ${dateTime(b.ended_at)} Uhr` : "Beendet";
  if (b.end_ts) {
    const d = new Date(b.end_ts * 1000);
    const date = d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "Europe/Berlin" });
    const clock = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
    return `Erhältlich bis ${date}, ${clock} Uhr`;
  }
  const t = String(b.end || "");
  return /erhältlich/i.test(t) ? t : t ? `Erhältlich bis ${t}` : "";
}

export const signedNum = (n: number) => `${n > 0 ? "+" : ""}${num(n)}`;
export const ddmm = (s?: string | null) => (s ? `${s.slice(8, 10)}.${s.slice(5, 7)}.` : "–");
export const ddmmhhmm = (s?: string | null) => (s ? `${s.slice(8, 10)}.${s.slice(5, 7)}. ${s.slice(11, 16)}` : "–");

export type Tone = "good" | "ok" | "bad" | "muted";
export const evTone = (p: number | null | undefined): Tone => (p == null ? "muted" : p >= 100 ? "good" : p >= 90 ? "ok" : "bad");

// Kartennamen der Seite haben teils eine offene Klammer ohne Gegenstück ("[Lugia V") - für die Anzeige weglassen
export function cardName(name: string): string {
  let out = name.trim();
  const pairs: [string, string][] = [["[", "]"], ["【", "】"], ["(", ")"], ["（", "）"]];
  for (const [open, close] of pairs) {
    const opens = out.split(open).length - 1;
    const closes = out.split(close).length - 1;
    if (opens > closes && out.startsWith(open)) out = out.slice(1).trim();
    else if (closes > opens && out.endsWith(close)) out = out.slice(0, -1).trim();
  }
  return out;
}
