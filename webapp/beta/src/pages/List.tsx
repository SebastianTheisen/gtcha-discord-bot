import { BannerCard, BannerSkeleton } from "../components/BannerCard";
import { banners, category, search, sort } from "../store";
import type { Banner } from "../types";

const CATEGORIES: [string, string][] = [
  ["Alle", "Alle"], ["Bonus", "Bonus"], ["MIX", "MIX"], ["Pokémon", "Pokémon"],
  ["One piece", "One Piece"], ["Dragon Ball", "Dragon Ball"], ["Store", "🏪 Store"], ["Lohnt", "💰 ≥ 100 %"],
];

const SORTS: Record<string, [string, (a: Banner, b: Banner) => number]> = {
  ev: ["Ø Rückgabe", (a, b) => (b.ev_pct ?? -1) - (a.ev_pct ?? -1)],
  new: ["Neueste", (a, b) => b.id - a.id],
  remaining: ["Wenigste Packs", (a, b) => a.remaining - b.remaining],
  price_low: ["Preis ↑", (a, b) => a.price - b.price],
  price_high: ["Preis ↓", (a, b) => b.price - a.price],
};

const matches = (b: Banner, q: string) =>
  `${b.id} ${b.title} ${b.headline || ""} ${b.category || ""}`.toLowerCase().includes(q.toLowerCase());

export function List() {
  const all = banners.value;
  const cat = category.value;
  const q = search.value.trim();
  const list = (all || [])
    .filter((b) => (q ? matches(b, q)
      : cat === "Alle" ? true
        : cat === "Lohnt" ? (b.ev_pct ?? 0) >= 100 && b.status !== "upcoming"
          : cat === "Store" ? b.store || b.category === "Store"
            : b.category === cat))
    .sort(SORTS[sort.value]?.[1] || SORTS.ev[1]);
  const good = (all || []).filter((b) => (b.ev_pct ?? 0) >= 100 && b.status !== "upcoming").length;

  return (
    <div>
      <div class="toolbar">
        <label class="search">
          <span>🔎</span>
          <input type="search" placeholder="Banner, Karte oder Nummer" value={search.value}
            onInput={(e) => (search.value = (e.target as HTMLInputElement).value)} />
        </label>
        <select class="select" value={sort.value} onChange={(e) => (sort.value = (e.target as HTMLSelectElement).value)}>
          {Object.entries(SORTS).map(([k, [label]]) => <option value={k}>{label}</option>)}
        </select>
      </div>
      <div class="chips">
        {CATEGORIES.map(([key, label]) => (
          <button class={`chip ${cat === key && !q ? "active" : ""}`} onClick={() => { category.value = key; search.value = ""; }}>
            {label}{key === "Lohnt" && good ? ` · ${good}` : ""}
          </button>
        ))}
      </div>
      {all == null
        ? <div class="grid">{[1, 2, 3, 4].map(() => <BannerSkeleton />)}</div>
        : list.length
          ? <div class="grid">{list.map((b) => <BannerCard key={b.id} b={b} />)}</div>
          : <div class="empty">Keine Banner gefunden</div>}
    </div>
  );
}
