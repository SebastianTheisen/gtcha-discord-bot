// Auf dem Gerät gespeicherte Angaben - dieselben Schlüssel wie die Live-App, damit beide sie teilen
import { signal } from "@preact/signals";
import type { Banner } from "./types";

export function load(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

export function save(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* privater Modus */
  }
}

// --- Mitgliedsrang und Aufladung (für "Für mich kaufbar") ---
export const RANKS: [string, string][] = [["white", "Weiß"], ["bronze", "Bronze"], ["silver", "Silber"], ["gold", "Gold"],
  ["rainbow", "Rainbow"], ["black", "Black"]];
export const rankLabel = (k?: string | null) => (RANKS.find(([key]) => key === k) || [null, k || "–"])[1];

export const myRank = signal(load("myRank", ""));
export const myCharge = signal(Number(load("myCharge", "0")) || 0);
myRank.subscribe((v) => save("myRank", v));
myCharge.subscribe((v) => save("myCharge", String(v)));

export interface Profile { updated_at?: string; rank?: string; charge?: number | null; charge_yen?: number | null }

// Rang und Aufladung aus dem letzten "Alles übertragen" - nur wenn es neue Daten gibt (Handänderung gilt bis dahin)
export function applyProfile(p?: Profile | null): boolean {
  if (!p || !p.updated_at || (!p.rank && p.charge == null)) return false;
  const stamp = `${p.updated_at}|${p.rank}|${p.charge}`;
  if (load("profileApplied", "") === stamp) return false;
  if (p.rank) myRank.value = p.rank;
  if (p.charge != null) myCharge.value = p.charge;
  save("profileApplied", stamp);
  return true;
}

// true = kann ich kaufen, false = nicht, null = unbekannt (Rang nicht eingestellt)
export function canBuy(b: Banner): boolean | null {
  if (!myRank.value) return null;
  if (b.password) return false;
  if (b.ranks?.length && !b.ranks.includes(myRank.value)) return false;
  if (b.min_charge && myCharge.value < b.min_charge) return false;
  return true;
}

export function whyNot(b: Banner): string {
  if (b.password) return "nur mit Passwort";
  if (b.ranks?.length && !b.ranks.includes(myRank.value)) return "nicht für deinen Mitgliedsrang";
  if (b.min_charge && myCharge.value < b.min_charge) return `erst ab ${b.min_charge.toLocaleString("de-DE")} Coins Aufladung im Monat`;
  return "";
}

// --- Wunschliste ---
export interface Wish { id: string; name: string; image?: string }
const readWish = (): Wish[] => {
  try {
    return JSON.parse(load("wish", "[]"));
  } catch {
    return [];
  }
};
export const wishList = signal<Wish[]>(readWish());
export const isWish = (id: string) => wishList.value.some((w) => w.id === id);

// --- GTCHA-Seite öffnen in (installierte App auf dem iPhone: echten Browser nehmen, dort ist man eingeloggt) ---
export const LINK_MODES: Record<string, string> = { safari: "Safari", chrome: "Chrome", app: "in der App" };
export const linkMode = signal(load("linkMode", "safari"));
linkMode.subscribe((v) => save("linkMode", v));
export const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
export const isStandalone = () =>
  (navigator as Navigator & { standalone?: boolean }).standalone === true || matchMedia("(display-mode: standalone)").matches;
export function buyHref(url: string): string {
  if (!isStandalone() || !isIOS() || linkMode.value === "app") return url;
  return linkMode.value === "chrome" ? url.replace(/^https:/, "googlechromes:") : `x-safari-${url}`;
}
