// Gemeinsamer Zustand (Preact Signals) und Live-Verbindung zum Server.
import { signal, computed } from "@preact/signals";
import { api, getToken, LockedError, type LockInfo } from "./api";
import type { Banner, Me } from "./types";

export const banners = signal<Banner[] | null>(null);
export const updated = signal(0);
export const locked = signal<LockInfo | null>(null);
export const loadError = signal<string | null>(null);
export const live = signal<"connecting" | "live" | "offline">("connecting");
export const me = signal<Me | null | undefined>(undefined);
// Banner, deren Daten sich beim letzten Live-Update geändert haben (kurz hervorheben)
export const changedIds = signal<Set<number>>(new Set());
export const route = signal(location.hash.slice(1) || "/");

addEventListener("hashchange", () => {
  route.value = location.hash.slice(1) || "/";
});

export const navigate = (path: string) => {
  location.hash = path;
};

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

export async function loadMe() {
  try {
    me.value = await api<Me>("api/me");
  } catch {
    me.value = null;
  }
}

// Live: Server-Sent Events melden jede Änderung der Banner-Daten. Fällt die Verbindung weg (z. B. App im
// Hintergrund), fragt die App jede Minute nach und verbindet sich neu, sobald sie wieder sichtbar ist.
let source: EventSource | null = null;
let poll: number | undefined;
let pending: number | undefined;

function scheduleLoad() {
  clearTimeout(pending);
  pending = window.setTimeout(loadBanners, 300);
}

export function connectLive() {
  source?.close();
  if (document.hidden) return;
  live.value = "connecting";
  source = new EventSource(`api/stream?t=${encodeURIComponent(getToken())}`);
  source.addEventListener("hello", () => {
    live.value = "live";
    scheduleLoad();
  });
  source.addEventListener("update", () => {
    live.value = "live";
    scheduleLoad();
    window.dispatchEvent(new Event("gtcha:update"));
  });
  source.onerror = () => {
    live.value = "offline";
    source?.close();
    source = null;
    setTimeout(connectLive, 15000);
  };
  clearInterval(poll);
  poll = window.setInterval(() => {
    if (live.value !== "live") loadBanners();
  }, 60000);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    source?.close();
    source = null;
  } else {
    loadBanners();
    connectLive();
  }
});

// Einstellungen pro Gerät
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
export const search = signal("");

export const visibleCount = computed(() => banners.value?.length ?? 0);
