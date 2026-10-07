// Gemeinsamer Zustand (Preact Signals) und Live-Verbindung zum Server.
import { signal } from "@preact/signals";
import { api, authApi, getToken, LockedError, type LockInfo } from "./api";
import { applyProfile, wishList, type Profile } from "./local";
import type { Banner, Me } from "./types";

export const banners = signal<Banner[] | null>(null);
export const updated = signal(0);
export const locked = signal<LockInfo | null>(null);
export const loadError = signal<string | null>(null);
export const live = signal<"connecting" | "live" | "offline">("connecting");
export const me = signal<Me | null | undefined>(undefined);
// Banner, deren Daten sich beim letzten Live-Update geändert haben (kurz hervorheben)
export const changedIds = signal<Set<number>>(new Set());
// Banner mit noch nicht gezogenen Wunschkarten: ID -> Kartennamen
export const wishBanners = signal<Map<number, string[]>>(new Map());
// Zähler: erhöht sich bei jedem Live-Update oder "Ziehen zum Aktualisieren" - Seiten laden dann ihre Daten neu
export const refreshTick = signal(0);
export const route = signal(location.hash.slice(1) || "/");

addEventListener("hashchange", () => {
  route.value = location.hash.slice(1) || "/";
});

let lastSnapshot = new Map<number, string>();

export async function loadBanners() {
  try {
    const data = await api<{ banners: Banner[]; updated: number }>("api/banners");
    const snapshot = new Map(data.banners.map((b) => [b.id, `${b.remaining}|${b.status}|${b.ev_pct}|${b.ship_cards}`]));
    if (lastSnapshot.size) {
      const changed = new Set<number>();
      snapshot.forEach((v, id) => {
        if (lastSnapshot.get(id) !== v) changed.add(id);
      });
      if (changed.size) {
        changedIds.value = changed;
        setTimeout(() => (changedIds.value = new Set()), 2500);
      }
    }
    lastSnapshot = snapshot;
    banners.value = data.banners;
    updated.value = data.updated;
    locked.value = null;
    loadError.value = null;
  } catch (e) {
    if (e instanceof LockedError) locked.value = e.info;
    else loadError.value = "Server nicht erreichbar";
  }
}

// Banner mit Wunschkarten (noch nicht gezogen) für die Kategorie "⭐ Wunschkarten"
export async function loadWishBanners() {
  const wish = wishList.value;
  if (!wish.length) {
    wishBanners.value = new Map();
    return;
  }
  try {
    const res = await api<{ cards: { name: string; banners: { id: number; out?: boolean }[] }[] }>(
      `api/cards?ids=${wish.map((w) => w.id).join(",")}`);
    const map = new Map<number, string[]>();
    for (const c of res.cards) for (const wb of c.banners) if (!wb.out) map.set(wb.id, [...(map.get(wb.id) || []), c.name]);
    wishBanners.value = map;
  } catch {
    /* ohne Zugang keine Wunsch-Banner */
  }
}

export async function loadMe() {
  if (!getToken()) {
    me.value = null;
    return;
  }
  try {
    me.value = await api<Me>("api/me");
  } catch (e) {
    me.value = null;
    if (String((e as Error).message) === "401") {
      try {
        localStorage.setItem("deviceToken", "");
      } catch {
        /* egal */
      }
    }
  }
  // Rang/Aufladung aus dem letzten Übertragen übernehmen
  if (me.value) authApi<Profile>("api/me/profile").then(applyProfile).catch(() => {});
}

export function refreshAll() {
  refreshTick.value++;
  return loadBanners();
}

// Live: Server-Sent Events melden jede Änderung der Banner-Daten. Fällt die Verbindung weg (z. B. App im
// Hintergrund), fragt die App alle 30 s nach und verbindet sich neu, sobald sie wieder sichtbar ist.
let source: EventSource | null = null;
let poll: number | undefined;
let pending: number | undefined;
let retry: number | undefined;

function scheduleLoad(tick: boolean) {
  clearTimeout(pending);
  pending = window.setTimeout(() => {
    if (tick) refreshTick.value++;
    loadBanners();
  }, 300);
}

export function connectLive() {
  source?.close();
  clearTimeout(retry);
  if (document.hidden) return;
  live.value = "connecting";
  source = new EventSource(`api/stream?t=${encodeURIComponent(getToken())}`);
  source.addEventListener("hello", () => {
    live.value = "live";
    scheduleLoad(false);
  });
  source.addEventListener("update", () => {
    live.value = "live";
    scheduleLoad(true);
  });
  source.onerror = () => {
    live.value = "offline";
    source?.close();
    source = null;
    retry = window.setTimeout(connectLive, 15000);
  };
  clearInterval(poll);
  poll = window.setInterval(() => {
    if (live.value !== "live" && !document.hidden) loadBanners();
  }, 30000);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    source?.close();
    source = null;
  } else {
    refreshAll();
    connectLive();
  }
});

// Einstellungen pro Gerät (nur Beta)
export function pref<T extends string>(key: string, fallback: T) {
  let initial = fallback;
  try {
    initial = (localStorage.getItem(`beta.${key}`) as T) || fallback;
  } catch {
    /* privater Modus */
  }
  const s = signal<T>(initial);
  s.subscribe((v) => {
    try {
      localStorage.setItem(`beta.${key}`, v);
    } catch {
      /* privater Modus */
    }
  });
  return s;
}

export const theme = pref<"auto" | "dark" | "light">("theme", "auto");
export const category = pref<string>("category", "Alle");
export const sort = pref<string>("sort", "ev");
export const listView = pref<string>("listView", "big");
export const search = signal("");
