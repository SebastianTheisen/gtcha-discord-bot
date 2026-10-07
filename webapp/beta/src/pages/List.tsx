import { signal } from "@preact/signals";
import { useEffect } from "preact/hooks";
import { api } from "../api";
import { BannerCard, BannerSkeleton, CompactRow } from "../components/BannerCard";
import { dateTime } from "../format";
import { canBuy, myRank, wishList } from "../local";
import { watchIds } from "../push";
import { banners, category, listView, loadWishBanners, pref, search, sort, updated, wishBanners } from "../store";
import type { Banner } from "../types";
import { ask } from "../ui";

const CATEGORIES: [string, string][] = [
  ["Alle", "Alle"], ["Bonus", "Bonus"], ["MIX", "MIX"], ["Pokémon", "Pokémon"], ["One piece", "One Piece"],
  ["Dragon Ball", "Dragon Ball"], ["Store", "🏪 Store"], ["Wunsch", "⭐ Wunschkarten"], ["Archiv", "🗄️ Archiv"],
];

const SORTS: Record<string, [string, (a: Banner, b: Banner) => number]> = {
  site: ["Wie auf der Seite", (a, b) => b.id - a.id],
  ev: ["Ø Rückgabe", (a, b) => (b.ev_pct ?? -1) - (a.ev_pct ?? -1)],
  remaining: ["Wenigste Packs", (a, b) => a.remaining - b.remaining],
  price_low: ["Preis ↑", (a, b) => a.price - b.price],
  price_high: ["Preis ↓", (a, b) => b.price - a.price],
};

const QUICK: Record<string, [string, (b: Banner) => boolean]> = {
  ev: ["💰 ≥ 100 %", (b) => b.ev_pct != null && b.ev_pct >= 100],
  hits: ["🎯 Hits drin", (b) => b.status !== "hits_out" && (b.hits_open || 0) > 0],
  watched: ["👀 Beobachtet", (b) => watchIds.value.has(String(b.id))],
  mine: ["✅ Für mich", (b) => canBuy(b) !== false],
};

const filters = pref<string>("filters", "[]");
const archive = signal<{ banners: Banner[]; updated: number } | null>(null);

// Suche: Banner-ID (Teil reicht) oder Titel; über alle Kategorien
const matches = (b: Banner, q: string) => {
  const digits = q.replace(/\D/g, "");
  return (digits && String(b.id).includes(digits)) || `${b.title} ${b.headline || ""}`.toLowerCase().includes(q.toLowerCase());
};

export function List() {
  const q = search.value.trim();
  const cat = category.value;
  const isArchive = cat === "Archiv" && !q;
  const active: string[] = (() => {
    try {
      return (JSON.parse(filters.value) as string[]).filter((k) => QUICK[k] && (k !== "mine" || myRank.value));
    } catch {
      return [];
    }
  })();

  useEffect(() => {
    loadWishBanners();
  }, [wishList.value]);
  useEffect(() => {
    if (isArchive && !archive.value) {
      archive.value = { banners: [], updated: updated.value };
      api<{ banners: Banner[]; updated: number }>("api/archive").then((d) => (archive.value = d)).catch(() => (archive.value = null));
    }
  }, [isArchive]);

  const source = isArchive ? archive.value?.banners || [] : banners.value || [];
  const shown = source
    .filter((b) => (q ? matches(b, q)
      : isArchive || cat === "Alle" ? true
        : cat === "Store" ? b.store || b.category === "Store"
          : cat === "Wunsch" ? wishBanners.value.has(b.id) && canBuy(b) !== false
            : b.category === cat))
    .filter((b) => active.every((k) => QUICK[k][1](b)))
    .sort((SORTS[sort.value] || SORTS.ev)[1]);

  async function toggleQuick(k: string) {
    if (k === "mine" && !myRank.value) {
      const r = await ask("Mitgliedsrang fehlt", "Stell unter „Ich“ deinen Mitgliedsrang ein, dann zeigt der Filter nur Banner, die du kaufen kannst.",
        ["Später", "Einstellen"]);
      if (r === "Einstellen") location.hash = "#/me";
      return;
    }
    const next = active.includes(k) ? active.filter((x) => x !== k) : [...active, k];
    filters.value = JSON.stringify(next);
  }

  // Enter: genau ein Treffer -> öffnen; volle ID, die nicht (mehr) aktiv ist -> trotzdem versuchen
  function submit(e: Event) {
    e.preventDefault();
    (document.activeElement as HTMLElement | null)?.blur();
    const hits = (banners.value || []).filter((b) => matches(b, q));
    const digits = q.replace(/\D/g, "");
    if (hits.length === 1) location.hash = `#/banner/${hits[0].id}`;
    else if (digits.length >= 4) location.hash = `#/banner/${digits}`;
  }

  const empty = q ? "Nicht gefunden – Enter öffnet die ID"
    : cat === "Wunsch" ? (wishList.value.length ? "Gerade keine Wunschkarte kaufbar." : "Noch keine Wunschkarten (auf einer Karte ⭐ merken).")
      : isArchive ? "Noch keine beendeten Banner (sie bleiben hier 30 Tage)"
        : active.length ? "Keine Banner für diese Filter" : "Keine Banner";

  return (
    <div>
      <form class="toolbar" onSubmit={submit} role="search">
        <label class="search">
          <span>🔎</span>
          <input type="search" inputMode="search" autoComplete="off" placeholder="Banner-ID oder Name" value={search.value}
            onInput={(e) => (search.value = (e.target as HTMLInputElement).value)} />
        </label>
        <select class="select" value={sort.value} aria-label="Sortierung"
          onChange={(e) => (sort.value = (e.target as HTMLSelectElement).value)}>
          {Object.entries(SORTS).map(([k, [label]]) => <option value={k}>{label}</option>)}
        </select>
      </form>
      <div class="chips">
        {CATEGORIES.map(([key, label]) => (
          <button class={`chip ${cat === key && !q ? "active" : ""}`} onClick={() => { category.value = key; search.value = ""; }}>{label}</button>
        ))}
      </div>
      <div class="chips quick">
        {Object.entries(QUICK).map(([k, [label]]) => (
          <button class={`chip small-chip ${active.includes(k) ? "on" : ""}`} onClick={() => toggleQuick(k)}>{label}</button>
        ))}
        <button class="chip small-chip" onClick={() => (listView.value = listView.value === "big" ? "compact" : "big")}>
          {listView.value === "big" ? "☰ Kompakt" : "▦ Groß"}
        </button>
      </div>
      <div class="row small muted" style={{ margin: "2px 2px 10px" }}>
        <span>{q ? `${shown.length} Treffer für „${q}“` : `${shown.length} Banner`}</span>
        <span>Stand {dateTime((isArchive ? archive.value?.updated : updated.value) || Date.now() / 1000)}</span>
      </div>
      {banners.value == null && !isArchive
        ? <div class="grid">{[1, 2, 3, 4].map(() => <BannerSkeleton />)}</div>
        : shown.length
          ? listView.value === "compact"
            ? <div class="clist">{shown.map((b, i) => <CompactRow key={b.id} b={b} eager={i < 12} />)}</div>
            : <div class="grid">{shown.map((b, i) => <BannerCard key={b.id} b={b} eager={i < 4} />)}</div>
          : <div class="empty">{empty}</div>}
    </div>
  );
}
