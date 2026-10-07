// Push-Benachrichtigungen, Beobachten, Wunschliste auf dem Server und Glocke (Verlauf der Pushes)
import { signal } from "@preact/signals";
import { api, getToken } from "./api";
import { haptic } from "./ui";
import { save, wishList, isWish, type Wish } from "./local";

export const EVENT_LABELS: Record<string, [string, string]> = {
  new: ["🆕 Neuer Banner", ""],
  value: ["💰 Lohnt sich", "über 100 %"],
  hit: ["🎯 Hit raus", ""],
  packs: ["📉 Pack-Bewegung", "gesammelt, max. 1× pro Minute"],
  ship: ["📦 Versand", ""],
  low: ["⚡ Endspurt", ""],
  end: ["🏁 Beendet", ""],
};
// Vorgaben wie auf dem Server (DEFAULTS in webapp/push.py)
export const EVENT_DEFAULTS: Record<string, boolean> = { new: true, value: true, hit: true, packs: false, ship: false, low: true, end: false };
export const WATCH_LABELS: Record<string, string> = {
  hit: "🎯 Hit raus", packs: "📉 Packs weniger", ship: "📦 Versand", ev: "💰 Über 100 %", low: "⚡ Endspurt", end: "🏁 Beendet",
};
// Vorauswahl beim Beobachten: alles außer "Packs weniger" (das kann sehr oft kommen)
export const WATCH_DEFAULT = ["hit", "ship", "ev", "low", "end"];

export type Prefs = Record<string, boolean | string[] | Record<string, string[]>> & { watch: Record<string, string[]>; wish?: string[] };

export const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

export async function pushState(): Promise<{ supported: boolean; sub: PushSubscription | null; prefs: Prefs }> {
  const supported = pushSupported();
  const sub = supported ? await currentSubscription().catch(() => null) : null;
  const prefs = (sub ? (await api<{ prefs: Prefs }>("api/push/prefs", jsonPost({ endpoint: sub.endpoint }))).prefs : {}) as Prefs;
  prefs.watch = prefs.watch || {};
  watchIds.value = new Set(Object.keys(prefs.watch));
  return { supported, sub, prefs };
}

const jsonPost = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// Mit Geräteschlüssel: der Server weiß dann, zu welcher Discord-Verknüpfung das Push-Abo gehört
export async function savePrefs(sub: PushSubscription, prefs: Prefs) {
  await api("api/push/subscribe", { ...jsonPost({ subscription: sub.toJSON(), prefs }), headers: { "Content-Type": "application/json", "X-Device-Token": getToken() } });
  watchIds.value = new Set(Object.keys(prefs.watch || {}));
}

function b64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export async function enablePush(prefs: Prefs): Promise<string | null> {
  if ((await Notification.requestPermission()) !== "granted") return "Benachrichtigungen wurden nicht erlaubt.";
  const { key } = await api<{ key: string }>("api/push/key");
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
  await savePrefs(sub, prefs);
  return null;
}

export async function disablePush(sub: PushSubscription) {
  await api("api/push/unsubscribe", jsonPost({ endpoint: sub.endpoint }));
  await sub.unsubscribe();
  watchIds.value = new Set();
}

export const testPush = (sub: PushSubscription) => api("api/push/test", jsonPost({ endpoint: sub.endpoint }));

// Beobachtete Banner (für Filter und Knopf auf der Banner-Seite)
export const watchIds = signal<Set<string>>(new Set());

export async function toggleWatch(id: string): Promise<boolean | null> {
  const { sub, prefs } = await pushState();
  if (!sub) return null;
  if (prefs.watch[id]) delete prefs.watch[id];
  else prefs.watch[id] = [...WATCH_DEFAULT];
  haptic();
  await savePrefs(sub, prefs);
  return !!prefs.watch[id];
}

// Wunschliste liegt auf dem Gerät; mit eingeschalteten Pushes zusätzlich beim Server (für die Wunschkarten-Pushes)
export async function toggleWish(card: Wish) {
  const list = wishList.value;
  const next = isWish(card.id) ? list.filter((w) => w.id !== card.id) : [...list, { id: card.id, name: card.name, image: card.image }];
  wishList.value = next.slice(-100);
  save("wish", JSON.stringify(wishList.value));
  haptic();
  try {
    const { sub, prefs } = await pushState();
    if (sub) await savePrefs(sub, { ...prefs, wish: wishList.value.map((w) => w.id) });
  } catch {
    /* ohne Pushes bleibt die Liste nur auf dem Gerät */
  }
}

// --- Glocke: Verlauf der Pushes dieses Geräts ---
export const unread = signal(0);

export interface InboxItem { id: number; t: string; title: string; body?: string; banner_id?: number; read?: boolean }

export async function inboxApi<T>(path: string, body: Record<string, unknown> = {}): Promise<T | null> {
  const sub = await currentSubscription().catch(() => null);
  if (!sub) return null;
  return api<T>(path, { ...jsonPost({ endpoint: sub.endpoint, ...body }), headers: { "Content-Type": "application/json", "X-Device-Token": getToken() } });
}

export function setBell(n: number) {
  unread.value = n;
  const nav = navigator as Navigator & { setAppBadge?: (n: number) => Promise<void>; clearAppBadge?: () => Promise<void> };
  try {
    if (n) nav.setAppBadge?.(n);
    else nav.clearAppBadge?.();
  } catch {
    /* nicht unterstützt */
  }
}

export async function updateBell() {
  const res = await inboxApi<{ unread: number }>("api/push/inbox", { limit: 1 }).catch(() => null);
  setBell(res ? res.unread : 0);
}

export async function markRead(ids: number[] | null) {
  await inboxApi("api/push/read", ids ? { ids } : { all: true }).catch(() => null);
  await updateBell();
}
