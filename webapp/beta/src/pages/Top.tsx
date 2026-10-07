import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { BannerCard, BannerSkeleton } from "../components/BannerCard";
import { updated } from "../store";
import type { Banner } from "../types";

// Top 10: die lohnendsten Banner gerade (Server-Rangliste wie in der Live-App)
export function Top() {
  const [hot, setHot] = useState<Banner[] | null>(null);
  useEffect(() => {
    api<{ hot: Banner[] }>("api/hot").then((d) => setHot(d.hot)).catch(() => setHot([]));
  }, [updated.value]);
  return (
    <div>
      <div class="section-title" style={{ marginTop: "4px" }}><h2>🔥 Top 10 gerade</h2>
        <span class="muted small">nach Ø Rückgabe, nur kaufbare</span></div>
      {hot == null
        ? <div class="grid">{[1, 2, 3].map(() => <BannerSkeleton />)}</div>
        : hot.length ? <div class="grid">{hot.map((b) => <BannerCard key={b.id} b={b} />)}</div>
          : <div class="empty">Gerade nichts in der Rangliste</div>}
    </div>
  );
}
