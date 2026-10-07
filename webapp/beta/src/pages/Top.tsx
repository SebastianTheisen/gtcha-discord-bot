import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { BannerCard, BannerSkeleton } from "../components/BannerCard";
import { refreshTick } from "../store";
import type { Banner } from "../types";

// Top 10: ziehbare Banner ohne Bonus, Gratis und Passwort nach Ø Rückgabe (Server-Rangliste wie in der Live-App)
export function Top() {
  const [hot, setHot] = useState<Banner[] | null>(null);
  useEffect(() => {
    api<{ hot: Banner[] }>("api/hot").then((d) => setHot(d.hot)).catch(() => setHot([]));
  }, [refreshTick.value]);
  return (
    <div>
      <div class="section-title" style={{ marginTop: "4px" }}><h2>🔥 Top 10 nach Ø Rückgabe</h2></div>
      <p class="muted small" style={{ marginTop: "-4px" }}>Ziehbare Banner ohne Bonus, Gratis und Passwort · Ø = erwartete Rückgabe pro Zug</p>
      {hot == null
        ? <div class="grid">{[1, 2, 3].map(() => <BannerSkeleton />)}</div>
        : hot.length ? <div class="grid">{hot.map((b, i) => <BannerCard key={b.id} b={b} rank={i + 1} eager={i < 4} />)}</div>
          : <div class="empty">Gerade kein ziehbarer Banner</div>}
    </div>
  );
}
