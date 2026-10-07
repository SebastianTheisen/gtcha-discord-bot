// Neue Version auf dem Server? Dann oben ein Hinweis - antippen lädt sie (sonst gälte sie erst beim übernächsten Öffnen)
import { signal } from "@preact/signals";

declare const __BUILD_ID__: string;
const updateReady = signal(false);

export async function checkVersion() {
  try {
    const res = await fetch("version.json", { cache: "no-store" });
    if (!res.ok) return;
    const { build } = await res.json();
    if (build && build !== __BUILD_ID__) updateReady.value = true;
  } catch {
    /* offline: später erneut */
  }
}

async function applyUpdate() {
  try {
    // gespeicherte App-Seite verwerfen, damit sie frisch vom Server kommt
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith("gtcha-beta")).map((k) => caches.delete(k)));
    const reg = await navigator.serviceWorker?.getRegistration();
    await reg?.update();
  } catch {
    /* egal - neu laden reicht meist */
  }
  location.reload();
}

export function UpdateBar() {
  if (!updateReady.value) return null;
  return (
    <button class="update-bar" onClick={applyUpdate}>
      <span>✨ Neue Version verfügbar</span><span>Tippen zum Laden ›</span>
    </button>
  );
}

setInterval(() => { if (!document.hidden) checkVersion(); }, 10 * 60 * 1000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) checkVersion(); });
